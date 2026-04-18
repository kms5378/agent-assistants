from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from app.contracts import InboundEvent, InternalUser
from app.services.google_calendar import GoogleOAuthService, OAuthConnectionRequired
from app.services.reminders import ReminderService


AuthUrlBuilder = Callable[[str, str], str]


@dataclass
class ToolRouter:
    reminder_service: ReminderService
    google_service: GoogleOAuthService
    auth_url_builder: AuthUrlBuilder

    def tool_definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "name": "reminder_create",
                "description": "Create a one-time or recurring reminder. Ask follow-up questions before calling if time is ambiguous.",
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "due_at_local": {"type": "string", "description": "ISO 8601 datetime in the user's timezone"},
                        "timezone": {"type": "string"},
                        "recurrence": {
                            "type": "object",
                            "properties": {
                                "type": {"type": "string", "enum": ["none", "daily", "weekly", "monthly"]},
                                "days_of_week": {
                                    "type": "array",
                                    "items": {"type": "integer", "minimum": 0, "maximum": 6},
                                },
                                "day_of_month": {"type": "integer", "minimum": 1, "maximum": 31},
                                "local_time": {"type": "string"},
                            },
                            "required": ["type"],
                            "additionalProperties": False,
                        },
                        "delivery_channel": {"type": "string"},
                        "notes": {"type": "string"},
                    },
                    "required": ["title", "due_at_local", "timezone", "recurrence", "delivery_channel"],
                    "additionalProperties": False,
                },
            },
            {
                "type": "function",
                "name": "reminder_search",
                "description": "Search for reminders by keyword before confirming deletion or edits.",
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                    "additionalProperties": False,
                },
            },
            {
                "type": "function",
                "name": "reminder_delete",
                "description": "Delete a reminder by id or keyword. For recurring reminders clarify whether to delete one occurrence or the entire series.",
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "reminder_id": {"type": "string"},
                        "query": {"type": "string"},
                        "delete_scope": {"type": "string", "enum": ["single", "series"]},
                    },
                    "additionalProperties": False,
                },
            },
            {
                "type": "function",
                "name": "reminder_list",
                "description": "List upcoming reminders for the user.",
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            },
            {
                "type": "function",
                "name": "calendar_list_events",
                "description": "List Google Calendar events in a time range.",
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "start_local": {"type": "string"},
                        "end_local": {"type": "string"},
                        "timezone": {"type": "string"},
                        "calendar_id": {"type": "string"},
                    },
                    "required": ["start_local", "end_local", "timezone"],
                    "additionalProperties": False,
                },
            },
            {
                "type": "function",
                "name": "calendar_create_event",
                "description": "Create a Google Calendar event.",
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "start_local": {"type": "string"},
                        "end_local": {"type": "string"},
                        "timezone": {"type": "string"},
                        "description": {"type": "string"},
                        "location": {"type": "string"},
                        "calendar_id": {"type": "string"},
                    },
                    "required": ["title", "start_local", "end_local", "timezone"],
                    "additionalProperties": False,
                },
            },
            {
                "type": "function",
                "name": "calendar_update_event",
                "description": "Update an existing Google Calendar event.",
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "event_id": {"type": "string"},
                        "timezone": {"type": "string"},
                        "title": {"type": "string"},
                        "start_local": {"type": "string"},
                        "end_local": {"type": "string"},
                        "description": {"type": "string"},
                        "location": {"type": "string"},
                        "calendar_id": {"type": "string"},
                    },
                    "required": ["event_id", "timezone"],
                    "additionalProperties": False,
                },
            },
        ]

    def execute(
        self,
        *,
        name: str,
        arguments: dict[str, Any],
        user: InternalUser,
        event: InboundEvent,
        message_id: Optional[int],
    ) -> dict:
        if name == "reminder_create":
            return self.reminder_service.create_reminder(
                user_id=user.id,
                source_platform=user.platform,
                source_chat_id=event.chat_id,
                created_from_message_id=message_id,
                title=arguments["title"],
                due_at_local=arguments["due_at_local"],
                timezone=arguments.get("timezone") or user.timezone,
                recurrence=arguments.get("recurrence"),
                notes=arguments.get("notes"),
            )
        if name == "reminder_search":
            return self.reminder_service.search_reminders(user_id=user.id, query=arguments["query"])
        if name == "reminder_delete":
            return self.reminder_service.delete_reminder(
                user_id=user.id,
                reminder_id=arguments.get("reminder_id"),
                query=arguments.get("query"),
                delete_scope=arguments.get("delete_scope"),
            )
        if name == "reminder_list":
            return self.reminder_service.list_reminders(user_id=user.id)

        try:
            if name == "calendar_list_events":
                return self.google_service.list_events(
                    self.reminder_service.session,
                    user_id=user.id,
                    timezone=arguments.get("timezone") or user.timezone,
                    start_local=arguments["start_local"],
                    end_local=arguments["end_local"],
                    calendar_id=arguments.get("calendar_id", "primary"),
                )
            if name == "calendar_create_event":
                return self.google_service.create_event(
                    self.reminder_service.session,
                    user_id=user.id,
                    timezone=arguments.get("timezone") or user.timezone,
                    title=arguments["title"],
                    start_local=arguments["start_local"],
                    end_local=arguments["end_local"],
                    description=arguments.get("description"),
                    location=arguments.get("location"),
                    calendar_id=arguments.get("calendar_id", "primary"),
                )
            if name == "calendar_update_event":
                return self.google_service.update_event(
                    self.reminder_service.session,
                    user_id=user.id,
                    timezone=arguments.get("timezone") or user.timezone,
                    event_id=arguments["event_id"],
                    title=arguments.get("title"),
                    start_local=arguments.get("start_local"),
                    end_local=arguments.get("end_local"),
                    description=arguments.get("description"),
                    location=arguments.get("location"),
                    calendar_id=arguments.get("calendar_id", "primary"),
                )
        except OAuthConnectionRequired as exc:
            return {
                "status": "oauth_required",
                "message": str(exc),
                "connect_url": self.auth_url_builder(user.id, user.platform),
            }

        return {"status": "error", "message": f"Unknown tool: {name}"}
