from __future__ import annotations

from datetime import timedelta
from typing import Optional
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.main import AppContainer, create_app
from app.channels.telegram import TelegramAdapter
from app.contracts import InboundEvent, ModelToolCall, ModelTurnResponse
from app.core.security import TokenCipher
from app.core.settings import Settings
from app.core.time import ensure_aware, utcnow
from app.db import (
    SCHEMA_BASELINE_DESCRIPTION,
    SCHEMA_BASELINE_VERSION,
    build_engine,
    build_session_factory,
    init_db,
)
from app.models import OAuthAccount, OAuthConnectToken, OAuthState, Reminder, SchemaMigration, User
from app.services.conversation import ConversationService
from app.services.google_calendar import GoogleOAuthService, GoogleTokenBundle
from app.services.reminders import ReminderService
from app.worker import run_worker_loop, run_worker_once


class FakeModel:
    def __init__(self, initial: ModelTurnResponse, follow_up: Optional[ModelTurnResponse] = None) -> None:
        self.initial = initial
        self.follow_up = follow_up
        self.tool_outputs: list[dict] = []

    def create_turn(self, *, messages, tools):
        self.messages = messages
        self.tools = tools
        return self.initial

    def submit_tool_outputs(self, *, previous_response_id, tool_outputs, tools):
        self.previous_response_id = previous_response_id
        self.tool_outputs = tool_outputs
        assert self.follow_up is not None
        return self.follow_up


class RecordingTelegramAdapter(TelegramAdapter):
    def __init__(self) -> None:
        super().__init__(bot_token="token", secret_token="secret")
        self.sent = []

    def send_message(self, message):
        self.sent.append(message)


class SequenceTelegramAdapter(RecordingTelegramAdapter):
    def __init__(self, outcomes: list[bool]) -> None:
        super().__init__()
        self.outcomes = outcomes
        self.calls = 0

    def send_message(self, message):
        self.calls += 1
        should_succeed = self.outcomes.pop(0) if self.outcomes else True
        if not should_succeed:
            raise RuntimeError(f"delivery failed #{self.calls}")
        super().send_message(message)


def make_container(tmp_path, model_client=None, persona_profile_path: Optional[str] = None):
    settings_kwargs = dict(
        app_base_url="https://assistant.example.com",
        database_url=f"sqlite:///{tmp_path / 'assistant.db'}",
        telegram_webhook_secret="secret",
        telegram_webhook_key="hook-key",
        google_client_id="test-google-client",
        google_client_secret="test-google-secret",
        google_redirect_uri="https://assistant.example.com/auth/google/callback",
        default_timezone="Asia/Seoul",
        encryption_key="test-key",
    )
    if persona_profile_path is not None:
        settings_kwargs["persona_profile_path"] = persona_profile_path
    settings = Settings(**settings_kwargs)
    session_factory = build_session_factory(settings)
    init_db(session_factory)
    return AppContainer(
        settings=settings,
        session_factory=session_factory,
        model_client=model_client or FakeModel(ModelTurnResponse(response_id="r1", text="안녕하세요")),
        telegram_adapter=RecordingTelegramAdapter(),
        google_service=GoogleOAuthService(settings, TokenCipher(settings.encryption_key)),
    )


def create_user(container: AppContainer) -> str:
    session = container.session_factory()
    try:
        user = User(timezone=container.settings.default_timezone)
        session.add(user)
        session.commit()
        return user.id
    finally:
        session.close()


def extract_connect_token(connect_url: str) -> str:
    return parse_qs(urlparse(connect_url).query)["connect_token"][0]


