# NVIDIA Chat Completions Migration Design

## Goal

Replace the OpenAI Responses API runtime with NVIDIA NIM's OpenAI-compatible
Chat Completions endpoint, including function calls for reminders and Google
Calendar.

The selected model is `nvidia/nemotron-3-ultra-550b-a55b`. NVIDIA's free
endpoint is for development and evaluation only; it must not be configured for
production Telegram traffic.

## Configuration

Replace the OpenAI settings with:

- `NVIDIA_API_KEY`: required at runtime.
- `NVIDIA_MODEL`: defaults to `nvidia/nemotron-3-ultra-550b-a55b`.

`OPENAI_API_KEY` and `OPENAI_MODEL` are removed from the application settings,
README, and deployment configuration. The NVIDIA key is only read by the
NVIDIA client and is never logged.

## Architecture

Keep the existing `ConversationModel` turn methods and `ConversationService`
tool loop. Add a `discard_response` cleanup method to the protocol. Replace `OpenAIResponsesClient` with
`NvidiaChatCompletionsClient`; `AppContainer.build` always constructs the
NVIDIA client.

The NVIDIA client calls `https://integrate.api.nvidia.com/v1/chat/completions`
through the existing OpenAI Python SDK. It converts the project's flat
Responses API tool definitions into Chat Completions function definitions:

```json
{"type":"function","function":{"name":"...","description":"...","parameters":{}}}
```

It explicitly requests non-streaming responses and sets
`enable_thinking` plus `force_nonempty_content` for tool-enabled calls. To
meet the selected model's message-role contract, conversation summaries are
appended to the initial system message instead of emitted as `developer`
messages.

For a tool call, the client records the original chat messages and the
assistant's tool-call message under a generated, request-local response ID.
When `ConversationService` submits tool output, the client reconstructs the
NVIDIA message sequence with `role: tool`, the original `tool_call_id`, and the
JSON result. A follow-up that contains another tool call receives a new local
response ID. State is removed after a terminal response so request-local chat
state does not accumulate. If tool execution or its follow-up request fails,
`ConversationService` discards the pending response state.

## Error Handling

Malformed tool-call JSON is normalized to an empty argument object. Calling
`submit_tool_outputs` with an unknown or completed response ID raises a
descriptive runtime error instead of silently dropping tool results. NVIDIA
HTTP/API errors are allowed to surface to the existing request boundary; they
are not swallowed.

## Tests and Documentation

Unit tests will use a recording fake Chat Completions client and assert:

1. NVIDIA endpoint/model/key configuration;
2. conversion of tools and parsing of tool calls;
3. reconstruction of assistant and tool-result messages for a follow-up turn;
4. required NVIDIA-key failure.

Update the README with the NVIDIA environment variables, model link, and the
trial-only restriction. Remove the OpenAI environment-variable instructions.
Existing conversation and application tests must remain green.
