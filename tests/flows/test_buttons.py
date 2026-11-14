from typing import Any
from typing import cast

from nephthys.bot import SupportBot
from nephthys.ui.buttons import DYNAMIC_ITEMS
from nephthys.ui.buttons import ReopenButton
from nephthys.ui.buttons import ResolveButton
from tests.helpers import make_settings


def test_custom_ids_match_templates():
    for cls, custom_id in (
        (ResolveButton, "cs:resolve:42"),
        (ReopenButton, "cs:reopen:42"),
    ):
        template = cls.__discord_ui_compiled_template__
        match = template.fullmatch(custom_id)
        assert match and match["ticket_id"] == "42"
        assert cls(42).custom_id == custom_id
        assert len(custom_id) <= 100


async def test_dynamic_items_register():
    bot = SupportBot(make_settings(), connect_db=False)
    bot.add_dynamic_items(*DYNAMIC_ITEMS)
    await bot.close()


async def test_extensions_load():
    from nephthys.bot import EXTENSIONS

    bot = SupportBot(make_settings(), connect_db=False)
    for ext in EXTENSIONS:
        await bot.load_extension(ext)
    assert bot.get_cog("CommunityHelp") is not None
    await bot.close()


async def test_slash_commands_serialize():
    from nephthys.bot import EXTENSIONS

    bot = SupportBot(make_settings(), connect_db=False)
    for ext in EXTENSIONS:
        await bot.load_extension(ext)
    payloads = {c.name: c.to_dict(bot.tree) for c in bot.tree.get_commands()}
    assert set(payloads) == {
        "waiting",
        "stats",
        "faq",
        "ip",
        "resolve",
        "reopen",
        "escalate",
    }
    days = payloads["stats"]["options"][0]
    assert days["min_value"] == 1 and days["max_value"] == 365
    await bot.close()


def test_scheduled_jobs():
    from apscheduler.triggers.cron import CronTrigger

    bot = SupportBot(make_settings(STALE_TICKET_DAYS="3"), connect_db=False)
    bot.configure_jobs()
    jobs = {job.func.__name__: job for job in bot.scheduler.get_jobs()}
    assert set(jobs) == {"run_stale_close", "run_daily_summary"}
    trigger = jobs["run_daily_summary"].trigger
    assert isinstance(trigger, CronTrigger)
    assert str(trigger.timezone) == "America/Phoenix"
    fields = {f.name: str(f) for f in trigger.fields}
    assert (fields["hour"], fields["minute"]) == ("9", "0")

    quiet = SupportBot(make_settings(STAFF_CHANNEL_ID=""), connect_db=False)
    quiet.configure_jobs()
    assert quiet.scheduler.get_jobs() == []


def test_jev_client_only_with_key():
    assert SupportBot(make_settings(), connect_db=False).jev is None
    bot = SupportBot(make_settings(AI_API_KEY="k"), connect_db=False)
    assert bot.jev is not None and bot.jev.model == "jev-latest"
    assert cast(Any, bot.jev).url == "https://ai.hackclub.com/proxy/v1/jev/systemone"
    # A leftover AI_MODEL from the generative assistant doesn't leak in
    old = SupportBot(make_settings(AI_API_KEY="k", AI_MODEL="gpt"), connect_db=False)
    assert old.jev is not None and old.jev.model == "jev-latest"


async def test_faq_watch_cog_loads_only_when_channels_are_watched():
    from nephthys.bot import EXTENSIONS
    from nephthys.bot import FAQ_WATCH_EXTENSION

    assert FAQ_WATCH_EXTENSION not in EXTENSIONS
    for env, watching in (
        ({}, False),
        ({"FAQ_WATCH_CHANNEL_IDS": "700"}, False),  # no key: Jev is off
        ({"AI_API_KEY": "k"}, False),
        ({"AI_API_KEY": "k", "FAQ_WATCH_CHANNEL_IDS": "700"}, True),
        ({"AI_API_KEY": "k", "FAQ_WATCH_CHANNEL_IDS": "700", "FAQ_MODE": "off"}, False),
    ):
        assert make_settings(**env).faq_watch_enabled is watching, env
    bot = SupportBot(
        make_settings(AI_API_KEY="k", FAQ_WATCH_CHANNEL_IDS="700"), connect_db=False
    )
    await bot.load_extension(FAQ_WATCH_EXTENSION)
    assert bot.get_cog("FAQWatch") is not None
    await bot.close()


def test_no_archive_sweep_is_scheduled():
    """Closing a #tickets thread archives it on the spot, so there's no
    background sweep (and no module for one) any more."""
    import importlib.util

    bot = SupportBot(make_settings(TICKETS_CHANNEL_ID="600"), connect_db=False)
    bot.configure_jobs()
    assert {job.func.__name__ for job in bot.scheduler.get_jobs()} == {
        "run_daily_summary"
    }
    assert not hasattr(bot, "run_archive_closed")
    assert importlib.util.find_spec("nephthys.tasks.archive_closed") is None
