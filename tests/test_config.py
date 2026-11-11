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
    assert "{faq_url}" in s.sticky_body


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


def test_sticky_body_newlines():
    s = make_settings(STICKY_BODY="a\\nb")
    assert s.sticky_body == "a\nb"


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
