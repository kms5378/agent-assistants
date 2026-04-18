from __future__ import annotations

import calendar
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo


class TimeValidationError(ValueError):
    """Raised when a time expression cannot be normalized."""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def ensure_aware(dt_value: datetime, fallback_tz: timezone = timezone.utc) -> datetime:
    if dt_value.tzinfo is None:
        return dt_value.replace(tzinfo=fallback_tz)
    return dt_value


def ensure_zoneinfo(tz_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name)
    except Exception as exc:  # pragma: no cover - defensive guard
        raise TimeValidationError(f"Unknown timezone: {tz_name}") from exc


def parse_local_datetime(value: str, tz_name: str) -> datetime:
    if not value:
        raise TimeValidationError("due_at_local is required")
    zone = ensure_zoneinfo(tz_name)
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise TimeValidationError("due_at_local must be ISO 8601") from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=zone)
    return parsed.astimezone(zone)


def to_utc(dt_value: datetime) -> datetime:
    if dt_value.tzinfo is None:
        raise TimeValidationError("datetime must be timezone aware")
    return dt_value.astimezone(timezone.utc)


def recurrence_rule_to_text(rule: Optional[dict]) -> Optional[str]:
    if not rule:
        return None
    return json.dumps(rule, ensure_ascii=True, separators=(",", ":"))


def recurrence_rule_from_text(raw: Optional[str]) -> dict:
    if not raw:
        return {}
    return json.loads(raw)


@dataclass
class RecurrenceConfig:
    type: str = "none"
    days_of_week: Optional[list[int]] = None
    day_of_month: Optional[int] = None
    local_time: Optional[str] = None

    @classmethod
    def from_dict(cls, value: Optional[dict]) -> "RecurrenceConfig":
        if value is None:
            return cls()
        return cls(
            type=(value.get("type") or "none").lower(),
            days_of_week=value.get("days_of_week"),
            day_of_month=value.get("day_of_month"),
            local_time=value.get("local_time"),
        )

    def as_dict(self) -> dict:
        return {
            "type": self.type,
            "days_of_week": self.days_of_week,
            "day_of_month": self.day_of_month,
            "local_time": self.local_time,
        }


def _parse_clock(value: Optional[str], fallback: datetime) -> tuple[int, int]:
    if not value:
        return fallback.hour, fallback.minute
    try:
        hour_str, minute_str = value.split(":")
        hour = int(hour_str)
        minute = int(minute_str)
    except Exception as exc:  # pragma: no cover - defensive guard
        raise TimeValidationError("local_time must be HH:MM") from exc
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise TimeValidationError("local_time must be HH:MM")
    return hour, minute


def compute_next_fire_at(
    *,
    due_at_local: datetime,
    recurrence: RecurrenceConfig,
    reference_utc: Optional[datetime] = None,
) -> datetime:
    if due_at_local.tzinfo is None:
        raise TimeValidationError("due_at_local must be timezone aware")

    reference_local = (
        due_at_local
        if reference_utc is None
        else ensure_aware(reference_utc).astimezone(due_at_local.tzinfo)
    )

    if recurrence.type == "none":
        return to_utc(due_at_local)

    if recurrence.type == "daily":
        candidate = due_at_local
        while candidate <= reference_local:
            candidate += timedelta(days=1)
        return to_utc(candidate)

    if recurrence.type == "weekly":
        days = recurrence.days_of_week or [due_at_local.weekday()]
        hour, minute = _parse_clock(recurrence.local_time, due_at_local)
        base_date = reference_local.date()
        for offset in range(0, 14):
            candidate_date = base_date + timedelta(days=offset)
            if candidate_date.weekday() not in days:
                continue
            candidate = datetime(
                candidate_date.year,
                candidate_date.month,
                candidate_date.day,
                hour,
                minute,
                tzinfo=due_at_local.tzinfo,
            )
            if candidate > reference_local:
                return to_utc(candidate)
        raise TimeValidationError("Unable to compute weekly next fire time")

    if recurrence.type == "monthly":
        target_day = recurrence.day_of_month or due_at_local.day
        hour, minute = _parse_clock(recurrence.local_time, due_at_local)
        year = reference_local.year
        month = reference_local.month
        for _ in range(0, 13):
            last_day = calendar.monthrange(year, month)[1]
            day = min(target_day, last_day)
            candidate = datetime(year, month, day, hour, minute, tzinfo=due_at_local.tzinfo)
            if candidate > reference_local:
                return to_utc(candidate)
            month += 1
            if month == 13:
                month = 1
                year += 1
        raise TimeValidationError("Unable to compute monthly next fire time")

    raise TimeValidationError(f"Unsupported recurrence type: {recurrence.type}")
