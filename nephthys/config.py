"""Runtime configuration, read from environment variables.

Every variable is documented in `.env.example`. Settings are loaded lazily
(via `get_settings()`) so that importing modules - e.g. in tests - doesn't
require a fully configured environment.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from zoneinfo import ZoneInfo
from zoneinfo import ZoneInfoNotFoundError

from dotenv import load_dotenv

VALID_ARCHIVE_DURATIONS = (60, 1440, 4320, 10080)

DEFAULT_CATEGORIES = ("Gameplay", "Bug", "Claims", "Economy", "Store", "Other")
AI_ANSWER_MODES = ("off", "shadow", "public")
DEFAULT_AI_BASE_URL = "https://ai.hackclub.com/proxy/v1"

DEFAULT_COPY_FILE = "copy/copy.toml"
DEFAULT_TICKET_CATEGORIES_FILE = "copy/ticket_categories.toml"
# Env vars that used to hold text; text now lives in copy/copy.toml
DEPRECATED_TEXT_ENV = ("STICKY_TITLE", "STICKY_BODY")


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
    # #tickets (Phase 2): disabled when tickets_channel_id is unset
    tickets_channel_id: int | None
    store_role_id: int | None
    mod_role_id: int | None
    ticket_number_start: int
    tickets_max_open_per_user: int
    ticket_categories_file: str
    # Closed #tickets threads: lock right away (player can't post), archive
    # when the opener rates or after csat_timeout_hours
    tickets_lock_on_close: bool
    csat_timeout_hours: float
    ticket_categories_file_explicit: bool
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
    # Message text (copy/copy.toml)
    copy_file: str
    copy_file_explicit: bool
    deprecated_env_set: tuple[str, ...]
    timezone: str

    # Categories (#community-help)
    ticket_categories: tuple[str, ...]

    # Daily summary
    daily_summary_enabled: bool
    daily_summary_hour: int
    daily_summary_minute: int
    daily_summary_timezone: str

    # AI (optional: disabled when ai_api_key is unset)
    ai_api_key: str | None
    ai_base_url: str
    ai_model: str | None
    ai_categorize: bool
    ai_answer_mode: str  # off | shadow | public
    ai_answer_min_confidence: float
    ai_timeout_seconds: float
    kb_dir: str

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

    @property
    def tickets_enabled(self) -> bool:
        return self.tickets_channel_id is not None

    def role_id_for(self, role: str) -> int:
        """Resolve a ticket category's role ("staff" | "store" | "mod" | ID).
        Store/Mod fall back to the Staff role when their IDs aren't set."""
        if role.isdigit():
            return int(role)
        if role == "store" and self.store_role_id:
            return self.store_role_id
        if role == "mod" and self.mod_role_id:
            return self.mod_role_id
        return self.staff_role_id

    @property
    def ai_enabled(self) -> bool:
        return bool(self.ai_api_key and self.ai_model)

    @property
    def ai_categorize_enabled(self) -> bool:
        return self.ai_enabled and self.ai_categorize

    @property
    def ai_answers_enabled(self) -> bool:
        return self.ai_enabled and self.ai_answer_mode != "off"


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

    categories_raw = get("TICKET_CATEGORIES")
    categories = (
        tuple(dict.fromkeys(c.strip() for c in categories_raw.split(",") if c.strip()))
        if categories_raw
        else DEFAULT_CATEGORIES
    )
    if not 1 <= len(categories) <= 25:
        # Discord select menus allow at most 25 options
        errors.append("TICKET_CATEGORIES must list between 1 and 25 categories")
    if any(len(c) > 100 for c in categories):
        errors.append("Each TICKET_CATEGORIES entry must be at most 100 characters")

    summary_time = get("DAILY_SUMMARY_TIME", "09:00") or "09:00"
    summary_hour, summary_minute = 9, 0
    try:
        hh, mm = summary_time.split(":")
        summary_hour, summary_minute = int(hh), int(mm)
        if not (0 <= summary_hour <= 23 and 0 <= summary_minute <= 59):
            raise ValueError
    except ValueError:
        errors.append(f"DAILY_SUMMARY_TIME must be HH:MM (24h), got {summary_time!r}")
    summary_tz = get("DAILY_SUMMARY_TIMEZONE", "America/Phoenix") or "America/Phoenix"
    try:
        ZoneInfo(summary_tz)
    except (ZoneInfoNotFoundError, ValueError):
        errors.append(f"DAILY_SUMMARY_TIMEZONE is not a known timezone: {summary_tz!r}")

    ai_api_key = get("AI_API_KEY")
    ai_model = get("AI_MODEL")
    if ai_api_key and not ai_model:
        errors.append("AI_MODEL is required when AI_API_KEY is set")
    ai_mode = (get("AI_ANSWER_MODE", "shadow") or "shadow").lower()
    if ai_mode not in AI_ANSWER_MODES:
        errors.append(
            f"AI_ANSWER_MODE must be one of {AI_ANSWER_MODES}, got {ai_mode!r}"
        )
    min_conf_raw = get("AI_ANSWER_MIN_CONFIDENCE", "0.8") or "0.8"
    try:
        min_conf = float(min_conf_raw)
        if not 0 <= min_conf <= 1:
            raise ValueError
    except ValueError:
        errors.append(
            f"AI_ANSWER_MIN_CONFIDENCE must be between 0 and 1, got {min_conf_raw!r}"
        )
        min_conf = 0.8
    ai_timeout_raw = get("AI_TIMEOUT_SECONDS", "30") or "30"
    try:
        ai_timeout = max(1.0, float(ai_timeout_raw))
    except ValueError:
        errors.append(f"AI_TIMEOUT_SECONDS must be a number, got {ai_timeout_raw!r}")
        ai_timeout = 30.0

    ticket_number_start = integer("TICKET_NUMBER_START", 26)
    if ticket_number_start is None or ticket_number_start < 1:
        errors.append("TICKET_NUMBER_START must be at least 1")
        ticket_number_start = 26
    csat_timeout_raw = get("CSAT_TIMEOUT_HOURS", "24") or "24"
    try:
        csat_timeout = float(csat_timeout_raw)
        if csat_timeout <= 0:
            raise ValueError
    except ValueError:
        errors.append(
            f"CSAT_TIMEOUT_HOURS must be a positive number, got {csat_timeout_raw!r}"
        )
        csat_timeout = 24.0
    max_open = integer("TICKETS_MAX_OPEN_PER_USER", 2)
    if max_open is None or max_open < 0:
        errors.append("TICKETS_MAX_OPEN_PER_USER must be 0 (unlimited) or more")
        max_open = 2

    # OPEN_REACTION may be explicitly set to empty to disable the 🤔 marker
    open_reaction = env.get("OPEN_REACTION", "🤔").strip() or None

    settings = Settings(
        discord_token=required("DISCORD_TOKEN"),
        guild_id=snowflake("GUILD_ID") or 0,
        community_channel_id=snowflake("COMMUNITY_CHANNEL_ID") or 0,
        staff_role_id=snowflake("STAFF_ROLE_ID") or 0,
        staff_channel_id=snowflake("STAFF_CHANNEL_ID", optional=True),
        tickets_channel_id=snowflake("TICKETS_CHANNEL_ID", optional=True),
        store_role_id=snowflake("STORE_ROLE_ID", optional=True),
        mod_role_id=snowflake("MOD_ROLE_ID", optional=True),
        ticket_number_start=ticket_number_start,
        tickets_max_open_per_user=max_open,
        ticket_categories_file=get(
            "TICKET_CATEGORIES_FILE", DEFAULT_TICKET_CATEGORIES_FILE
        )
        or DEFAULT_TICKET_CATEGORIES_FILE,
        ticket_categories_file_explicit=get("TICKET_CATEGORIES_FILE") is not None,
        tickets_lock_on_close=boolean("TICKETS_LOCK_ON_CLOSE", True),
        csat_timeout_hours=csat_timeout,
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
        copy_file=get("COPY_FILE", DEFAULT_COPY_FILE) or DEFAULT_COPY_FILE,
        copy_file_explicit=get("COPY_FILE") is not None,
        deprecated_env_set=tuple(n for n in DEPRECATED_TEXT_ENV if get(n) is not None),
        timezone=get("TIMEZONE", "Europe/London") or "Europe/London",
        ticket_categories=categories,
        daily_summary_enabled=boolean("DAILY_SUMMARY_ENABLED", True),
        daily_summary_hour=summary_hour,
        daily_summary_minute=summary_minute,
        daily_summary_timezone=summary_tz,
        ai_api_key=ai_api_key,
        ai_base_url=(
            get("AI_BASE_URL", DEFAULT_AI_BASE_URL) or DEFAULT_AI_BASE_URL
        ).rstrip("/"),
        ai_model=ai_model,
        ai_categorize=boolean("AI_CATEGORIZE", True),
        ai_answer_mode=ai_mode,
        ai_answer_min_confidence=min_conf,
        ai_timeout_seconds=ai_timeout,
        kb_dir=get("KB_DIR", "kb") or "kb",
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
