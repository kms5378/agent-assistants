from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.contracts import InboundEvent, InternalUser, OutboundMessage
from app.core.settings import Settings
from app.models import ChannelAccount, ConversationSummary, Message, User
from app.services.openai_responses import ConversationModel
from app.services.tool_router import ToolRouter


SYSTEM_PROMPT = """You are a bilingual personal assistant for Telegram and future Discord channels.
- Be concise, helpful, and natural.
- Support Korean first, with English when the user uses English.
- Use reminder and calendar tools when they are needed.
- If a reminder time is incomplete or ambiguous, ask a follow-up question instead of calling a tool.
- When deleting reminders, do not assume which one to delete if there are multiple matches.
- If Google Calendar is not connected, explain that the user needs to connect Google and include the provided link.
"""


@dataclass
class ConversationService:
    session: Session
    settings: Settings
    model_client: ConversationModel
    tool_router: ToolRouter

    def handle_event(self, event: InboundEvent) -> list[OutboundMessage]:
        user = self._ensure_internal_user(event)
        inbound = self._store_inbound_message(user, event)
        if inbound is None:
            return []

        messages = self._build_prompt_messages(user_id=user.id, platform=event.platform, conversation_id=event.conversation_id)
        tools = self.tool_router.tool_definitions()
        response = self.model_client.create_turn(messages=messages, tools=tools)

        while response.tool_calls:
            tool_outputs: list[dict[str, str]] = []
            for tool_call in response.tool_calls:
                result = self.tool_router.execute(
                    name=tool_call.name,
                    arguments=tool_call.arguments,
                    user=user,
                    event=event,
                    message_id=inbound.id,
                )
                self._store_tool_message(user.id, event, tool_call.name, result)
                tool_outputs.append(
                    {
                        "type": "function_call_output",
                        "call_id": tool_call.call_id,
                        "output": json.dumps(result, ensure_ascii=False),
                    }
                )
            if not response.response_id:
                break
            response = self.model_client.submit_tool_outputs(
                previous_response_id=response.response_id,
                tool_outputs=tool_outputs,
                tools=tools,
            )

        final_text = response.text.strip() or "도와드릴 준비가 되었어요."
        self._store_assistant_message(user.id, event, final_text)
        self._refresh_summary(user.id, event.platform, event.conversation_id)
        self.session.commit()
        return [
            OutboundMessage(
                platform=event.platform,
                chat_id=event.chat_id,
                text=final_text,
                reply_to_message_id=event.message_id,
            )
        ]

    def _ensure_internal_user(self, event: InboundEvent) -> InternalUser:
        account = self.session.scalar(
            select(ChannelAccount).where(
                ChannelAccount.platform == event.platform,
                ChannelAccount.platform_user_id == event.external_user_id,
            )
        )
        if account is None:
            user = User(timezone=self.settings.default_timezone)
            account = ChannelAccount(
                user=user,
                platform=event.platform,
                platform_user_id=event.external_user_id,
                platform_chat_id=event.chat_id,
                username=event.username,
                display_name=event.display_name,
            )
            self.session.add_all([user, account])
            self.session.flush()
        else:
            account.platform_chat_id = event.chat_id
            account.username = event.username
            account.display_name = event.display_name
            self.session.flush()
            user = account.user
        return InternalUser(
            id=user.id,
            timezone=user.timezone,
            platform=account.platform,
            platform_user_id=account.platform_user_id,
        )

    def _store_inbound_message(self, user: InternalUser, event: InboundEvent) -> Optional[Message]:
        message = Message(
            user_id=user.id,
            platform=event.platform,
            conversation_id=event.conversation_id,
            chat_id=event.chat_id,
            external_message_id=event.message_id,
            external_update_id=event.update_id,
            direction="inbound",
            role="user",
            content=event.text,
        )
        self.session.add(message)
        try:
            self.session.flush()
        except IntegrityError:
            self.session.rollback()
            return None
        return message

    def _store_tool_message(self, user_id: str, event: InboundEvent, tool_name: str, payload: dict) -> None:
        self.session.add(
            Message(
                user_id=user_id,
                platform=event.platform,
                conversation_id=event.conversation_id,
                chat_id=event.chat_id,
                direction="internal",
                role="tool",
                tool_name=tool_name,
                tool_payload=json.dumps(payload, ensure_ascii=False),
                content=None,
            )
        )
        self.session.flush()

    def _store_assistant_message(self, user_id: str, event: InboundEvent, text: str) -> None:
        self.session.add(
            Message(
                user_id=user_id,
                platform=event.platform,
                conversation_id=event.conversation_id,
                chat_id=event.chat_id,
                direction="outbound",
                role="assistant",
                content=text,
            )
        )
        self.session.flush()

    def _build_prompt_messages(self, *, user_id: str, platform: str, conversation_id: str) -> list[dict]:
        items = [{"role": "system", "content": SYSTEM_PROMPT}]
        summary = self.session.scalar(
            select(ConversationSummary).where(
                ConversationSummary.user_id == user_id,
                ConversationSummary.platform == platform,
                ConversationSummary.conversation_id == conversation_id,
            )
        )
        if summary:
            items.append(
                {
                    "role": "developer",
                    "content": f"Conversation summary:\n{summary.summary_text}",
                }
            )

        recent_messages = list(
            self.session.scalars(
                select(Message)
                .where(
                    Message.user_id == user_id,
                    Message.platform == platform,
                    Message.conversation_id == conversation_id,
                    Message.role.in_(["user", "assistant"]),
                )
                .order_by(Message.id.desc())
                .limit(self.settings.recent_message_window)
            )
        )
        for message in reversed(recent_messages):
            items.append({"role": message.role, "content": message.content or ""})
        return items

    def _refresh_summary(self, user_id: str, platform: str, conversation_id: str) -> None:
        messages = list(
            self.session.scalars(
                select(Message)
                .where(
                    Message.user_id == user_id,
                    Message.platform == platform,
                    Message.conversation_id == conversation_id,
                    Message.role.in_(["user", "assistant"]),
                )
                .order_by(Message.id.asc())
            )
        )
        if len(messages) < self.settings.summary_trigger_messages:
            return

        summary = self.session.scalar(
            select(ConversationSummary).where(
                ConversationSummary.user_id == user_id,
                ConversationSummary.platform == platform,
                ConversationSummary.conversation_id == conversation_id,
            )
        )
        if summary is not None and len(messages) - summary.message_count < self.settings.summary_trigger_messages:
            return

        older_messages = messages[:-self.settings.recent_message_window] if len(messages) > self.settings.recent_message_window else messages
        summary_lines = []
        for message in older_messages[-10:]:
            prefix = "User" if message.role == "user" else "Assistant"
            content = (message.content or "").strip().replace("\n", " ")
            summary_lines.append(f"- {prefix}: {content[:140]}")
        if not summary_lines:
            return
        summary_text = "Key context so far:\n" + "\n".join(summary_lines)

        if summary is None:
            summary = ConversationSummary(
                user_id=user_id,
                platform=platform,
                conversation_id=conversation_id,
                summary_text=summary_text,
                message_count=len(messages),
            )
            self.session.add(summary)
        else:
            summary.summary_text = summary_text
            summary.message_count = len(messages)
        self.session.flush()
