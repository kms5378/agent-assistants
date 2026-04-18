from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
import logging
from typing import Optional

import httpx
from fastapi import FastAPI, Header, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.channels.telegram import TelegramAdapter
from app.core.security import TokenCipher
from app.core.settings import Settings, get_settings
from app.core.time import ensure_aware, utcnow
from app.db import build_session_factory, init_db
from app.models import OAuthState
from app.services.conversation import ConversationService
from app.services.google_calendar import GoogleOAuthService, InvalidConnectToken
from app.services.openai_responses import OpenAIResponsesClient
from app.services.reminders import ReminderService
from app.services.tool_router import ToolRouter

logger = logging.getLogger(__name__)


@dataclass
class AppContainer:
    settings: Settings
    session_factory: sessionmaker[Session]
    model_client: object
    telegram_adapter: TelegramAdapter
    google_service: GoogleOAuthService

    @classmethod
    def build(cls, settings: Optional[Settings] = None) -> "AppContainer":
        resolved = settings or get_settings()
        session_factory = build_session_factory(resolved)
        cipher = TokenCipher(resolved.encryption_key)
        return cls(
            settings=resolved,
            session_factory=session_factory,
            model_client=OpenAIResponsesClient(
                api_key=resolved.openai_api_key,
                model=resolved.openai_model,
            ),
            telegram_adapter=TelegramAdapter(
                bot_token=resolved.telegram_bot_token or "",
                secret_token=resolved.telegram_webhook_secret,
            ),
            google_service=GoogleOAuthService(resolved, cipher),
        )

    def build_conversation_service(self, session: Session) -> ConversationService:
        reminder_service = ReminderService(session=session, default_timezone=self.settings.default_timezone)
        tool_router = ToolRouter(
            reminder_service=reminder_service,
            google_service=self.google_service,
        )
        return ConversationService(
            session=session,
            settings=self.settings,
            model_client=self.model_client,
            tool_router=tool_router,
        )


def create_app(container: Optional[AppContainer] = None) -> FastAPI:
    resolved_container = container or AppContainer.build()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.container = resolved_container
        init_db(resolved_container.session_factory)
        yield

    app = FastAPI(title=resolved_container.settings.app_name, lifespan=lifespan)
    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/webhooks/telegram/{webhook_key}")
    async def telegram_webhook(
        webhook_key: str,
        request: Request,
        x_telegram_bot_api_secret_token: Optional[str] = Header(default=None),
    ) -> JSONResponse:
        if webhook_key != resolved_container.settings.telegram_webhook_key:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Webhook not found.")
        if x_telegram_bot_api_secret_token != resolved_container.settings.telegram_webhook_secret:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid webhook token.")

        payload = await request.json()
        event = resolved_container.telegram_adapter.parse_update(payload)
        if event is None:
            return JSONResponse({"ok": True, "ignored": True})

        session = resolved_container.session_factory()
        try:
            try:
                service = resolved_container.build_conversation_service(session)
                outbound_messages = service.handle_event(event)
            except Exception as exc:
                logger.exception("Conversation processing failed for telegram update %s", event.update_id)
                return JSONResponse(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    content={
                        "ok": False,
                        "stage": "conversation",
                        "detail": "Conversation processing failed.",
                        "error_type": exc.__class__.__name__,
                    },
                )

            try:
                for outbound in outbound_messages:
                    resolved_container.telegram_adapter.send_message(outbound)
            except httpx.HTTPStatusError as exc:
                logger.warning(
                    "Telegram delivery failed for update %s with status %s",
                    event.update_id,
                    exc.response.status_code,
                )
                return JSONResponse(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    content={
                        "ok": False,
                        "stage": "delivery",
                        "detail": "Telegram delivery failed.",
                        "error_type": exc.__class__.__name__,
                        "upstream_status": exc.response.status_code,
                        "upstream_body": (exc.response.text or "")[:200],
                    },
                )
            except httpx.HTTPError as exc:
                logger.warning("Telegram delivery network error for update %s", event.update_id)
                return JSONResponse(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    content={
                        "ok": False,
                        "stage": "delivery",
                        "detail": "Telegram delivery failed.",
                        "error_type": exc.__class__.__name__,
                    },
                )
            except Exception as exc:
                logger.exception("Telegram delivery crashed for update %s", event.update_id)
                return JSONResponse(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    content={
                        "ok": False,
                        "stage": "delivery",
                        "detail": "Telegram delivery failed.",
                        "error_type": exc.__class__.__name__,
                    },
                )

            return JSONResponse({"ok": True, "messages_sent": len(outbound_messages)})
        finally:
            session.close()

    @app.get("/auth/google/start")
    def google_auth_start(
        connect_token: str = Query(...),
    ) -> RedirectResponse:
        session = resolved_container.session_factory()
        try:
            try:
                connect_record = resolved_container.google_service.consume_connect_token(session, connect_token)
            except InvalidConnectToken as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Connect token is invalid, expired, or already used.",
                ) from exc
            state = resolved_container.google_service.create_state_token()
            session.add(
                OAuthState(
                    state=state,
                    user_id=connect_record.user_id,
                    platform=connect_record.platform,
                    expires_at=utcnow() + timedelta(minutes=10),
                )
            )
            auth_url = resolved_container.google_service.build_authorization_url(state=state)
            session.commit()
            return RedirectResponse(auth_url)
        finally:
            session.close()

    @app.get("/auth/google/callback")
    def google_auth_callback(state: str = Query(...), code: str = Query(...)) -> HTMLResponse:
        session = resolved_container.session_factory()
        try:
            record = session.scalar(select(OAuthState).where(OAuthState.state == state))
            if record is None or ensure_aware(record.expires_at) < utcnow():
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="OAuth state is invalid or expired.")
            try:
                bundle = resolved_container.google_service.exchange_code(code)
            except httpx.HTTPError as exc:
                raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Google token exchange failed.") from exc
            resolved_container.google_service.save_tokens(session, user_id=record.user_id, bundle=bundle)
            session.delete(record)
            session.commit()
            return HTMLResponse(
                "<html><body><h1>Google Calendar connected</h1><p>You can return to Telegram.</p></body></html>"
            )
        finally:
            session.close()

    return app


app = create_app()
