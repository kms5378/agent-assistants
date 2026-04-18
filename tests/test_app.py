from __future__ import annotations

from datetime import timedelta
from typing import Optional
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api.main import AppContainer, create_app
from app.channels.telegram import TelegramAdapter
from app.contracts import InboundEvent, ModelToolCall, ModelTurnResponse
from app.core.settings import Settings
from app.core.time import ensure_aware, utcnow
from app.db import (
    SCHEMA_BASELINE_DESCRIPTION,
    SCHEMA_BASELINE_VERSION,
    build_engine,
    build_session_factory,
    init_db,
)
from app.models import Reminder, SchemaMigration
from app.services.conversation import ConversationService
from app.services.google_calendar import OAuthConnectionRequired
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


class DummyGoogleService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def get_connect_url(self, user_id: str, platform: str = "telegram") -> str:
        return f"{self.settings.app_base_url}/auth/google/start?user_id={user_id}&platform={platform}"

    def create_state_token(self) -> str:
        return "state"

    def build_authorization_url(self, *, state: str) -> str:
        return f"https://accounts.example.com/oauth?state={state}"

    def exchange_code(self, code: str):  # pragma: no cover - not used in tests
        raise NotImplementedError

    def save_tokens(self, session, *, user_id: str, bundle):  # pragma: no cover - not used in tests
        raise NotImplementedError

    def list_events(self, *args, **kwargs):
        raise OAuthConnectionRequired("Google account is not connected.")

    def create_event(self, *args, **kwargs):
        raise OAuthConnectionRequired("Google account is not connected.")

    def update_event(self, *args, **kwargs):
        raise OAuthConnectionRequired("Google account is not connected.")


def make_container(tmp_path, model_client=None):
    settings = Settings(
        app_base_url="https://assistant.example.com",
        database_url=f"sqlite:///{tmp_path / 'assistant.db'}",
        telegram_webhook_secret="secret",
        telegram_webhook_key="hook-key",
        default_timezone="Asia/Seoul",
        encryption_key="test-key",
    )
    session_factory = build_session_factory(settings)
    init_db(session_factory)
    return AppContainer(
        settings=settings,
        session_factory=session_factory,
        model_client=model_client or FakeModel(ModelTurnResponse(response_id="r1", text="안녕하세요")),
        telegram_adapter=RecordingTelegramAdapter(),
        google_service=DummyGoogleService(settings),
    )


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
    assert ensure_aware(reminder.next_fire_at) > utcnow()

    processed_second = run_worker_once(container)
    assert processed_second == 0
