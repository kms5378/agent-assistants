from __future__ import annotations

import json
from typing import Any, Optional, Protocol

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


class OpenAIResponsesClient:
    def __init__(self, *, api_key: Optional[str], model: str) -> None:
        self.api_key = api_key
        self.model = model
        self._client = None

    def _get_client(self):
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured")
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(api_key=self.api_key)
        return self._client

    def create_turn(self, *, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelTurnResponse:
        response = self._get_client().responses.create(
            model=self.model,
            input=messages,
            tools=tools,
        )
        return self._normalize_response(response)

    def submit_tool_outputs(
        self,
        *,
        previous_response_id: str,
        tool_outputs: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> ModelTurnResponse:
        response = self._get_client().responses.create(
            model=self.model,
            previous_response_id=previous_response_id,
            input=tool_outputs,
            tools=tools,
        )
        return self._normalize_response(response)

    def _normalize_response(self, response: Any) -> ModelTurnResponse:
        response_id = _lookup(response, "id")
        text = _extract_text(response)
        tool_calls: list[ModelToolCall] = []
        for item in _lookup(response, "output", default=[]) or []:
            item_type = _lookup(item, "type")
            if item_type != "function_call":
                continue
            raw_args = _lookup(item, "arguments", default="{}") or "{}"
            try:
                arguments = json.loads(raw_args)
            except json.JSONDecodeError:
                arguments = {}
            tool_calls.append(
                ModelToolCall(
                    call_id=_lookup(item, "call_id") or _lookup(item, "id") or "",
                    name=_lookup(item, "name") or "",
                    arguments=arguments,
                )
            )
        return ModelTurnResponse(response_id=response_id, text=text, tool_calls=tool_calls)


def _extract_text(response: Any) -> str:
    output_text = _lookup(response, "output_text")
    if isinstance(output_text, str):
        return output_text.strip()
    parts: list[str] = []
    for item in _lookup(response, "output", default=[]) or []:
        if _lookup(item, "type") != "message":
            continue
        for content in _lookup(item, "content", default=[]) or []:
            text = _lookup(content, "text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(part for part in parts if part).strip()


def _lookup(obj: Any, field: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(field, default)
    return getattr(obj, field, default)
