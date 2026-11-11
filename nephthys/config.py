"""Runtime configuration, read from environment variables.

Every variable is documented in `.env.example`. Settings are loaded lazily
(via `get_settings()`) so that importing modules - e.g. in tests - doesn't
require a fully configured environment.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache

from dotenv import load_dotenv

VALID_ARCHIVE_DURATIONS = (60, 1440, 4320, 10080)

DEFAULT_STICKY_TITLE = "How #community-help works"
DEFAULT_STICKY_BODY = (
    "**Post your question as a new message in this channel** and a thread will be "
    "opened for it. Anyone can help out, and staff will be along soon!\n\n"
    "• Keep all follow-ups inside your thread.\n"
    "• Once you're sorted, hit **Mark as resolved** in your thread.\n"
    "• Got a new question later? Just post a new message.\n\n"
    "Before posting, check the FAQ: {faq_url}"
)


class ConfigError(ValueError):
    """Raised when required environment variables are missing or invalid."""


@dataclass(frozen=True)
class Settings:
    # Discord
    discord_token: str
    guild_id: int
    community_channel_id: int
    staff_role_id: int
    staff_channel_id: int | None
    log_channel_id: int | None
    maintainer_id: int | None

    # Database
    database_url: str

    # Behaviour
    faq_url: str
    server_address: str
    open_reaction: str | None
    resolved_reaction: str
    thread_auto_archive_minutes: int
    stale_ticket_days: int | None
    sticky_enabled: bool
    sticky_debounce_seconds: float
    sticky_title: str
    sticky_body: str
    timezone: str

    # Runtime
    environment: str
    port: int
    log_level: str
    otel_logs_url: str | None
    otel_service_name: str
    otel_logs_basic_auth: str | None
    log_level_otel: str

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


def _parse_bool(name: str, value: str) -> bool:
    v = value.strip().lower()
    if v in {"1", "true", "t", "yes", "y", "on"}:
        return True
    if v in {"0", "false", "f", "no", "n", "off"}:
        return False
    raise ConfigError(f"{name} must be a boolean, got {value!r}")


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    """Build `Settings` from a mapping (defaults to `os.environ`).

    All problems are collected and reported together, so a misconfigured
    deployment shows every missing variable at once.
    """
    if environ is None:
        load_dotenv()
        environ = os.environ
    env = environ
    errors: list[str] = []

    def get(name: str, default: str | None = None) -> str | None:
        value = env.get(name)
        if value is None or value.strip() == "":
            return default
        return value.strip()

    def required(name: str) -> str:
        value = get(name)
        if value is None:
            errors.append(f"{name} is required")
            return ""
        return value

    def snowflake(name: str, *, optional: bool = False) -> int | None:
        raw = get(name) if optional else required(name)
        if not raw:
            return None
        if not raw.isdigit():
            errors.append(f"{name} must be a numeric Discord ID, got {raw!r}")
            return None
        return int(raw)

    def integer(name: str, default: int | None) -> int | None:
        raw = get(name)
        if raw is None:
            return default
        try:
            return int(raw)
        except ValueError:
            errors.append(f"{name} must be an integer, got {raw!r}")
            return default

    def boolean(name: str, default: bool) -> bool:
        raw = get(name)
        if raw is None:
            return default
        try:
            return _parse_bool(name, raw)
        except ConfigError as e:
            errors.append(str(e))
            return default

    archive = integer("THREAD_AUTO_ARCHIVE_MINUTES", 10080) or 10080
    if archive not in VALID_ARCHIVE_DURATIONS:
        errors.append(
            f"THREAD_AUTO_ARCHIVE_MINUTES must be one of {VALID_ARCHIVE_DURATIONS}, got {archive}"
        )

    stale = integer("STALE_TICKET_DAYS", None)
    if stale is not None and stale <= 0:
        stale = None

    debounce_raw = get("STICKY_DEBOUNCE_SECONDS", "5") or "5"
    try:
        debounce = max(0.0, float(debounce_raw))
    except ValueError:
        errors.append(f"STICKY_DEBOUNCE_SECONDS must be a number, got {debounce_raw!r}")
        debounce = 5.0

    # OPEN_REACTION may be explicitly set to empty to disable the 🤔 marker
    open_reaction = env.get("OPEN_REACTION", "🤔").strip() or None

    settings = Settings(
        discord_token=required("DISCORD_TOKEN"),
        guild_id=snowflake("GUILD_ID") or 0,
        community_channel_id=snowflake("COMMUNITY_CHANNEL_ID") or 0,
        staff_role_id=snowflake("STAFF_ROLE_ID") or 0,
        staff_channel_id=snowflake("STAFF_CHANNEL_ID", optional=True),
        log_channel_id=snowflake("LOG_CHANNEL_ID", optional=True),
        maintainer_id=snowflake("MAINTAINER_ID", optional=True),
        database_url=required("DATABASE_URL"),
        faq_url=get("FAQ_URL", "") or "",
        server_address=get("SERVER_ADDRESS", "") or "",
        open_reaction=open_reaction,
        resolved_reaction=get("RESOLVED_REACTION", "✅") or "✅",
        thread_auto_archive_minutes=archive,
        stale_ticket_days=stale,
        sticky_enabled=boolean("STICKY_ENABLED", True),
        sticky_debounce_seconds=debounce,
        sticky_title=get("STICKY_TITLE", DEFAULT_STICKY_TITLE) or DEFAULT_STICKY_TITLE,
        sticky_body=(get("STICKY_BODY", DEFAULT_STICKY_BODY) or DEFAULT_STICKY_BODY)
        # Allow literal "\n" in .env files for multi-line bodies
        .replace("\\n", "\n"),
        timezone=get("TIMEZONE", "Europe/London") or "Europe/London",
        environment=get("ENVIRONMENT", "development") or "development",
        port=integer("PORT", 3000) or 3000,
        log_level=get("LOG_LEVEL", "INFO") or "INFO",
        otel_logs_url=get("OTEL_EXPORTER_OTLP_LOGS_ENDPOINT"),
        otel_service_name=get("OTEL_SERVICE_NAME", "cloudsail-support")
        or "cloudsail-support",
        otel_logs_basic_auth=get("OTEL_EXPORTER_OTLP_LOGS_BASIC_AUTH"),
        log_level_otel=get("LOG_LEVEL_OTEL", "INFO") or "INFO",
    )
    if errors:
        raise ConfigError("Invalid configuration:\n  - " + "\n  - ".join(errors))
    return settings


@cache
def get_settings() -> Settings:
    return load_settings()
