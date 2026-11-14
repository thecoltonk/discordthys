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
FAQ_MODES = ("off", "shadow", "public")
# Jev, TypeSafe's decision model, served by Hack Club AI (POST <base>/systemone)
DEFAULT_JEV_BASE_URL = "https://ai.hackclub.com/proxy/v1/jev"
DEFAULT_JEV_MODEL = "jev-latest"

# The bot's service alias on Zammad's Docker network (docker-compose.zammad.yml)
DEFAULT_ZAMMAD_WEBHOOK_URL = "http://cloudsail-support-bot:3000/zammad/webhook"

DEFAULT_COPY_FILE = "copy/copy.toml"
DEFAULT_COPY_OVERRIDE_FILE = "copy/copy.local.toml"
DEFAULT_TICKET_CATEGORIES_FILE = "copy/ticket_categories.toml"
# Env vars that used to hold text; text now lives in copy/copy.toml
DEPRECATED_TEXT_ENV = ("STICKY_TITLE", "STICKY_BODY")
# Env vars whose feature was removed. They're never read, so a .env that still
# has them (with any value) starts fine; startup logs that they're ignored.
REMOVED_ENV: dict[str, str] = {
    "CSAT_TIMEOUT_HOURS": "CSAT ratings were removed",
    "TICKETS_LOCK_ON_CLOSE": "closed #tickets threads are always locked and archived",
    "STORE_ROLE_ID": "staff alerts no longer ping roles",
    "MOD_ROLE_ID": "staff alerts no longer ping roles",
    "AI_MODEL": "the generative assistant was replaced by the Jev FAQ matcher (JEV_MODEL)",
    "AI_BASE_URL": "the generative assistant was replaced by the Jev FAQ matcher (JEV_BASE_URL)",
    "AI_ANSWER_MODE": "replaced by FAQ_MODE",
    "AI_ANSWER_MIN_CONFIDENCE": "replaced by FAQ_THRESHOLD",
}


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
    ticket_number_start: int
    tickets_max_open_per_user: int
    ticket_categories_file: str
    ticket_categories_file_explicit: bool
    log_channel_id: int | None
    maintainer_id: int | None
    # Post "online" to the log channel at startup (@silent)
    log_startup_notice: bool

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
    # Local overrides on top of copy_file (gitignored; any key wins)
    copy_override_file: str
    copy_override_file_explicit: bool
    deprecated_env_set: tuple[str, ...]
    # Removed variables still present in the environment (ignored, logged)
    removed_env_set: tuple[str, ...]
    timezone: str

    # Categories (#community-help)
    ticket_categories: tuple[str, ...]

    # Daily summary
    daily_summary_enabled: bool
    daily_summary_hour: int
    daily_summary_minute: int
    daily_summary_timezone: str

    # Jev (optional: everything below is off when ai_api_key is unset). It
    # only ever picks between pre-written options; it never writes text.
    ai_api_key: str | None
    jev_base_url: str
    jev_model: str
    ai_categorize: bool
    ai_timeout_seconds: float
    # FAQ matcher: off | shadow (matches go to the staff channel) | public
    faq_mode: str
    # Minimum confidence to answer a new #community-help ticket
    faq_threshold: float
    # Other channels (#general etc.) watched for questions the FAQ answers
    faq_watch_channel_ids: tuple[int, ...]
    # Minimum confidence there (both "it's a real question" and the match)
    faq_watch_threshold: float
    # Also point confident support questions with no FAQ match to #community-help
    faq_watch_pointer: bool
    faq_user_cooldown_seconds: float
    faq_channel_cooldown_seconds: float
    # The FAQ entries (one file each)
    kb_dir: str

    # Zammad two-way sync for #tickets (off unless url + token are both set)
    zammad_url: str | None
    zammad_token: str | None
    # Admin-capable token used only by `nephthys zammad-setup` (falls back to
    # zammad_token)
    zammad_setup_token: str | None
    # Optional: a token of a separate Zammad user whose role grants admin.user,
    # used only to set customers' avatars (POST /users/avatar with "From").
    # Unset = names are still kept in sync, avatars aren't.
    zammad_avatar_token: str | None
    # Zammad as browsers (and Discord's image proxy) reach it: ticket links on
    # staff cards, agent avatars
    zammad_public_url: str | None
    zammad_webhook_secret: str | None
    # Where Zammad's webhook calls the bot (registered by zammad-setup)
    zammad_webhook_url: str
    zammad_default_avatar_url: str | None
    zammad_attachment_max_mb: float
    zammad_timeout_seconds: float
    zammad_outbox_max_attempts: int
    zammad_reconcile_minutes: int

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

    @property
    def zammad_enabled(self) -> bool:
        return bool(self.zammad_url and self.zammad_token and self.tickets_enabled)

    @property
    def ai_enabled(self) -> bool:
        return bool(self.ai_api_key)

    @property
    def ai_categorize_enabled(self) -> bool:
        return self.ai_enabled and self.ai_categorize

    @property
    def faq_enabled(self) -> bool:
        return self.ai_enabled and self.faq_mode != "off"

    @property
    def faq_public(self) -> bool:
        return self.faq_mode == "public"

    @property
    def faq_watch_enabled(self) -> bool:
        return self.faq_enabled and bool(self.faq_watch_channel_ids)


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

    def fraction(name: str, default: float) -> float:
        raw = get(name)
        if raw is None:
            return default
        try:
            value = float(raw)
            if not 0 <= value <= 1:
                raise ValueError
        except ValueError:
            errors.append(f"{name} must be between 0 and 1, got {raw!r}")
            return default
        return value

    ai_api_key = get("AI_API_KEY")
    faq_mode = (get("FAQ_MODE", "shadow") or "shadow").lower()
    if faq_mode not in FAQ_MODES:
        errors.append(f"FAQ_MODE must be one of {FAQ_MODES}, got {faq_mode!r}")
    watch_ids: list[int] = []
    for part in (get("FAQ_WATCH_CHANNEL_IDS") or "").split(","):
        part = part.strip()
        if not part:
            continue
        if not part.isdigit():
            errors.append(
                "FAQ_WATCH_CHANNEL_IDS must be comma-separated numeric channel IDs, "
                f"got {part!r}"
            )
            continue
        if int(part) not in watch_ids:
            watch_ids.append(int(part))
    # Watching a channel the bot already runs would answer its own surfaces
    for name in (
        "COMMUNITY_CHANNEL_ID",
        "TICKETS_CHANNEL_ID",
        "STAFF_CHANNEL_ID",
        "LOG_CHANNEL_ID",
    ):
        own = get(name)
        if own and own.isdigit() and int(own) in watch_ids:
            errors.append(f"FAQ_WATCH_CHANNEL_IDS must not include {name} ({own})")
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
    max_open = integer("TICKETS_MAX_OPEN_PER_USER", 2)
    if max_open is None or max_open < 0:
        errors.append("TICKETS_MAX_OPEN_PER_USER must be 0 (unlimited) or more")
        max_open = 2

    def number(name: str, default: float, *, minimum: float) -> float:
        raw = get(name)
        if raw is None:
            return default
        try:
            value = float(raw)
            if value < minimum:
                raise ValueError
        except ValueError:
            errors.append(f"{name} must be a number >= {minimum:g}, got {raw!r}")
            return default
        return value

    def http_url(name: str) -> str | None:
        raw = get(name)
        if raw is None:
            return None
        if not raw.lower().startswith(("http://", "https://")):
            errors.append(f"{name} must start with http:// or https://, got {raw!r}")
            return None
        return raw.rstrip("/")

    zammad_url = http_url("ZAMMAD_URL")
    zammad_token = get("ZAMMAD_TOKEN")
    zammad_secret = get("ZAMMAD_WEBHOOK_SECRET")
    if zammad_url and zammad_token:
        if get("TICKETS_CHANNEL_ID") is None:
            errors.append(
                "TICKETS_CHANNEL_ID is required when ZAMMAD_URL and ZAMMAD_TOKEN are "
                "set (the Zammad sync mirrors #tickets)"
            )
        if not zammad_secret:
            errors.append(
                "ZAMMAD_WEBHOOK_SECRET is required when ZAMMAD_URL and ZAMMAD_TOKEN "
                "are set (it signs Zammad's webhook calls to the bot)"
            )
        elif len(zammad_secret) < 16:
            errors.append("ZAMMAD_WEBHOOK_SECRET must be at least 16 characters")
    zammad_max_attempts = integer("ZAMMAD_OUTBOX_MAX_ATTEMPTS", 25) or 25
    if zammad_max_attempts < 1:
        errors.append("ZAMMAD_OUTBOX_MAX_ATTEMPTS must be at least 1")
        zammad_max_attempts = 25
    zammad_reconcile = integer("ZAMMAD_RECONCILE_MINUTES", 5)
    if zammad_reconcile is None or zammad_reconcile < 0:
        errors.append("ZAMMAD_RECONCILE_MINUTES must be 0 (off) or more")
        zammad_reconcile = 5

    # OPEN_REACTION may be explicitly set to empty to disable the 🤔 marker
    open_reaction = env.get("OPEN_REACTION", "🤔").strip() or None

    settings = Settings(
        discord_token=required("DISCORD_TOKEN"),
        guild_id=snowflake("GUILD_ID") or 0,
        community_channel_id=snowflake("COMMUNITY_CHANNEL_ID") or 0,
        staff_role_id=snowflake("STAFF_ROLE_ID") or 0,
        staff_channel_id=snowflake("STAFF_CHANNEL_ID", optional=True),
        tickets_channel_id=snowflake("TICKETS_CHANNEL_ID", optional=True),
        ticket_number_start=ticket_number_start,
        tickets_max_open_per_user=max_open,
        ticket_categories_file=get(
            "TICKET_CATEGORIES_FILE", DEFAULT_TICKET_CATEGORIES_FILE
        )
        or DEFAULT_TICKET_CATEGORIES_FILE,
        ticket_categories_file_explicit=get("TICKET_CATEGORIES_FILE") is not None,
        log_channel_id=snowflake("LOG_CHANNEL_ID", optional=True),
        maintainer_id=snowflake("MAINTAINER_ID", optional=True),
        log_startup_notice=boolean("LOG_STARTUP_NOTICE", False),
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
        copy_override_file=get("COPY_OVERRIDE_FILE", DEFAULT_COPY_OVERRIDE_FILE)
        or DEFAULT_COPY_OVERRIDE_FILE,
        copy_override_file_explicit=get("COPY_OVERRIDE_FILE") is not None,
        deprecated_env_set=tuple(n for n in DEPRECATED_TEXT_ENV if get(n) is not None),
        removed_env_set=tuple(n for n in REMOVED_ENV if get(n) is not None),
        timezone=get("TIMEZONE", "Europe/London") or "Europe/London",
        ticket_categories=categories,
        daily_summary_enabled=boolean("DAILY_SUMMARY_ENABLED", True),
        daily_summary_hour=summary_hour,
        daily_summary_minute=summary_minute,
        daily_summary_timezone=summary_tz,
        ai_api_key=ai_api_key,
        jev_base_url=http_url("JEV_BASE_URL") or DEFAULT_JEV_BASE_URL,
        jev_model=get("JEV_MODEL", DEFAULT_JEV_MODEL) or DEFAULT_JEV_MODEL,
        ai_categorize=boolean("AI_CATEGORIZE", True),
        ai_timeout_seconds=ai_timeout,
        faq_mode=faq_mode,
        faq_threshold=fraction("FAQ_THRESHOLD", 0.9),
        faq_watch_channel_ids=tuple(watch_ids),
        faq_watch_threshold=fraction("FAQ_WATCH_THRESHOLD", 0.95),
        faq_watch_pointer=boolean("FAQ_WATCH_POINTER", False),
        faq_user_cooldown_seconds=number("FAQ_USER_COOLDOWN_SECONDS", 600.0, minimum=0),
        faq_channel_cooldown_seconds=number(
            "FAQ_CHANNEL_COOLDOWN_SECONDS", 120.0, minimum=0
        ),
        kb_dir=get("KB_DIR", "kb") or "kb",
        zammad_url=zammad_url,
        zammad_token=zammad_token,
        zammad_setup_token=get("ZAMMAD_SETUP_TOKEN"),
        zammad_avatar_token=get("ZAMMAD_AVATAR_TOKEN"),
        zammad_public_url=http_url("ZAMMAD_PUBLIC_URL"),
        zammad_webhook_secret=zammad_secret,
        zammad_webhook_url=http_url("ZAMMAD_WEBHOOK_URL") or DEFAULT_ZAMMAD_WEBHOOK_URL,
        zammad_default_avatar_url=http_url("ZAMMAD_DEFAULT_AVATAR_URL"),
        zammad_attachment_max_mb=number("ZAMMAD_ATTACHMENT_MAX_MB", 10.0, minimum=0),
        zammad_timeout_seconds=number("ZAMMAD_TIMEOUT_SECONDS", 20.0, minimum=1),
        zammad_outbox_max_attempts=zammad_max_attempts,
        zammad_reconcile_minutes=zammad_reconcile,
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