def test_reminder_create_and_delete_ambiguity(tmp_path):
    container = make_container(tmp_path)
    session = container.session_factory()
    service = ReminderService(session, container.settings.default_timezone)

    future_one = (utcnow() + timedelta(days=1)).astimezone().isoformat()
    future_two = (utcnow() + timedelta(days=2)).astimezone().isoformat()

    first = service.create_reminder(
        user_id="user-1",
        source_platform="telegram",
        source_chat_id="chat-1",
        created_from_message_id=None,
        title="약 먹기",
        due_at_local=future_one,
        timezone="Asia/Seoul",
        recurrence={"type": "none"},
        notes=None,
    )
    second = service.create_reminder(
        user_id="user-1",
        source_platform="telegram",
        source_chat_id="chat-1",
        created_from_message_id=None,
        title="약 사기",
        due_at_local=future_two,
        timezone="Asia/Seoul",
        recurrence={"type": "none"},
        notes=None,
    )
    session.commit()

    assert first["status"] == "ok"
    assert second["status"] == "ok"

    ambiguous = service.delete_reminder(user_id="user-1", query="약")
    assert ambiguous["status"] == "ambiguity"
    assert len(ambiguous["candidates"]) == 2


def test_init_db_records_schema_baseline_once(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'baseline.db'}")
    session_factory = build_session_factory(settings)

    init_db(session_factory)
    init_db(session_factory)

    session = session_factory()
    try:
        baseline = session.get(SchemaMigration, SCHEMA_BASELINE_VERSION)
        count = session.query(SchemaMigration).count()
    finally:
        session.close()

    assert baseline is not None
    assert baseline.description == SCHEMA_BASELINE_DESCRIPTION
    assert count == 1


def test_build_engine_enables_pool_pre_ping_for_postgres():
    settings = Settings(database_url="postgresql+psycopg://assistant:assistant@postgres:5432/assistant")

    with patch("app.db.create_engine") as create_engine:
        build_engine(settings)

    create_engine.assert_called_once()
    args, kwargs = create_engine.call_args
    assert args[0] == settings.database_url
    assert kwargs["connect_args"] == {}
    assert kwargs["pool_pre_ping"] is True


def test_run_worker_loop_initializes_db_before_polling(tmp_path):
    container = make_container(tmp_path)

    with patch("app.worker.init_db") as init_db_mock, patch("app.worker.run_worker_once") as run_once_mock, patch(
        "app.worker.time.sleep",
        side_effect=RuntimeError("stop-loop"),
    ):
        try:
            run_worker_loop(container)
        except RuntimeError as exc:
            assert str(exc) == "stop-loop"

    init_db_mock.assert_called_once_with(container.session_factory)
    run_once_mock.assert_called_once_with(container)


def test_conversation_service_creates_reminder_via_tool_call(tmp_path):
    model = FakeModel(
        initial=ModelTurnResponse(
            response_id="resp-1",
            text="",
            tool_calls=[
                ModelToolCall(
                    call_id="call-1",
                    name="reminder_create",
                    arguments={
                        "title": "물 마시기",
                        "due_at_local": (utcnow() + timedelta(days=1)).astimezone().isoformat(),
                        "timezone": "Asia/Seoul",
                        "recurrence": {"type": "none"},
                        "delivery_channel": "telegram",
                        "notes": "",
                    },
                )
            ],
        ),
        follow_up=ModelTurnResponse(response_id="resp-2", text="내일 오전 알림으로 등록했어요."),
    )
    container = make_container(tmp_path, model_client=model)
    session = container.session_factory()
    service = container.build_conversation_service(session)

    outbound = service.handle_event(
        InboundEvent(
            platform="telegram",
            external_user_id="tg-user",
            chat_id="chat-1",
            conversation_id="chat-1",
            message_id="msg-1",
            update_id="update-1",
            text="내일 오전에 물 마시라고 알려줘",
        )
    )

    reminders = list(session.query(Reminder).all())
    assert len(reminders) == 1
    assert outbound[0].text == "내일 오전 알림으로 등록했어요."
    assert "status" in model.tool_outputs[0]["output"]


