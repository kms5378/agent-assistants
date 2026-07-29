# NVIDIA Direct Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the OpenAI Responses API runtime with NVIDIA NIM Chat Completions while retaining reminder and Calendar tool calls.

**Architecture:** `ConversationService` retains its `ConversationModel` dependency. `NvidiaChatCompletionsClient` uses the OpenAI SDK with NVIDIA's base URL, converts function schemas, and preserves just enough request-local state to post tool outputs as Chat Completions `tool` messages.

**Tech Stack:** Python 3.11+, FastAPI, OpenAI Python SDK, NVIDIA NIM, pytest.

## Global Constraints

- Use `https://integrate.api.nvidia.com/v1`.
- Default to `nvidia/nemotron-3-ultra-550b-a55b`.
- Replace `OPENAI_API_KEY` and `OPENAI_MODEL` with `NVIDIA_API_KEY` and `NVIDIA_MODEL`.
- Preserve `ConversationModel.create_turn`, `ConversationModel.submit_tool_outputs`, and the `ConversationService` loop.
- Never log the API key or swallow NVIDIA API failures.
- State in documentation that NVIDIA's free Endpoint is development/evaluation-only, not for production Telegram traffic.

---

## File Structure

- Create `app/services/nvidia_chat_completions.py`: client, protocol, tool schema conversion, response normalization, and in-flight tool-call state.
- Delete `app/services/openai_responses.py`: obsolete Responses API implementation.
- Modify `app/services/conversation.py`: import `ConversationModel` from the NVIDIA module.
- Modify `app/core/settings.py`: NVIDIA-only model settings.
- Modify `app/api/main.py`: direct NVIDIA client construction.
- Modify `tests/test_app.py`: NVIDIA client and container tests.
- Modify `README.md`: NVIDIA-only configuration and trial notice.

### Task 1: Build the NVIDIA initial-turn client

**Files:**
- Create: `app/services/nvidia_chat_completions.py`
- Delete: `app/services/openai_responses.py`
- Modify: `app/services/conversation.py:13`
- Test: `tests/test_app.py`

**Interfaces:**
- Produces: `ConversationModel` protocol with existing two method signatures.
- Produces: `NvidiaChatCompletionsClient(api_key: Optional[str], model: str)`.
- Produces: `create_turn(messages, tools) -> ModelTurnResponse`.

Add these test helpers above the NVIDIA client tests so every later test uses
the same SDK-shaped fake:

```python
from pathlib import Path
from types import SimpleNamespace

import pytest


REMINDER_LIST_TOOL = {
    "type": "function",
    "name": "reminder_list",
    "description": "List upcoming reminders.",
    "parameters": {"type": "object", "properties": {}},
}


class FakeToolCall:
    def __init__(self, *, id, name, arguments):
        self.id = id
        self.type = "function"
        self.function = SimpleNamespace(name=name, arguments=arguments)


class FakeChatResponse:
    def __init__(self, *, content, tool_calls):
        self.choices = [SimpleNamespace(
            message=SimpleNamespace(content=content, tool_calls=tool_calls)
        )]


class RecordingCompletions:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return next(self.responses)


def make_nvidia_client(*responses):
    client = NvidiaChatCompletionsClient(api_key="test-key", model="nvidia/model")
    recorder = RecordingCompletions(responses)
    client._client = SimpleNamespace(
        chat=SimpleNamespace(completions=recorder)
    )
    return client, recorder
```

- [ ] **Step 1: Write the failing test**

```python
def test_nvidia_client_converts_tools_and_returns_tool_calls():
    client, recorder = make_nvidia_client(
        FakeChatResponse(content=None, tool_calls=[
            FakeToolCall(id="call-1", name="reminder_list", arguments="{}")
        ])
    )

    turn = client.create_turn(
        messages=[{"role": "user", "content": "알림 보여줘"}],
        tools=[REMINDER_LIST_TOOL],
    )

    assert turn.text == ""
    assert turn.tool_calls == [
        ModelToolCall(call_id="call-1", name="reminder_list", arguments={})
    ]
    assert recorder.calls[0]["tools"] == [{
        "type": "function",
        "function": {
            "name": "reminder_list",
            "description": "List upcoming reminders.",
            "parameters": {"type": "object", "properties": {}},
        },
    }]
```

