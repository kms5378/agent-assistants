from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from typing import Any


@dataclass
class InboundEvent:
    platform: str
    external_user_id: str
    chat_id: str
    conversation_id: str
    message_id: str
    update_id: str
    text: str
    username: Optional[str] = None
    display_name: Optional[str] = None
    raw_payload: dict[str, Any] = field(default_factory=dict)
    received_at: Optional[datetime] = None


@dataclass
class InternalUser:
    id: str
    timezone: str
    platform: str
    platform_user_id: str


@dataclass
class OutboundMessage:
    platform: str
    chat_id: str
    text: str
    reply_to_message_id: Optional[str] = None


@dataclass
class ModelToolCall:
    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ModelTurnResponse:
    response_id: Optional[str]
    text: str
    tool_calls: list[ModelToolCall] = field(default_factory=list)