def test_webhook_is_idempotent(tmp_path):
    container = make_container(tmp_path)
    app = create_app(container)
    client = TestClient(app)
    payload = {
        "update_id": 100,
        "message": {
            "message_id": 10,
            "text": "안녕",
            "chat": {"id": 777},
            "from": {"id": 999, "first_name": "Tester"},
        },
    }

    first = client.post(
        "/webhooks/telegram/hook-key",
        json=payload,
        headers={"X-Telegram-Bot-Api-Secret-Token": "secret"},
    )
    second = client.post(
        "/webhooks/telegram/hook-key",
        json=payload,
        headers={"X-Telegram-Bot-Api-Secret-Token": "secret"},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert len(container.telegram_adapter.sent) == 1
    assert second.json()["messages_sent"] == 0


def test_google_oauth_start_consumes_signed_one_time_connect_token(tmp_path):
    container = make_container(tmp_path)
    app = create_app(container)
    client = TestClient(app)
    user_id = create_user(container)

    session = container.session_factory()
    try:
        connect_url = container.google_service.issue_connect_url(session, user_id=user_id, platform="telegram")
        token = extract_connect_token(connect_url)
        session.commit()
    finally:
        session.close()

    response = client.get("/auth/google/start", params={"connect_token": token}, follow_redirects=False)

    assert response.status_code == 307
    redirect_query = parse_qs(urlparse(response.headers["location"]).query)
    assert redirect_query["client_id"] == ["test-google-client"]
    assert "state" in redirect_query

    session = container.session_factory()
    try:
        token_record = session.get(OAuthConnectToken, token)
        oauth_state = session.scalar(select(OAuthState).where(OAuthState.state == redirect_query["state"][0]))
        assert token_record is not None
        assert token_record.used_at is not None
        assert oauth_state is not None
        assert oauth_state.user_id == user_id
        assert oauth_state.platform == "telegram"
    finally:
        session.close()


def test_google_oauth_start_rejects_reused_connect_token(tmp_path):
    container = make_container(tmp_path)
    app = create_app(container)
    client = TestClient(app)
    user_id = create_user(container)

    session = container.session_factory()
    try:
        connect_url = container.google_service.issue_connect_url(session, user_id=user_id, platform="telegram")
        token = extract_connect_token(connect_url)
        session.commit()
    finally:
        session.close()

    first = client.get("/auth/google/start", params={"connect_token": token}, follow_redirects=False)
    second = client.get("/auth/google/start", params={"connect_token": token}, follow_redirects=False)

    assert first.status_code == 307
    assert second.status_code == 400


def test_google_oauth_start_rejects_expired_connect_token(tmp_path):
    container = make_container(tmp_path)
    app = create_app(container)
    client = TestClient(app)
    user_id = create_user(container)

    session = container.session_factory()
    try:
        connect_url = container.google_service.issue_connect_url(
            session,
            user_id=user_id,
            platform="telegram",
            expires_in=timedelta(minutes=-1),
        )
        token = extract_connect_token(connect_url)
        session.commit()
    finally:
        session.close()

    response = client.get("/auth/google/start", params={"connect_token": token}, follow_redirects=False)

    assert response.status_code == 400


def test_google_oauth_start_rejects_tampered_connect_token(tmp_path):
    container = make_container(tmp_path)
    app = create_app(container)
    client = TestClient(app)
    user_id = create_user(container)

    session = container.session_factory()
    try:
        connect_url = container.google_service.issue_connect_url(session, user_id=user_id, platform="telegram")
        token = extract_connect_token(connect_url)
        session.commit()
    finally:
        session.close()

    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
    response = client.get("/auth/google/start", params={"connect_token": tampered}, follow_redirects=False)

    assert response.status_code == 400


def test_google_oauth_callback_saves_tokens_after_valid_start(tmp_path):
    container = make_container(tmp_path)
    app = create_app(container)
    client = TestClient(app)
    user_id = create_user(container)

    session = container.session_factory()
    try:
        connect_url = container.google_service.issue_connect_url(session, user_id=user_id, platform="telegram")
        token = extract_connect_token(connect_url)
        session.commit()
    finally:
        session.close()

    start = client.get("/auth/google/start", params={"connect_token": token}, follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]

    def fake_exchange_code(code: str) -> GoogleTokenBundle:
        assert code == "auth-code"
        return GoogleTokenBundle(
            access_token="access-token",
            refresh_token="refresh-token",
            expires_in=3600,
            scope="openid email profile",
            token_type="Bearer",
            email="tester@example.com",
        )

    container.google_service.exchange_code = fake_exchange_code

    callback = client.get("/auth/google/callback", params={"state": state, "code": "auth-code"})

    assert callback.status_code == 200
    assert "Google Calendar connected" in callback.text

    session = container.session_factory()
    try:
        account = session.scalar(
            select(OAuthAccount).where(
                OAuthAccount.user_id == user_id,
                OAuthAccount.provider == "google",
            )
        )
        state_record = session.scalar(select(OAuthState).where(OAuthState.state == state))
        assert account is not None
        assert account.email == "tester@example.com"
        assert account.refresh_token_encrypted is not None
        assert state_record is None
    finally:
        session.close()


def test_service_is_platform_agnostic_for_future_discord(tmp_path):
    container = make_container(tmp_path, model_client=FakeModel(ModelTurnResponse(response_id="r1", text="discord reply")))
    session = container.session_factory()
    service = container.build_conversation_service(session)

    outbound = service.handle_event(
        InboundEvent(
            platform="discord",
            external_user_id="discord-user",
            chat_id="channel-1",
            conversation_id="thread-1",
            message_id="msg-1",
            update_id="evt-1",
            text="회의 일정 알려줘",
        )
    )

    assert outbound[0].platform == "discord"
    assert outbound[0].chat_id == "channel-1"
    assert outbound[0].text == "discord reply"


def test_conversation_service_uses_persona_profile_from_settings_path(tmp_path):
    persona_path = tmp_path / "custom-persona.yaml"
    persona_path.write_text(
        "\n".join(
            [
                'name: "Calm Planner"',
                "tone_rules:",
                '  - "Keep replies grounded and reassuring."',
                '  - "Start in Korean unless the user clearly prefers English."',
                "style_examples:",
                '  - "일정을 차분하게 정리해드릴게요."',
                '  - "필요한 정보만 짧게 먼저 말씀드릴게요."',
                "response_length_rules:",
                '  - "Default to two short sentences for routine replies."',
                '  - "Expand only when the user asks for more detail."',
                "disallowed_phrases:",
                '  - "As an AI language model"',
                '  - "제가 이미 전부 끝냈어요"',
                'safety_disclaimer: "Do not pretend a tool action succeeded without confirmation."',
            ]
        ),
        encoding="utf-8",
    )
    model = FakeModel(ModelTurnResponse(response_id="r1", text="차분하게 도와드릴게요."))
    container = make_container(
        tmp_path,
        model_client=model,
        persona_profile_path=str(persona_path),
    )
    session = container.session_factory()
    service = container.build_conversation_service(session)

    outbound = service.handle_event(
        InboundEvent(
            platform="telegram",
            external_user_id="tg-user",
            chat_id="chat-1",
            conversation_id="chat-1",
            message_id="msg-1",
            update_id="update-1",
            text="오늘 일정 정리해줘",
        )
    )

    assert outbound[0].text == "차분하게 도와드릴게요."
    assert model.messages[0]["role"] == "system"
    assert "Calm Planner" in model.messages[0]["content"]
    assert "Keep replies grounded and reassuring." in model.messages[0]["content"]
    assert "Default to two short sentences for routine replies." in model.messages[0]["content"]


def test_worker_sends_due_reminder_once_and_reschedules_recurring(tmp_path):
    container = make_container(tmp_path)
    session = container.session_factory()
    reminder_service = ReminderService(session, container.settings.default_timezone)
    recurring = reminder_service.create_reminder(
        user_id="user-1",
        source_platform="telegram",
        source_chat_id="chat-1",
        created_from_message_id=None,
        title="주간 회의 준비",
        due_at_local=(utcnow() + timedelta(hours=1)).astimezone().isoformat(),
        timezone="Asia/Seoul",
        recurrence={"type": "daily", "local_time": "09:00"},
        notes=None,
    )
    reminder = session.get(Reminder, recurring["reminder"]["id"])
    reminder.next_fire_at = utcnow() - timedelta(minutes=1)
    session.commit()

    processed_first = run_worker_once(container)
    session.refresh(reminder)

    assert processed_first == 1
    assert len(container.telegram_adapter.sent) == 1
    assert reminder.status == "scheduled"
    assert reminder.attempt_count == 0
    assert reminder.last_error is None
    assert reminder.next_attempt_at is None
    assert reminder.max_attempts == 3
    assert ensure_aware(reminder.next_fire_at) > utcnow()

    processed_second = run_worker_once(container)
    assert processed_second == 0


def test_worker_marks_one_time_reminder_sent_after_success(tmp_path):
    container = make_container(tmp_path)
    session = container.session_factory()
    reminder_service = ReminderService(session, container.settings.default_timezone)

    result = reminder_service.create_reminder(
        user_id="user-1",
        source_platform="telegram",
        source_chat_id="chat-1",
        created_from_message_id=None,
        title="약 먹기",
        due_at_local=(utcnow() + timedelta(hours=1)).astimezone().isoformat(),
        timezone="Asia/Seoul",
        recurrence={"type": "none"},
        notes=None,
    )
    reminder = session.get(Reminder, result["reminder"]["id"])
    reminder.next_fire_at = utcnow() - timedelta(minutes=1)
    session.commit()

    processed = run_worker_once(container)
    session.refresh(reminder)

    assert processed == 1
    assert len(container.telegram_adapter.sent) == 1
    assert reminder.status == "sent"
    assert reminder.attempt_count == 0
    assert reminder.last_error is None
    assert reminder.next_attempt_at is None
    assert reminder.max_attempts == 3


def test_worker_retries_failed_delivery_three_times_then_marks_failed(tmp_path):
    container = make_container(tmp_path)
    container.telegram_adapter = SequenceTelegramAdapter([False, False, False])
    session = container.session_factory()
    reminder_service = ReminderService(session, container.settings.default_timezone)

    result = reminder_service.create_reminder(
        user_id="user-1",
        source_platform="telegram",
        source_chat_id="chat-1",
        created_from_message_id=None,
        title="운동 알림",
        due_at_local=(utcnow() + timedelta(hours=1)).astimezone().isoformat(),
        timezone="Asia/Seoul",
        recurrence={"type": "none"},
        notes=None,
    )
    reminder = session.get(Reminder, result["reminder"]["id"])
    reminder.next_fire_at = utcnow() - timedelta(minutes=1)
    session.commit()

    processed_first = run_worker_once(container)
    session.refresh(reminder)

    assert processed_first == 1
    assert reminder.status == "pending"
    assert reminder.attempt_count == 1
    assert reminder.last_error == "delivery failed #1"
    assert reminder.max_attempts == 3
    assert reminder.next_attempt_at is not None
    assert ensure_aware(reminder.next_attempt_at) > utcnow()

    processed_before_retry = run_worker_once(container)
    assert processed_before_retry == 0

    reminder.next_attempt_at = utcnow() - timedelta(seconds=1)
    session.commit()
    processed_second = run_worker_once(container)
    session.refresh(reminder)

    assert processed_second == 1
    assert reminder.status == "pending"
    assert reminder.attempt_count == 2
    assert reminder.last_error == "delivery failed #2"
    assert reminder.next_attempt_at is not None
    assert ensure_aware(reminder.next_attempt_at) > utcnow()

    reminder.next_attempt_at = utcnow() - timedelta(seconds=1)
    session.commit()
    processed_third = run_worker_once(container)
    session.refresh(reminder)

    assert processed_third == 1
    assert reminder.status == "failed"
    assert reminder.attempt_count == 3
    assert reminder.last_error == "delivery failed #3"
    assert reminder.next_attempt_at is None
