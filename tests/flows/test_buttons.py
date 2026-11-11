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
