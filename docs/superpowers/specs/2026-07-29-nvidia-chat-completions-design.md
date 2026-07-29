# NVIDIA Chat Completions Provider Design

## Goal

Allow the assistant to use NVIDIA NIM's OpenAI-compatible Chat Completions
endpoint, including function calls for reminders and Google Calendar, while
preserving the existing OpenAI Responses API implementation as the default.

The NVIDIA development model is
`nvidia/nemotron-3-ultra-550b-a55b`. NVIDIA's free endpoint is for development
and evaluation only; it must not be configured for production Telegram traffic.

## Configuration

Add these settings:

- `AI_PROVIDER`: `openai` (default) or `nvidia`.
- `NVIDIA_API_KEY`: required when `AI_PROVIDER=nvidia`.
- `NVIDIA_MODEL`: defaults to `nvidia/nemotron-3-ultra-550b-a55b`.

Existing `OPENAI_API_KEY` and `OPENAI_MODEL` retain their current meaning and
are used unchanged when `AI_PROVIDER=openai`.

An unsupported provider value fails at container construction with a clear
error. No API key is read or logged outside of its selected provider client.

## Architecture

Keep `ConversationModel` unchanged so `ConversationService` and its tool loop
remain provider-neutral. Add `NvidiaChatCompletionsClient` beside
`OpenAIResponsesClient` and select the client in `AppContainer.build`.

The NVIDIA client calls `https://integrate.api.nvidia.com/v1/chat/completions`
through the existing OpenAI Python SDK. It converts the project's flat
Responses API tool definitions into Chat Completions function definitions:

```json
{"type":"function","function":{"name":"...","description":"...","parameters":{}}}
```

For a tool call, the client records the original chat messages and the
assistant's tool-call message under a generated, request-local response ID.
When `ConversationService` submits tool output, the client reconstructs the
NVIDIA message sequence with `role: tool`, the original `tool_call_id`, and the
JSON result. The follow-up response receives a new response ID if it asks for
another tool. State is removed after a terminal response so request-local chat
state does not accumulate.

## Error Handling

Malformed tool-call JSON is normalized to an empty argument object, matching
the existing OpenAI client behavior. Calling `submit_tool_outputs` with an
unknown or completed response ID raises a descriptive runtime error instead of
silently dropping tool results. NVIDIA HTTP/API errors are allowed to surface to
the existing request boundary; they are not swallowed.

## Tests and Documentation

Unit tests will use a recording fake Chat Completions client and assert:

1. request endpoint/model/key configuration;
2. conversion of tools and parsing of tool calls;
3. reconstruction of assistant and tool-result messages for a follow-up turn;
4. provider selection and invalid-provider failure.

Update the README with the NVIDIA environment variables, model link, and the
trial-only restriction. Existing conversation and application tests must remain
green.
