import pytest

from nephthys.config import ConfigError
from nephthys.config import load_settings
from nephthys.config import REMOVED_ENV
from tests.helpers import BASE_ENV
from tests.helpers import make_settings


def test_minimal_settings():
    s = make_settings()
    assert s.guild_id == 100
    assert s.community_channel_id == 200
    assert s.staff_role_id == 300
    assert s.open_reaction == "🤔"
    assert s.resolved_reaction == "✅"
    assert s.thread_auto_archive_minutes == 10080
    assert s.stale_ticket_days is None
    assert s.sticky_enabled is False  # disabled in BASE_ENV
    assert make_settings(STICKY_ENABLED="").sticky_enabled is True  # default
    assert s.copy_file == "copy/copy.toml" and not s.copy_file_explicit
    assert s.deprecated_env_set == ()


def test_missing_required_vars_are_all_reported():
    with pytest.raises(ConfigError) as exc:
        load_settings({})
    msg = str(exc.value)
    for name in (
        "DISCORD_TOKEN",
        "GUILD_ID",
        "COMMUNITY_CHANNEL_ID",
        "STAFF_ROLE_ID",
        "DATABASE_URL",
    ):
        assert name in msg


def test_invalid_values():
    env = dict(BASE_ENV, GUILD_ID="abc", THREAD_AUTO_ARCHIVE_MINUTES="30")
    with pytest.raises(ConfigError) as exc:
        load_settings(env)
    assert "GUILD_ID" in str(exc.value)
    assert "THREAD_AUTO_ARCHIVE_MINUTES" in str(exc.value)


def test_open_reaction_can_be_disabled():
    assert make_settings(OPEN_REACTION="").open_reaction is None


def test_stale_days_non_positive_disables():
    assert make_settings(STALE_TICKET_DAYS="0").stale_ticket_days is None
    assert make_settings(STALE_TICKET_DAYS="3").stale_ticket_days == 3


def test_copy_file_and_deprecated_text_env():
    s = make_settings(COPY_FILE="/etc/copy.toml", STICKY_BODY="old text")
    assert s.copy_file == "/etc/copy.toml" and s.copy_file_explicit
    assert s.deprecated_env_set == ("STICKY_BODY",)


def test_categories_default_and_custom():
    assert make_settings().ticket_categories == (
        "Gameplay",
        "Bug",
        "Claims",
        "Economy",
        "Store",
        "Other",
    )
    custom = make_settings(TICKET_CATEGORIES=" Bugs , Ranks,Bugs,, Other ")
    assert custom.ticket_categories == ("Bugs", "Ranks", "Other")
    with pytest.raises(ConfigError):
        make_settings(TICKET_CATEGORIES=",".join(f"c{i}" for i in range(26)))


def test_daily_summary_settings():
    s = make_settings()
    assert (s.daily_summary_hour, s.daily_summary_minute) == (9, 0)
    assert s.daily_summary_timezone == "America/Phoenix"
    assert s.daily_summary_enabled
    s = make_settings(
        DAILY_SUMMARY_TIME="17:45", DAILY_SUMMARY_TIMEZONE="Europe/London"
    )
    assert (s.daily_summary_hour, s.daily_summary_minute) == (17, 45)
    for bad in ({"DAILY_SUMMARY_TIME": "25:00"}, {"DAILY_SUMMARY_TIME": "9am"}):
        with pytest.raises(ConfigError):
            make_settings(**bad)
    with pytest.raises(ConfigError):
        make_settings(DAILY_SUMMARY_TIMEZONE="Mars/Olympus")


def test_jev_and_faq_settings():
    off = make_settings()
    assert not off.ai_enabled and not off.faq_enabled
    assert not off.ai_categorize_enabled and not off.faq_watch_enabled
    # Defaults: Jev on Hack Club AI, shadow mode, ~0.9, a higher bar elsewhere
    assert off.jev_base_url == "https://ai.hackclub.com/proxy/v1/jev"
    assert off.jev_model == "jev-latest"
    assert off.faq_mode == "shadow" and not off.faq_public
    assert off.faq_threshold == 0.9
    assert off.faq_watch_threshold == 0.95 > off.faq_threshold
    assert off.faq_watch_channel_ids == () and off.faq_watch_pointer is False
    assert off.faq_user_cooldown_seconds == 600
    assert off.faq_channel_cooldown_seconds == 120

    # The existing HCAI key is all it takes
    on = make_settings(AI_API_KEY="k")
    assert on.ai_enabled and on.faq_enabled and on.ai_categorize_enabled
    assert not on.faq_public
    assert make_settings(AI_API_KEY="k", FAQ_MODE="PUBLIC").faq_public
    quiet = make_settings(AI_API_KEY="k", FAQ_MODE="off")
    assert not quiet.faq_enabled and quiet.ai_categorize_enabled
    assert not make_settings(
        AI_API_KEY="k", AI_CATEGORIZE="false"
    ).ai_categorize_enabled

    custom = make_settings(
        AI_API_KEY="k",
        JEV_BASE_URL="https://jev.example/v1/",
        JEV_MODEL="jev-2",
        FAQ_THRESHOLD="0.8",
        FAQ_WATCH_THRESHOLD="0.99",
        FAQ_WATCH_CHANNEL_IDS=" 700, 701 ,700,",
        FAQ_WATCH_POINTER="true",
        FAQ_USER_COOLDOWN_SECONDS="30",
        FAQ_CHANNEL_COOLDOWN_SECONDS="0",
    )
    assert custom.jev_base_url == "https://jev.example/v1"
    assert custom.jev_model == "jev-2"
    assert (custom.faq_threshold, custom.faq_watch_threshold) == (0.8, 0.99)
    assert custom.faq_watch_channel_ids == (700, 701)
    assert custom.faq_watch_pointer and custom.faq_watch_enabled
    assert custom.faq_user_cooldown_seconds == 30
    assert custom.faq_channel_cooldown_seconds == 0


