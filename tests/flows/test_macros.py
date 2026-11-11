from typing import Any
from typing import cast

import pytest

from nephthys.cogs.community_help import CommunityHelp
from nephthys.core import tickets
from nephthys.core.transcript import TRANSCRIPT
from nephthys.database.enums import TicketStatus
from nephthys.database.enums import UserType
from nephthys.macros import find_macro
from nephthys.macros import parse_macro
from tests.fakes import FakeBot
from tests.fakes import make_message
from tests.fakes import make_user
from tests.helpers import make_settings

pytestmark = pytest.mark.usefixtures("db")


def test_parse_macro():
    assert parse_macro("?faq") == ("faq", "")
    assert parse_macro("  ?FAQ please read  ") == ("faq", "please read")
    assert parse_macro("?") is None
    assert parse_macro("??") is None
    assert parse_macro("what? no") is None
    assert parse_macro("? faq") is None


def test_aliases():
    assert find_macro("close") is find_macro("resolve")
    assert find_macro("unresolve") is find_macro("reopen")
    assert find_macro("ticket") is None  # v2


async def setup_ticket(bot: FakeBot | None = None):
    bot = bot or FakeBot()
    cog = CommunityHelp(cast(Any, bot))
    op = make_user(name="op")
    q = make_message(bot, op)
    await cog.on_message(q)
    ticket = await tickets.get_ticket_by_message(q.id)
    assert ticket
    return bot, cog, ticket, op, q


async def send(bot: FakeBot, cog: CommunityHelp, ticket, author, text: str):
    thread = bot.world.threads[ticket.thread_id]
    msg = make_message(bot, author, text, channel=thread)
    await cog.on_message(msg)
    return msg


async def test_faq_posts_link_claims_and_does_not_resolve():
    bot, cog, ticket, _, _ = await setup_ticket()
    mod = make_user(staff=True)
    invocation = await send(bot, cog, ticket, mod, "?faq")

    invocation.delete.assert_awaited_once()
    assert bot.sends(ticket.thread_id)[-1].content == TRANSCRIPT.faq_macro.format(
        faq_url="https://example.com/faq"
    )
    t = await tickets.require_ticket(ticket.id)
    assert t.status == TicketStatus.IN_PROGRESS
    assert t.assigned_to is not None
    assert t.last_msg_by == UserType.HELPER


async def test_ip_macro():
    bot, cog, ticket, _, _ = await setup_ticket()
    await send(bot, cog, ticket, make_user(staff=True), "?ip")
    assert "play.example.com" in bot.sends(ticket.thread_id)[-1].content


async def test_unconfigured_macros_error():
    bot = FakeBot(make_settings(FAQ_URL="", SERVER_ADDRESS=""))
    bot, cog, ticket, _, _ = await setup_ticket(bot)
    await send(bot, cog, ticket, make_user(staff=True), "?ip")
    last = bot.sends(ticket.thread_id)[-1]
    assert last.content == TRANSCRIPT.ip_macro_unconfigured
    assert last.delete_after == 10


async def test_resolve_and_reopen_macros():
    bot, cog, ticket, _, q = await setup_ticket()
    mod = make_user(staff=True)
    await send(bot, cog, ticket, mod, "?resolve")
    t = await tickets.require_ticket(ticket.id)
    assert t.status == TicketStatus.CLOSED
    assert t.assigned_to is None  # ?resolve isn't a reply, so no claim / credit
    assert bot.reactions_on(q.id) == {"✅"}
    assert bot.world.threads[ticket.thread_id].archived

    await send(bot, cog, ticket, mod, "?close")
    assert bot.sends(ticket.thread_id)[-1].content == TRANSCRIPT.already_resolved

    await send(bot, cog, ticket, mod, "?reopen")
    t = await tickets.require_ticket(ticket.id)
    assert t.status == TicketStatus.OPEN
    assert bot.reactions_on(q.id) == {"🤔"}


async def test_non_staff_question_mark_is_a_normal_message():
    bot, cog, ticket, _, _ = await setup_ticket()
    player = make_user(name="player")
    msg = await send(bot, cog, ticket, player, "?resolve")
    msg.delete.assert_not_awaited()
    t = await tickets.require_ticket(ticket.id)
    assert t.status == TicketStatus.OPEN
    assert t.last_msg_by == UserType.OTHER


async def test_unknown_macro_and_wrong_state():
    bot, cog, ticket, _, _ = await setup_ticket()
    mod = make_user(staff=True)
    msg = await send(bot, cog, ticket, mod, "?ticket")
    msg.delete.assert_not_awaited()
    assert "isn't a macro" in bot.sends(ticket.thread_id)[-1].content
    # unknown macros don't count as a reply / claim
    assert (await tickets.require_ticket(ticket.id)).assigned_to is None

    await send(bot, cog, ticket, mod, "?resolve")
    await send(bot, cog, ticket, mod, "?faq")
    assert bot.sends(ticket.thread_id)[
        -1
    ].content == TRANSCRIPT.macro_wrong_state.format(name="faq")
