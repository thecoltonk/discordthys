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
    assert set(payloads) == {"waiting", "stats", "faq", "ip", "resolve", "reopen"}
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


def test_ai_client_only_with_key():
    assert SupportBot(make_settings(), connect_db=False).ai_client is None
    bot = SupportBot(make_settings(AI_API_KEY="k", AI_MODEL="m"), connect_db=False)
    assert bot.ai_client is not None and bot.ai_client.model == "m"