@pytest.mark.parametrize(
    ("env", "problem"),
    [
        ({"FAQ_MODE": "live"}, "FAQ_MODE"),
        ({"FAQ_THRESHOLD": "1.5"}, "FAQ_THRESHOLD"),
        ({"FAQ_THRESHOLD": "high"}, "FAQ_THRESHOLD"),
        ({"FAQ_WATCH_THRESHOLD": "-0.1"}, "FAQ_WATCH_THRESHOLD"),
        ({"FAQ_WATCH_CHANNEL_IDS": "general"}, "FAQ_WATCH_CHANNEL_IDS"),
        ({"FAQ_USER_COOLDOWN_SECONDS": "-5"}, "FAQ_USER_COOLDOWN_SECONDS"),
        ({"JEV_BASE_URL": "ai.hackclub.com"}, "JEV_BASE_URL"),
        # Watching a channel the bot already runs is refused
        ({"FAQ_WATCH_CHANNEL_IDS": "200"}, "COMMUNITY_CHANNEL_ID"),
        ({"FAQ_WATCH_CHANNEL_IDS": "700,400"}, "STAFF_CHANNEL_ID"),
        (
            {"FAQ_WATCH_CHANNEL_IDS": "600", "TICKETS_CHANNEL_ID": "600"},
            "TICKETS_CHANNEL_ID",
        ),
    ],
)
def test_bad_faq_settings(env: dict[str, str], problem: str):
    with pytest.raises(ConfigError, match=problem):
        make_settings(**env)


def test_generative_assistant_variables_are_ignored():
    """A .env from before the switch keeps working: AI_MODEL is no longer
    required or read, and the old base URL can't redirect Jev's requests."""
    s = make_settings(
        AI_API_KEY="k",
        AI_MODEL="some-chat-model",
        AI_BASE_URL="https://ai.hackclub.com/proxy/v1",
        AI_ANSWER_MODE="public",
        AI_ANSWER_MIN_CONFIDENCE="2",
    )
    assert s.jev_base_url == "https://ai.hackclub.com/proxy/v1/jev"
    assert s.jev_model == "jev-latest"
    # The old "public" switch does NOT carry over: shadow until FAQ_MODE says so
    assert s.faq_mode == "shadow" and not s.faq_public
    assert {
        "AI_MODEL",
        "AI_BASE_URL",
        "AI_ANSWER_MODE",
        "AI_ANSWER_MIN_CONFIDENCE",
    } <= set(s.removed_env_set)
    for gone in ("ai_model", "ai_base_url", "ai_answer_mode", "ai_answers_enabled"):
        assert not hasattr(s, gone)
    # Values the old settings would have rejected no longer stop startup
    make_settings(AI_ANSWER_MODE="loud", AI_ANSWER_MIN_CONFIDENCE="1.5")


def test_tickets_settings():
    s = make_settings()
    assert not s.tickets_enabled
    assert s.ticket_number_start == 26
    assert s.tickets_max_open_per_user == 2
    assert s.ticket_categories_file == "copy/ticket_categories.toml"
    s = make_settings(TICKETS_CHANNEL_ID="600", TICKET_NUMBER_START="100")
    assert (
        s.tickets_enabled
        and s.tickets_channel_id == 600
        and s.ticket_number_start == 100
    )
    with pytest.raises(ConfigError):
        make_settings(TICKET_NUMBER_START="0")
    with pytest.raises(ConfigError):
        make_settings(TICKETS_MAX_OPEN_PER_USER="-1")


def test_removed_variables_are_ignored_whatever_they_hold():
    """A .env written for an older version still starts the bot: the removed
    CSAT and role-ping variables are never read, so even values that used to
    be rejected are fine. They're only reported, to be logged at startup."""
    assert make_settings().removed_env_set == ()
    leftovers = {
        "CSAT_TIMEOUT_HOURS": "soon",
        "TICKETS_LOCK_ON_CLOSE": "maybe",
        "STORE_ROLE_ID": "not-an-id",
        "MOD_ROLE_ID": "302",
    }
    s = make_settings(TICKETS_CHANNEL_ID="600", **leftovers)
    assert set(s.removed_env_set) == set(leftovers) <= set(REMOVED_ENV)
    for gone in (
        "csat_timeout_hours",
        "tickets_lock_on_close",
        "store_role_id",
        "mod_role_id",
        "role_id_for",
    ):
        assert not hasattr(s, gone)


def test_startup_logs_removed_variables(caplog: pytest.LogCaptureFixture):
    from nephthys.__main__ import load_copy_or_exit

    with caplog.at_level("INFO"):
        load_copy_or_exit(
            make_settings(CSAT_TIMEOUT_HOURS="24", TICKETS_LOCK_ON_CLOSE="true")
        )
    messages = [r.getMessage() for r in caplog.records]
    for name in ("CSAT_TIMEOUT_HOURS", "TICKETS_LOCK_ON_CLOSE"):
        assert any(name in m and "no longer used" in m for m in messages)
