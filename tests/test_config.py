import pytest

from nephthys.config import ConfigError
from nephthys.config import load_settings
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


def test_ai_settings():
    off = make_settings()
    assert (
        not off.ai_enabled
        and not off.ai_answers_enabled
        and not off.ai_categorize_enabled
    )
    assert off.ai_base_url == "https://ai.hackclub.com/proxy/v1"
    assert off.ai_answer_mode == "shadow"
    on = make_settings(AI_API_KEY="k", AI_MODEL="m")
    assert on.ai_enabled and on.ai_answers_enabled and on.ai_categorize_enabled
    assert on.ai_answer_min_confidence == 0.8
    assert not make_settings(
        AI_API_KEY="k", AI_MODEL="m", AI_ANSWER_MODE="off"
    ).ai_answers_enabled
    assert not make_settings(
        AI_API_KEY="k", AI_MODEL="m", AI_CATEGORIZE="false"
    ).ai_categorize_enabled
    with pytest.raises(ConfigError):
        make_settings(AI_API_KEY="k")  # model required
    with pytest.raises(ConfigError):
        make_settings(AI_ANSWER_MODE="loud")
    with pytest.raises(ConfigError):
        make_settings(AI_ANSWER_MIN_CONFIDENCE="1.5")


def test_tickets_settings():
    s = make_settings()
    assert not s.tickets_enabled
    assert s.ticket_number_start == 26
    assert s.tickets_max_open_per_user == 2
    assert s.ticket_categories_file == "copy/ticket_categories.toml"
    s = make_settings(
        TICKETS_CHANNEL_ID="600", STORE_ROLE_ID="301", TICKET_NUMBER_START="100"
    )
    assert (
        s.tickets_enabled
        and s.tickets_channel_id == 600
        and s.ticket_number_start == 100
    )
    assert s.role_id_for("store") == 301
    assert s.role_id_for("mod") == s.staff_role_id  # falls back to Staff
    assert s.role_id_for("staff") == 300
    assert s.role_id_for("12345") == 12345
    with pytest.raises(ConfigError):
        make_settings(TICKET_NUMBER_START="0")
    with pytest.raises(ConfigError):
        make_settings(TICKETS_MAX_OPEN_PER_USER="-1")


def test_csat_archive_settings():
    s = make_settings()
    assert s.csat_timeout_hours == 24 and s.tickets_lock_on_close is True
    s = make_settings(CSAT_TIMEOUT_HOURS="0.5", TICKETS_LOCK_ON_CLOSE="no")
    assert s.csat_timeout_hours == 0.5 and s.tickets_lock_on_close is False
    for bad in ("0", "-1", "soon"):
        with pytest.raises(ConfigError):
            make_settings(CSAT_TIMEOUT_HOURS=bad)
