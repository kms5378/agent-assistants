from __future__ import annotations

import json
from typing import Any, Optional, Protocol
from uuid import uuid4

from app.contracts import ModelToolCall, ModelTurnResponse


class ConversationModel(Protocol):
    def create_turn(self, *, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelTurnResponse:
        ...

    def submit_tool_outputs(
        self,
        *,
        previous_response_id: str,
        tool_outputs: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> ModelTurnResponse:
        ...

    def discard_response(self, *, response_id: str) -> None:
        ...


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
            from openai import OpenAI

            self._client = OpenAI(
                api_key=self.api_key,
                base_url="https://integrate.api.nvidia.com/v1",
            )
        return self._client

    def create_turn(self, *, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelTurnResponse:
        response = self._create_completion(messages=messages, tools=tools)
        return self._normalize_response(response, messages)

    def submit_tool_outputs(
        self,
        *,
        previous_response_id: str,
        tool_outputs: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> ModelTurnResponse:
        messages = self._pending_messages.pop(previous_response_id, None)
        if messages is None:
            raise RuntimeError(f"Unknown or completed NVIDIA response: {previous_response_id}")
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
        response = self._create_completion(
            messages=messages,
            tools=tools,
        )
        return self._normalize_response(response, messages)

    def discard_response(self, *, response_id: str) -> None:
        self._pending_messages.pop(response_id, None)

    def _create_completion(self, *, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> Any:
        request: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "tools": _to_chat_tools(tools),
            "stream": False,
        }
        if tools:
            request["extra_body"] = {
                "chat_template_kwargs": {
                    "enable_thinking": True,
                    "force_nonempty_content": True,
                }
            }
        return self._get_client().chat.completions.create(**request)

    def _normalize_response(self, response: Any, messages: list[dict[str, Any]]) -> ModelTurnResponse:
        message = _lookup(_lookup(response, "choices", default=[])[0], "message")
        text = _lookup(message, "content", default="") or ""
        tool_calls: list[ModelToolCall] = []
        assistant_tool_calls: list[dict[str, Any]] = []
        for tool_call in _lookup(message, "tool_calls", default=[]) or []:
            function = _lookup(tool_call, "function")
            raw_arguments = _lookup(function, "arguments", default="{}") or "{}"
            try:
                arguments = json.loads(raw_arguments)
            except json.JSONDecodeError:
                arguments = {}
            call_id = _lookup(tool_call, "id") or ""
            name = _lookup(function, "name") or ""
            tool_calls.append(ModelToolCall(call_id=call_id, name=name, arguments=arguments))
            assistant_tool_calls.append(
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": raw_arguments},
                }
            )

        if not tool_calls:
            return ModelTurnResponse(response_id=None, text=text)

        response_id = str(uuid4())
        assistant_message: dict[str, Any] = {"role": "assistant", "content": text or None, "tool_calls": assistant_tool_calls}
        self._pending_messages[response_id] = [*messages, assistant_message]
        return ModelTurnResponse(response_id=response_id, text=text, tool_calls=tool_calls)


def _to_chat_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool["parameters"],
            },
        }
        for tool in tools
    ]


def _lookup(obj: Any, field: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(field, default)
    return getattr(obj, field, default)
