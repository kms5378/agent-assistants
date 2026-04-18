from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


REQUIRED_PERSONA_FIELDS = (
    "name",
    "tone_rules",
    "style_examples",
    "response_length_rules",
    "disallowed_phrases",
    "safety_disclaimer",
)

OPERATING_RULES = (
    "Use reminder and calendar tools when they are needed.",
    "If a reminder time is incomplete or ambiguous, ask a follow-up question instead of calling a tool.",
    "When deleting reminders, do not assume which reminder to delete if there are multiple matches.",
    "If Google Calendar is not connected, explain that the user needs to connect Google and include the provided link.",
)


@dataclass(frozen=True)
class PersonaProfile:
    name: str
    tone_rules: tuple[str, ...]
    style_examples: tuple[str, ...]
    response_length_rules: tuple[str, ...]
    disallowed_phrases: tuple[str, ...]
    safety_disclaimer: str

    def build_system_prompt(self, *, platform: str) -> str:
        sections = [
            f"You are {self.name}, a personal assistant serving the {platform} channel.",
            "Keep the behavior compatible with future channel adapters such as Discord.",
            _render_bullets("Tone rules", self.tone_rules),
            _render_bullets("Response length rules", self.response_length_rules),
            _render_bullets("Style examples", self.style_examples),
            _render_bullets("Disallowed phrases", self.disallowed_phrases),
            f"Safety disclaimer:\n- {self.safety_disclaimer}",
            _render_bullets("Operating rules", OPERATING_RULES),
        ]
        return "\n\n".join(sections)


def load_persona_profile(path: str | Path) -> PersonaProfile:
    resolved = Path(path).expanduser()
    if not resolved.is_absolute():
        resolved = Path.cwd() / resolved

    with resolved.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}

    if not isinstance(raw, dict):
        raise ValueError(f"Persona profile must be a mapping: {resolved}")

    missing = [field for field in REQUIRED_PERSONA_FIELDS if field not in raw]
    if missing:
        raise ValueError(f"Persona profile is missing required fields: {', '.join(missing)}")

    return PersonaProfile(
        name=_read_text(raw, "name"),
        tone_rules=_read_text_list(raw, "tone_rules"),
        style_examples=_read_text_list(raw, "style_examples"),
        response_length_rules=_read_text_list(raw, "response_length_rules"),
        disallowed_phrases=_read_text_list(raw, "disallowed_phrases"),
        safety_disclaimer=_read_text(raw, "safety_disclaimer"),
    )


def _read_text(raw: dict[str, Any], field: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Persona field '{field}' must be a non-empty string.")
    return value.strip()


def _read_text_list(raw: dict[str, Any], field: str) -> tuple[str, ...]:
    value = raw.get(field)
    if not isinstance(value, list):
        raise ValueError(f"Persona field '{field}' must be a list of strings.")

    items: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"Persona field '{field}' must contain non-empty strings only.")
        items.append(item.strip())
    return tuple(items)


def _render_bullets(title: str, items: tuple[str, ...]) -> str:
    lines = [f"{title}:"]
    for item in items:
        lines.append(f"- {item}")
    return "\n".join(lines)
