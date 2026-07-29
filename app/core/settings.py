from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    value = os.getenv(name)
    if value is None:
        return default
    return value


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    return int(raw)


@dataclass
class Settings:
    app_name: str = _env("APP_NAME", "personal-ai-assistant") or "personal-ai-assistant"
    app_base_url: str = _env("APP_BASE_URL", "https://example.com") or "https://example.com"
    environment: str = _env("APP_ENV", "development") or "development"
    database_url: str = _env("DATABASE_URL", "sqlite:///./assistant.db") or "sqlite:///./assistant.db"
    nvidia_api_key: Optional[str] = _env("NVIDIA_API_KEY")
    nvidia_model: str = _env(
        "NVIDIA_MODEL", "nvidia/nemotron-3-ultra-550b-a55b"
    ) or "nvidia/nemotron-3-ultra-550b-a55b"
    telegram_bot_token: Optional[str] = _env("TELEGRAM_BOT_TOKEN")
    telegram_webhook_secret: str = _env("TELEGRAM_WEBHOOK_SECRET", "change-me") or "change-me"
    telegram_webhook_key: str = _env("TELEGRAM_WEBHOOK_KEY", "telegram-webhook-key") or "telegram-webhook-key"
    google_client_id: Optional[str] = _env("GOOGLE_CLIENT_ID")
    google_client_secret: Optional[str] = _env("GOOGLE_CLIENT_SECRET")
    google_redirect_uri: str = _env("GOOGLE_REDIRECT_URI", "https://example.com/auth/google/callback") or "https://example.com/auth/google/callback"
    google_oauth_scopes: str = _env(
        "GOOGLE_OAUTH_SCOPES",
        "openid email profile https://www.googleapis.com/auth/calendar.events",
    ) or "openid email profile https://www.googleapis.com/auth/calendar.events"
    default_timezone: str = _env("DEFAULT_TIMEZONE", "Asia/Seoul") or "Asia/Seoul"
    summary_trigger_messages: int = _int_env("SUMMARY_TRIGGER_MESSAGES", 10)
    recent_message_window: int = _int_env("RECENT_MESSAGE_WINDOW", 8)
    worker_poll_seconds: int = _int_env("WORKER_POLL_SECONDS", 5)
    reminder_batch_size: int = _int_env("REMINDER_BATCH_SIZE", 20)
    encryption_key: str = _env("APP_ENCRYPTION_KEY", "dev-encryption-key-change-me") or "dev-encryption-key-change-me"
    log_sql: bool = _bool_env("LOG_SQL", False)

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


def get_settings() -> Settings:
    return Settings()