- [ ] **Step 2: Run the test and confirm it is red**

Run: `.venv/bin/pytest tests/test_app.py::test_nvidia_client_converts_tools_and_returns_tool_calls -v`

Expected: FAIL with `ModuleNotFoundError: app.services.nvidia_chat_completions`.

- [ ] **Step 3: Implement the minimal initial-turn client**

```python
class NvidiaChatCompletionsClient:
    def __init__(self, *, api_key: Optional[str], model: str) -> None:
        self.api_key = api_key
        self.model = model
        self._client = None
        self._pending_messages: dict[str, list[dict[str, Any]]] = {}

    def _get_client(self):
        if not self.api_key:
            raise RuntimeError("NVIDIA_API_KEY is not configured")
        if self._client is None:
            self._client = OpenAI(
                api_key=self.api_key,
                base_url="https://integrate.api.nvidia.com/v1",
            )
        return self._client

    def create_turn(self, *, messages, tools):
        response = self._get_client().chat.completions.create(
            model=self.model, messages=messages, tools=_to_chat_tools(tools)
        )
        return self._normalize_response(response, messages)
```

Implement `_to_chat_tools` by placing each existing `name`, `description`, and `parameters` in the nested `function` object. Normalize `response.choices[0].message.content` and parse every tool call's JSON arguments into `ModelToolCall`. Generate and retain a UUID response ID only if tool calls are present.

- [ ] **Step 4: Run the initial-turn test and confirm it is green**

Run: `.venv/bin/pytest tests/test_app.py::test_nvidia_client_converts_tools_and_returns_tool_calls -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/nvidia_chat_completions.py app/services/openai_responses.py app/services/conversation.py tests/test_app.py
git commit -m "feat: add NVIDIA chat completions client"
```

### Task 2: Continue a NVIDIA conversation after tool execution

**Files:**
- Modify: `app/services/nvidia_chat_completions.py`
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `previous_response_id` from `create_turn` and existing output dictionaries: `{"type": "function_call_output", "call_id": str, "output": str}`.
- Produces: `submit_tool_outputs(...) -> ModelTurnResponse`.

- [ ] **Step 1: Write the failing test**

```python
def test_nvidia_client_sends_tool_results_as_chat_tool_messages():
    client, recorder = make_nvidia_client(
        FakeChatResponse(content=None, tool_calls=[
            FakeToolCall(id="call-1", name="reminder_list", arguments="{}")
        ]),
        FakeChatResponse(content="알림이 없습니다.", tool_calls=[]),
    )
    initial = client.create_turn(
        messages=[{"role": "user", "content": "알림 보여줘"}],
        tools=[REMINDER_LIST_TOOL],
    )

    final = client.submit_tool_outputs(
        previous_response_id=initial.response_id,
        tool_outputs=[{
            "type": "function_call_output",
            "call_id": "call-1",
            "output": '{"status":"ok","items":[]}',
        }],
        tools=[REMINDER_LIST_TOOL],
    )

    assert final.text == "알림이 없습니다."
    assert recorder.calls[1]["messages"][-1] == {
        "role": "tool",
        "tool_call_id": "call-1",
        "content": '{"status":"ok","items":[]}',
    }
    assert initial.response_id not in client._pending_messages
```

- [ ] **Step 2: Run the test and confirm it is red**

Run: `.venv/bin/pytest tests/test_app.py::test_nvidia_client_sends_tool_results_as_chat_tool_messages -v`

Expected: FAIL because `submit_tool_outputs` does not reconstruct NVIDIA tool messages.

- [ ] **Step 3: Implement tool-result reconstruction**

```python
def submit_tool_outputs(self, *, previous_response_id, tool_outputs, tools):
    messages = self._pending_messages.pop(previous_response_id, None)
    if messages is None:
        raise RuntimeError(
            f"Unknown or completed NVIDIA response: {previous_response_id}"
        )
    messages = [
        *messages,
        *[
            {
                "role": "tool",
                "tool_call_id": item["call_id"],
                "content": item["output"],
            }
            for item in tool_outputs
        ],
    ]
    response = self._get_client().chat.completions.create(
        model=self.model, messages=messages, tools=_to_chat_tools(tools)
    )
    return self._normalize_response(response, messages)
```

