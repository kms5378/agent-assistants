from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import httpx

from app.contracts import InboundEvent, OutboundMessage


@dataclass
class TelegramAdapter:
    bot_token: str
    secret_token: str
    platform: str = "telegram"

    def parse_update(self, payload: dict) -> Optional[InboundEvent]:
        message = payload.get("message") or payload.get("edited_message")
        if not message:
            return None
        text = message.get("text")
        if not text:
            return None
        chat = message.get("chat") or {}
        sender = message.get("from") or {}
        display_name = " ".join(
            part for part in [sender.get("first_name"), sender.get("last_name")] if part
        ).strip() or sender.get("username")
        return InboundEvent(
            platform=self.platform,
            external_user_id=str(sender.get("id")),
            chat_id=str(chat.get("id")),
            conversation_id=str(chat.get("id")),
            message_id=str(message.get("message_id")),
            update_id=str(payload.get("update_id")),
            text=text,
            username=sender.get("username"),
            display_name=display_name or None,
            raw_payload=payload,
        )

    def send_message(self, message: OutboundMessage) -> None:
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": message.chat_id,
            "text": message.text,
        }
        if message.reply_to_message_id:
            payload["reply_to_message_id"] = message.reply_to_message_id
        response = httpx.post(url, json=payload, timeout=20)
        response.raise_for_status()
