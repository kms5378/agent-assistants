from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import timedelta, timezone as dt_timezone
from typing import Optional
from urllib.parse import urlencode

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import TokenCipher
from app.core.settings import Settings
from app.core.time import parse_local_datetime, utcnow
from app.models import OAuthAccount


class OAuthConnectionRequired(RuntimeError):
    """Raised when a user has not connected Google OAuth."""


@dataclass
class GoogleTokenBundle:
    access_token: str
    refresh_token: Optional[str]
    expires_in: Optional[int]
    scope: Optional[str]
    token_type: Optional[str]
    email: Optional[str]


class GoogleOAuthService:
    auth_url = "https://accounts.google.com/o/oauth2/v2/auth"
    token_url = "https://oauth2.googleapis.com/token"
    userinfo_url = "https://openidconnect.googleapis.com/v1/userinfo"
    calendar_base = "https://www.googleapis.com/calendar/v3"

    def __init__(self, settings: Settings, cipher: TokenCipher) -> None:
        self.settings = settings
        self.cipher = cipher

    def build_authorization_url(self, *, state: str) -> str:
        params = {
            "client_id": self.settings.google_client_id,
            "redirect_uri": self.settings.google_redirect_uri,
            "response_type": "code",
            "scope": self.settings.google_oauth_scopes,
            "access_type": "offline",
            "include_granted_scopes": "true",
            "prompt": "consent",
            "state": state,
        }
        return f"{self.auth_url}?{urlencode(params)}"

    def create_state_token(self) -> str:
        return secrets.token_urlsafe(24)

    def exchange_code(self, code: str) -> GoogleTokenBundle:
        payload = {
            "code": code,
            "client_id": self.settings.google_client_id,
            "client_secret": self.settings.google_client_secret,
            "redirect_uri": self.settings.google_redirect_uri,
            "grant_type": "authorization_code",
        }
        with httpx.Client(timeout=20) as client:
            token_response = client.post(self.token_url, data=payload)
            token_response.raise_for_status()
            token_payload = token_response.json()

            email = None
            access_token = token_payload["access_token"]
            userinfo_response = client.get(
                self.userinfo_url,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if userinfo_response.is_success:
                email = userinfo_response.json().get("email")

        return GoogleTokenBundle(
            access_token=access_token,
            refresh_token=token_payload.get("refresh_token"),
            expires_in=token_payload.get("expires_in"),
            scope=token_payload.get("scope"),
            token_type=token_payload.get("token_type"),
            email=email,
        )

    def save_tokens(self, session: Session, *, user_id: str, bundle: GoogleTokenBundle) -> OAuthAccount:
        account = session.scalar(
            select(OAuthAccount).where(
                OAuthAccount.user_id == user_id,
                OAuthAccount.provider == "google",
            )
        )
        if account is None:
            account = OAuthAccount(user_id=user_id, provider="google")
            session.add(account)
        account.email = bundle.email
        account.scope = bundle.scope
        account.token_type = bundle.token_type
        account.access_token_encrypted = self.cipher.encrypt(bundle.access_token)
        if bundle.refresh_token:
            account.refresh_token_encrypted = self.cipher.encrypt(bundle.refresh_token)
        if bundle.expires_in:
            account.expires_at = utcnow() + timedelta(seconds=bundle.expires_in)
        session.flush()
        return account

    def get_connect_url(self, user_id: str, platform: str = "telegram") -> str:
        return f"{self.settings.app_base_url}/auth/google/start?user_id={user_id}&platform={platform}"

    def _ensure_valid_access_token(self, session: Session, user_id: str) -> str:
        account = session.scalar(
            select(OAuthAccount).where(
                OAuthAccount.user_id == user_id,
                OAuthAccount.provider == "google",
            )
        )
        if account is None:
            raise OAuthConnectionRequired("Google account is not connected.")

        expires_at = account.expires_at
        token = self.cipher.decrypt(account.access_token_encrypted)
        if token and (expires_at is None or expires_at > utcnow() + timedelta(seconds=60)):
            return token

        refresh_token = self.cipher.decrypt(account.refresh_token_encrypted)
        if not refresh_token:
            raise OAuthConnectionRequired("Google account needs to be reconnected.")

        payload = {
            "client_id": self.settings.google_client_id,
            "client_secret": self.settings.google_client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }
        token_response = httpx.post(self.token_url, data=payload, timeout=20)
        token_response.raise_for_status()
        token_payload = token_response.json()
        account.access_token_encrypted = self.cipher.encrypt(token_payload["access_token"])
        if token_payload.get("expires_in"):
            account.expires_at = utcnow() + timedelta(seconds=token_payload["expires_in"])
        session.flush()
        return token_payload["access_token"]

    def list_events(
        self,
        session: Session,
        *,
        user_id: str,
        timezone: str,
        start_local: str,
        end_local: str,
        calendar_id: str = "primary",
    ) -> dict:
        token = self._ensure_valid_access_token(session, user_id)
        params = {
            "timeMin": parse_local_datetime(start_local, timezone).astimezone(dt_timezone.utc).isoformat(),
            "timeMax": parse_local_datetime(end_local, timezone).astimezone(dt_timezone.utc).isoformat(),
            "singleEvents": "true",
            "orderBy": "startTime",
        }
        response = httpx.get(
            f"{self.calendar_base}/calendars/{calendar_id}/events",
            params=params,
            headers={"Authorization": f"Bearer {token}"},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        items = payload.get("items", [])
        return {
            "status": "ok",
            "items": [
                {
                    "id": item.get("id"),
                    "title": item.get("summary"),
                    "start": (item.get("start") or {}).get("dateTime") or (item.get("start") or {}).get("date"),
                    "end": (item.get("end") or {}).get("dateTime") or (item.get("end") or {}).get("date"),
                }
                for item in items
            ],
        }

    def create_event(
        self,
        session: Session,
        *,
        user_id: str,
        timezone: str,
        title: str,
        start_local: str,
        end_local: str,
        description: Optional[str] = None,
        location: Optional[str] = None,
        calendar_id: str = "primary",
    ) -> dict:
        token = self._ensure_valid_access_token(session, user_id)
        payload = {
            "summary": title,
            "description": description,
            "location": location,
            "start": {"dateTime": parse_local_datetime(start_local, timezone).isoformat(), "timeZone": timezone},
            "end": {"dateTime": parse_local_datetime(end_local, timezone).isoformat(), "timeZone": timezone},
        }
        response = httpx.post(
            f"{self.calendar_base}/calendars/{calendar_id}/events",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
            timeout=20,
        )
        response.raise_for_status()
        item = response.json()
        return {
            "status": "ok",
            "item": {
                "id": item.get("id"),
                "title": item.get("summary"),
                "html_link": item.get("htmlLink"),
            },
        }

    def update_event(
        self,
        session: Session,
        *,
        user_id: str,
        timezone: str,
        event_id: str,
        title: Optional[str] = None,
        start_local: Optional[str] = None,
        end_local: Optional[str] = None,
        description: Optional[str] = None,
        location: Optional[str] = None,
        calendar_id: str = "primary",
    ) -> dict:
        token = self._ensure_valid_access_token(session, user_id)
        patch: dict[str, object] = {}
        if title is not None:
            patch["summary"] = title
        if description is not None:
            patch["description"] = description
        if location is not None:
            patch["location"] = location
        if start_local is not None:
            patch["start"] = {"dateTime": parse_local_datetime(start_local, timezone).isoformat(), "timeZone": timezone}
        if end_local is not None:
            patch["end"] = {"dateTime": parse_local_datetime(end_local, timezone).isoformat(), "timeZone": timezone}

        response = httpx.patch(
            f"{self.calendar_base}/calendars/{calendar_id}/events/{event_id}",
            json=patch,
            headers={"Authorization": f"Bearer {token}"},
            timeout=20,
        )
        response.raise_for_status()
        item = response.json()
        return {
            "status": "ok",
            "item": {
                "id": item.get("id"),
                "title": item.get("summary"),
                "html_link": item.get("htmlLink"),
            },
        }