When normalizing a tool-call response, append its assistant message—including every `id`, `type: "function"`, `function.name`, and `function.arguments`—before storing it as pending state. Do not store state for a text-only final response.

- [ ] **Step 4: Run the focused and conversation regression tests**

Run: `.venv/bin/pytest tests/test_app.py::test_nvidia_client_sends_tool_results_as_chat_tool_messages tests/test_app.py::test_conversation_service_creates_reminder_via_tool_call -v`

Expected: both PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/nvidia_chat_completions.py tests/test_app.py
git commit -m "feat: continue NVIDIA conversations after tool calls"
```

### Task 3: Make NVIDIA the sole runtime configuration

**Files:**
- Modify: `app/core/settings.py:35-36`
- Modify: `app/api/main.py:21,42-45`
- Test: `tests/test_app.py`

**Interfaces:**
- Produces: `Settings.nvidia_api_key: Optional[str]`.
- Produces: `Settings.nvidia_model: str`.
- Produces: `AppContainer.build()` with `NvidiaChatCompletionsClient`.

- [ ] **Step 1: Write the failing tests**

```python
def test_app_container_uses_nvidia_client(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'assistant.db'}",
        nvidia_api_key="test-key",
    )

    container = AppContainer.build(settings)

    assert isinstance(container.model_client, NvidiaChatCompletionsClient)
    assert container.model_client.model == "nvidia/nemotron-3-ultra-550b-a55b"


def test_nvidia_client_requires_api_key():
    with pytest.raises(RuntimeError, match="NVIDIA_API_KEY"):
        NvidiaChatCompletionsClient(api_key=None, model="nvidia/model")._get_client()
```

- [ ] **Step 2: Run the tests and confirm they are red**

Run: `.venv/bin/pytest tests/test_app.py::test_app_container_uses_nvidia_client tests/test_app.py::test_nvidia_client_requires_api_key -v`

Expected: FAIL because settings and the container still reference the OpenAI Responses client.

- [ ] **Step 3: Replace settings and dependency wiring**

```python
nvidia_api_key: Optional[str] = _env("NVIDIA_API_KEY")
nvidia_model: str = _env(
    "NVIDIA_MODEL", "nvidia/nemotron-3-ultra-550b-a55b"
) or "nvidia/nemotron-3-ultra-550b-a55b"
```

In `AppContainer.build`, import and construct:

```python
NvidiaChatCompletionsClient(
    api_key=resolved.nvidia_api_key,
    model=resolved.nvidia_model,
)
```

Remove only `openai_api_key` and `openai_model`; leave unrelated settings and explicit fake-model injection in `make_container` untouched.

- [ ] **Step 4: Run the configuration tests and confirm they are green**

Run: `.venv/bin/pytest tests/test_app.py::test_app_container_uses_nvidia_client tests/test_app.py::test_nvidia_client_requires_api_key -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/core/settings.py app/api/main.py tests/test_app.py
git commit -m "feat: make NVIDIA the assistant model provider"
```

### Task 4: Document the NVIDIA-only runtime and verify the migration

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: `NVIDIA_API_KEY`, optional `NVIDIA_MODEL`, and the NVIDIA endpoint.
- Produces: documentation without OpenAI runtime or configuration instructions.

- [ ] **Step 1: Replace the setup instructions**

Document NVIDIA NIM Chat Completions, the selected model, `NVIDIA_API_KEY`, optional `NVIDIA_MODEL`, endpoint `https://integrate.api.nvidia.com/v1`, and [the NVIDIA model page](https://build.nvidia.com/nvidia/nemotron-3-ultra-550b-a55b). Add this Korean warning exactly: `NVIDIA 무료 Endpoint는 개발·평가용이며 운영 Telegram 트래픽에 사용하지 않는다.`

- [ ] **Step 2: Run the full suite**

Run: `.venv/bin/pytest -q`

Expected: all tests PASS.

- [ ] **Step 3: Verify removal and commit**

Run: `git diff --check && rg -n "OPENAI_API_KEY|OPENAI_MODEL|Responses API" README.md app tests`

Expected: no OpenAI runtime or configuration references remain; retaining the generic `openai` Python package is expected because NVIDIA uses its compatible client.

```bash
git add README.md tests/test_app.py
git commit -m "docs: document NVIDIA NIM assistant setup"
```
