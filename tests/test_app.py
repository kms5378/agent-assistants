from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from typing import Optional

import pytest
from fastapi.testclient import TestClient

from app.api.main import AppContainer, create_app
from app.channels.telegram import TelegramAdapter
from app.contracts import InboundEvent, ModelToolCall, ModelTurnResponse
from app.core.settings import Settings
from app.core.time import ensure_aware, utcnow
from app.db import build_session_factory, init_db
from app.models import Reminder
from app.services.conversation import ConversationService
from app.services.google_calendar import OAuthConnectionRequired
from app.services.nvidia_chat_completions import NvidiaChatCompletionsClient
from app.services.reminders import ReminderService
from app.worker import run_worker_once


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


REMINDER_LIST_TOOL = {
    "type": "function",
    "name": "reminder_list",
    "description": "List upcoming reminders.",
    "parameters": {"type": "object", "properties": {}},
}


class FakeChatToolCall:
    def __init__(self, *, call_id: str, name: str, arguments: str) -> None:
        self.id = call_id
        self.type = "function"
        self.function = SimpleNamespace(name=name, arguments=arguments)


class FakeChatResponse:
    def __init__(self, *, content: Optional[str], tool_calls: list[FakeChatToolCall]) -> None:
        self.choices = [
            SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=tool_calls))
        ]


class RecordingChatCompletions:
    def __init__(self, responses: list[FakeChatResponse]) -> None:
        self.responses = iter(responses)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return next(self.responses)


def make_nvidia_client(*responses: FakeChatResponse) -> tuple[NvidiaChatCompletionsClient, RecordingChatCompletions]:
    client = NvidiaChatCompletionsClient(api_key="test-key", model="nvidia/model")
    completions = RecordingChatCompletions(list(responses))
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return client, completions


def test_nvidia_client_converts_tools_and_returns_tool_calls():
    client, completions = make_nvidia_client(
        FakeChatResponse(
            content=None,
            tool_calls=[FakeChatToolCall(call_id="call-1", name="reminder_list", arguments="{}")],
        )
    )

    turn = client.create_turn(
        messages=[{"role": "user", "content": "알림 보여줘"}],
        tools=[REMINDER_LIST_TOOL],
    )

    assert turn.text == ""
    assert turn.response_id
    assert turn.tool_calls == [ModelToolCall(call_id="call-1", name="reminder_list", arguments={})]
    assert completions.calls == [
        {
            "model": "nvidia/model",
            "messages": [{"role": "user", "content": "알림 보여줘"}],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "reminder_list",
                        "description": "List upcoming reminders.",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
        }
    ]


def test_app_container_uses_nvidia_client(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'assistant.db'}",
        nvidia_api_key="test-key",
    )

    container = AppContainer.build(settings)

    assert isinstance(container.model_client, NvidiaChatCompletionsClient)
    assert container.model_client.model == "nvidia/nemotron-3-ultra-550b-a55b"


def test_nvidia_client_rejects_a_missing_api_key():
    client = NvidiaChatCompletionsClient(api_key=None, model="nvidia/model")

    with pytest.raises(RuntimeError, match="NVIDIA_API_KEY is not configured"):
        client._get_client()


def test_nvidia_client_sends_tool_results_as_chat_tool_messages():
    client, completions = make_nvidia_client(
        FakeChatResponse(
            content=None,
            tool_calls=[FakeChatToolCall(call_id="call-1", name="reminder_list", arguments="{}")],
        ),
        FakeChatResponse(content="알림이 없습니다.", tool_calls=[]),
    )
    initial = client.create_turn(
        messages=[{"role": "user", "content": "알림 보여줘"}],
        tools=[REMINDER_LIST_TOOL],
    )

    final = client.submit_tool_outputs(
        previous_response_id=initial.response_id or "",
        tool_outputs=[
            {
                "type": "function_call_output",
                "call_id": "call-1",
                "output": '{"status":"ok","items":[]}',
            }
        ],
        tools=[REMINDER_LIST_TOOL],
    )

    assert final.text == "알림이 없습니다."
    assert completions.calls[1]["messages"][-1] == {
        "role": "tool",
        "tool_call_id": "call-1",
        "content": '{"status":"ok","items":[]}',
    }
    assert initial.response_id not in client._pending_messages


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
