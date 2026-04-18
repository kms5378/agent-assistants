from __future__ import annotations

from typing import Optional, Protocol

from app.contracts import InboundEvent, OutboundMessage


class ChannelAdapter(Protocol):
    platform: str

    def parse_update(self, payload: dict) -> Optional[InboundEvent]:
        ...

    def send_message(self, message: OutboundMessage) -> None:
        ...
