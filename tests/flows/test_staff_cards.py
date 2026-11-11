from types import SimpleNamespace
from typing import Any
from typing import cast

import pytest

from nephthys.cogs.community_help import CommunityHelp
from nephthys.core import tickets
from nephthys.core.copytext import DEFAULT_COPY as C
from nephthys.discord_ops import flows
from nephthys.ui.buttons import CategorySelect
from nephthys.ui.buttons import ReopenButton
from nephthys.ui.buttons import ResolveButton
from tests.fakes import FakeBot
from tests.fakes import make_interaction
from tests.fakes import make_message
from tests.fakes import make_user
from tests.helpers import make_settings

pytestmark = pytest.mark.usefixtures("db")


def fields(embed: Any) -> dict[str, str]:
    return {f.name: f.value for f in embed.fields}


async def ask(
    bot: FakeBot, cog: CommunityHelp, author=None, content="How do I claim land?"
):
    author = author or make_user(name="op")
    msg = make_message(bot, author, content)
    await cog.on_message(msg)
    ticket = await tickets.get_ticket_by_message(msg.id)
    assert ticket
    return msg, ticket, author


def card_of(bot: FakeBot, ticket) -> Any:
    assert ticket.staff_card_message_id, "card should be recorded on the ticket"
    return bot.message(bot.settings.staff_channel_id or 0, ticket.staff_card_message_id)


async def fresh(ticket):
    return await tickets.require_ticket(ticket.id)


async def test_card_contents_and_past_tickets():
    bot = FakeBot()
    cog = CommunityHelp(cast(Any, bot))
    op = make_user(name="op")
    await ask(bot, cog, op, "first question")
    _, ticket, _ = await ask(bot, cog, op, "second question\nwith details")

    card = card_of(bot, await fresh(ticket))
    embed = card.embed
    assert embed.title == f"#{ticket.id} · second question"
    assert "with details" in embed.description
    assert embed.url.endswith(f"/{ticket.thread_id}")
    f = fields(embed)
    assert f["Asked by"] == f"{op.mention} · 1 past ticket"
    assert f["Status"].startswith("🟡 Waiting for staff")
    assert f["Category"] == "—"
    # Category select + link button
    select = card.view.children[0]
    assert select.custom_id == f"cs:cat:{ticket.id}"
    assert [o.label for o in select.item.options] == list(
        make_settings().ticket_categories
    )
    assert card.view.children[1].url.endswith(f"/{ticket.thread_id}")


async def test_card_tracks_claim_resolve_reopen():
    bot = FakeBot()
    cog = CommunityHelp(cast(Any, bot))
    _, ticket, op = await ask(bot, cog)
    thread = bot.world.threads[ticket.thread_id]

    mod = make_user(staff=True, name="mod")
    await cog.on_message(make_message(bot, mod, "try /claim", channel=thread))
    assert (
        fields(card_of(bot, await fresh(ticket)).embed)["Status"]
        == f"🔵 Claimed by <@{mod.id}>"
    )

    await ResolveButton(ticket.id).callback(make_interaction(bot, op))
    status = fields(card_of(bot, await fresh(ticket)).embed)["Status"]
    assert status == f"✅ Resolved by <@{op.id}> · credit <@{mod.id}>"

    await ReopenButton(ticket.id).callback(make_interaction(bot, op))
    status = fields(card_of(bot, await fresh(ticket)).embed)["Status"]
    assert status == f"🔵 Claimed by <@{mod.id}> (re-opened)"


async def test_card_shows_no_credit_for_self_resolve():
    bot = FakeBot()
    cog = CommunityHelp(cast(Any, bot))
    _, ticket, op = await ask(bot, cog)
    await ResolveButton(ticket.id).callback(make_interaction(bot, op))
    status = fields(card_of(bot, await fresh(ticket)).embed)["Status"]
    assert status.endswith("· no staff credit")


async def test_category_select_staff_only():
    bot = FakeBot()
    cog = CommunityHelp(cast(Any, bot))
    _, ticket, _ = await ask(bot, cog)

    player = make_interaction(bot, make_user())
    player.data = {"values": ["Bug"]}
    await CategorySelect(ticket.id).callback(player)
    assert player.response.send_message.call_args.args[0] == C.errors.staff_only
    assert (await fresh(ticket)).category is None

    mod = make_interaction(bot, make_user(staff=True))
    mod.data = {"values": ["Bug"]}
    await CategorySelect(ticket.id).callback(mod)
    t = await fresh(ticket)
    assert (t.category, t.category_source) == ("Bug", "staff")
    edited = mod.response.edit_message.call_args.kwargs
    assert fields(edited["embed"])["Category"] == "Bug"
    defaults = [o.label for o in edited["view"].children[0].item.options if o.default]
    assert defaults == ["Bug"]


async def test_category_select_from_custom_id_uses_configured_categories():
    bot = FakeBot(make_settings(TICKET_CATEGORIES="A,B"))
    item = await CategorySelect.from_custom_id(
        cast(Any, SimpleNamespace(client=bot)),
        cast(Any, None),
        CategorySelect.__discord_ui_compiled_template__.fullmatch("cs:cat:7"),  # type: ignore[arg-type]
    )
    assert item.ticket_id == 7
    assert [o.label for o in item.item.options] == ["A", "B"]


async def test_card_deleted_with_ticket():
    bot = FakeBot()
    cog = CommunityHelp(cast(Any, bot))
    msg, ticket, _ = await ask(bot, cog)
    ticket = await fresh(ticket)
    await flows.handle_question_deleted(cast(Any, bot), msg.id)
    assert (
        bot.settings.staff_channel_id,
        ticket.staff_card_message_id,
    ) in bot.world.deleted_messages


async def test_no_staff_channel_no_card():
    bot = FakeBot(make_settings(STAFF_CHANNEL_ID=""))
    cog = CommunityHelp(cast(Any, bot))
    _, ticket, _ = await ask(bot, cog)
    assert (await fresh(ticket)).staff_card_message_id is None
