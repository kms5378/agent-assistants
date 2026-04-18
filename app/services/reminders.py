from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import Select, and_, or_, select
from sqlalchemy.orm import Session

from app.core.time import (
    RecurrenceConfig,
    TimeValidationError,
    compute_next_fire_at,
    ensure_aware,
    parse_local_datetime,
    recurrence_rule_from_text,
    recurrence_rule_to_text,
    utcnow,
)
from app.models import Reminder, ReminderDelivery

RETRY_DELAY = timedelta(minutes=3)
DEFAULT_MAX_ATTEMPTS = 3


@dataclass
class ReminderService:
    session: Session
    default_timezone: str

    def create_reminder(
        self,
        *,
        user_id: str,
        source_platform: str,
        source_chat_id: str,
        created_from_message_id: Optional[int],
        title: str,
        due_at_local: str,
        timezone: Optional[str],
        recurrence: Optional[dict] = None,
        notes: Optional[str] = None,
    ) -> dict:
        tz_name = timezone or self.default_timezone
        recurrence_config = RecurrenceConfig.from_dict(recurrence)
        try:
            due_local = parse_local_datetime(due_at_local, tz_name)
        except TimeValidationError as exc:
            return {"status": "error", "message": str(exc)}

        now_local = utcnow().astimezone(due_local.tzinfo)
        if recurrence_config.type == "none" and due_local <= now_local:
            return {"status": "error", "message": "due_at_local must be in the future"}

        try:
            next_fire_at = compute_next_fire_at(
                due_at_local=due_local,
                recurrence=recurrence_config,
                reference_utc=utcnow(),
            )
        except TimeValidationError as exc:
            return {"status": "error", "message": str(exc)}

        reminder = Reminder(
            user_id=user_id,
            source_platform=source_platform,
            source_chat_id=source_chat_id,
            title=title,
            timezone=tz_name,
            due_at=due_local.astimezone(due_local.tzinfo),
            recurrence_type=recurrence_config.type,
            recurrence_rule=recurrence_rule_to_text(recurrence_config.as_dict()),
            next_fire_at=next_fire_at,
            status="scheduled",
            attempt_count=0,
            last_error=None,
            next_attempt_at=None,
            max_attempts=DEFAULT_MAX_ATTEMPTS,
            notes=notes,
            created_from_message_id=created_from_message_id,
        )
        self.session.add(reminder)
        self.session.flush()
        return {
            "status": "ok",
            "reminder": self.serialize_reminder(reminder),
        }

    def search_reminders(self, *, user_id: str, query: str, limit: int = 5) -> dict:
        statement = self._active_reminders_statement(user_id).where(
            or_(
                Reminder.title.ilike(f"%{query}%"),
                Reminder.notes.ilike(f"%{query}%"),
            )
        ).limit(limit)
        reminders = list(self.session.scalars(statement))
        return {
            "status": "ok",
            "items": [self.serialize_reminder(reminder) for reminder in reminders],
        }

    def list_reminders(self, *, user_id: str, limit: int = 10) -> dict:
        reminders = list(self.session.scalars(self._active_reminders_statement(user_id).limit(limit)))
        return {
            "status": "ok",
            "items": [self.serialize_reminder(reminder) for reminder in reminders],
        }

    def delete_reminder(
        self,
        *,
        user_id: str,
        reminder_id: Optional[str] = None,
        query: Optional[str] = None,
        delete_scope: Optional[str] = None,
    ) -> dict:
        reminder = None
        if reminder_id:
            reminder = self.session.get(Reminder, reminder_id)
            if reminder is None or reminder.user_id != user_id:
                return {"status": "not_found", "message": "Reminder not found."}
        elif query:
            matches = list(
                self.session.scalars(
                    self._active_reminders_statement(user_id).where(Reminder.title.ilike(f"%{query}%")).limit(5)
                )
            )
            if not matches:
                return {"status": "not_found", "message": "No reminders matched that request."}
            if len(matches) > 1:
                return {
                    "status": "ambiguity",
                    "message": "Multiple reminders matched. Ask the user to choose one.",
                    "candidates": [self.serialize_reminder(item) for item in matches],
                }
            reminder = matches[0]
        else:
            return {"status": "error", "message": "reminder_id or query is required"}

        assert reminder is not None

        if reminder.recurrence_type != "none" and not delete_scope:
            return {
                "status": "needs_confirmation",
                "message": "This is a recurring reminder. Ask whether to delete this occurrence or the entire series.",
                "reminder": self.serialize_reminder(reminder),
            }

        if reminder.recurrence_type != "none" and delete_scope == "single":
            recurrence = RecurrenceConfig.from_dict(recurrence_rule_from_text(reminder.recurrence_rule))
            tzinfo = ZoneInfo(reminder.timezone)
            due_local = reminder.next_fire_at
            if due_local.tzinfo is None:
                due_local = due_local.replace(tzinfo=tzinfo)
            else:
                due_local = due_local.astimezone(tzinfo)
            reminder.next_fire_at = compute_next_fire_at(
                due_at_local=due_local,
                recurrence=recurrence,
                reference_utc=ensure_aware(reminder.next_fire_at),
            )
            self.session.flush()
            return {
                "status": "ok",
                "deleted_scope": "single",
                "reminder": self.serialize_reminder(reminder),
            }

        reminder.status = "canceled"
        reminder.canceled_at = utcnow()
        self.session.flush()
        return {
            "status": "ok",
            "deleted_scope": "series" if reminder.recurrence_type != "none" else "single",
            "reminder": self.serialize_reminder(reminder),
        }

    def claim_due_reminders(self, *, limit: int) -> list[Reminder]:
        now = utcnow()
        statement: Select[tuple[Reminder]] = (
            select(Reminder)
            .where(
                or_(
                    and_(Reminder.status == "scheduled", Reminder.next_fire_at <= now),
                    and_(
                        Reminder.status == "pending",
                        Reminder.next_attempt_at.is_not(None),
                        Reminder.next_attempt_at <= now,
                    ),
                )
            )
            .order_by(Reminder.next_fire_at.asc())
            .limit(limit)
        )
        dialect = self.session.bind.dialect.name if self.session.bind is not None else ""
        if dialect == "postgresql":
            statement = statement.with_for_update(skip_locked=True)
        reminders = list(self.session.scalars(statement))
        for reminder in reminders:
            reminder.status = "processing"
        self.session.flush()
        return reminders

    def mark_delivery_result(self, *, reminder: Reminder, success: bool, error: Optional[str] = None) -> None:
        now = utcnow()
        attempt_count = reminder.attempt_count + 1
        next_attempt_at = None

        if not success and attempt_count < reminder.max_attempts:
            next_attempt_at = now + RETRY_DELAY

        delivery = ReminderDelivery(
            reminder_id=reminder.id,
            platform=reminder.source_platform,
            target_chat_id=reminder.source_chat_id,
            status="sent" if success else "failed",
            attempt_count=attempt_count,
            last_error=error,
            next_attempt_at=next_attempt_at,
            max_attempts=reminder.max_attempts,
            delivered_at=now if success else None,
        )
        self.session.add(delivery)
        if success:
            reminder.attempt_count = 0
            reminder.last_error = None
            reminder.next_attempt_at = None
            if reminder.recurrence_type == "none":
                reminder.status = "sent"
            else:
                recurrence = RecurrenceConfig.from_dict(recurrence_rule_from_text(reminder.recurrence_rule))
                tzinfo = ZoneInfo(reminder.timezone)
                due_local = reminder.next_fire_at
                if due_local.tzinfo is None:
                    due_local = due_local.replace(tzinfo=tzinfo)
                else:
                    due_local = due_local.astimezone(tzinfo)
                reminder.next_fire_at = compute_next_fire_at(
                    due_at_local=due_local,
                    recurrence=recurrence,
                    reference_utc=ensure_aware(reminder.next_fire_at),
                )
                reminder.status = "scheduled"
        else:
            reminder.attempt_count = attempt_count
            reminder.last_error = error
            reminder.next_attempt_at = next_attempt_at
            if attempt_count < reminder.max_attempts:
                reminder.status = "pending"
            else:
                reminder.status = "failed"
        self.session.flush()

    def build_notification_text(self, reminder: Reminder) -> str:
        return f"리마인더: {reminder.title}"

    def serialize_reminder(self, reminder: Reminder) -> dict:
        return {
            "id": reminder.id,
            "title": reminder.title,
            "timezone": reminder.timezone,
            "due_at": ensure_aware(reminder.due_at).isoformat(),
            "next_fire_at": ensure_aware(reminder.next_fire_at).isoformat(),
            "status": reminder.status,
            "attempt_count": reminder.attempt_count,
            "last_error": reminder.last_error,
            "next_attempt_at": ensure_aware(reminder.next_attempt_at).isoformat() if reminder.next_attempt_at else None,
            "max_attempts": reminder.max_attempts,
            "recurrence_type": reminder.recurrence_type,
            "recurrence_rule": recurrence_rule_from_text(reminder.recurrence_rule),
            "notes": reminder.notes,
        }

    def _active_reminders_statement(self, user_id: str) -> Select[tuple[Reminder]]:
        return (
            select(Reminder)
            .where(
                Reminder.user_id == user_id,
                Reminder.status.in_(["scheduled", "pending", "processing", "failed"]),
            )
            .order_by(Reminder.next_fire_at.asc())
        )
