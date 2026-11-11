from types import SimpleNamespace
from typing import Any
from typing import cast

import discord
import pytest

from nephthys.cogs.community_help import CommunityHelp
from nephthys.core import tickets
from nephthys.core.transcript import TRANSCRIPT
from nephthys.database.enums import BotMessageKind
from nephthys.database.enums import TicketStatus
from nephthys.database.tables import User
from nephthys.discord_ops import flows
from nephthys.ui.buttons import ReopenButton
from nephthys.ui.buttons import ResolveButton
from tests.fakes import FakeBot
from tests.fakes import make_interaction
from tests.fakes import make_message
from tests.fakes import make_user
from tests.helpers import make_settings

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
def bot() -> FakeBot:
    return FakeBot()


@pytest.fixture
def cog(bot: FakeBot) -> CommunityHelp:
    return CommunityHelp(cast(Any, bot))


def button_ids(view: discord.ui.View) -> list[str]:
    return [cast(discord.ui.Button, item).custom_id or "" for item in view.children]


async def post_question(
    bot: FakeBot, cog: CommunityHelp, author=None, content="How do I claim land?"
):
    author = author or make_user(name="op")
    msg = make_message(bot, author, content)
    await cog.on_message(msg)
    ticket = await tickets.get_ticket_by_message(msg.id)
    assert ticket is not None, "expected a ticket to be created"
    return msg, ticket, author


async def reply(bot: FakeBot, cog: CommunityHelp, ticket, author, content="try /claim"):
    thread = bot.world.threads[ticket.thread_id]
    msg = make_message(bot, author, content, channel=thread)
    await cog.on_message(msg)
    return msg


async def test_new_question_opens_thread_ticket_and_auto_reply(bot, cog):
    msg, ticket, _author = await post_question(bot, cog)

    assert ticket is not None
    assert ticket.thread_id == msg.id
    assert ticket.title == "How do I claim land?"
    assert ticket.status == TicketStatus.OPEN
    msg.create_thread.assert_awaited_once()
    assert msg.create_thread.call_args.kwargs["auto_archive_duration"] == 10080

    [auto_reply] = bot.sends(ticket.thread_id)
    assert "someone should be along to help you soon" in auto_reply.content
    assert "https://example.com/faq" in auto_reply.content
    assert button_ids(auto_reply.view) == [f"cs:resolve:{ticket.id}"]

    assert bot.reactions_on(msg.id) == {"🤔"}

    [card] = bot.sends(bot.settings.staff_channel_id or 0)
    assert card.content is None and card.embed.title.startswith(f"#{ticket.id}")
    assert card.allowed_mentions.users is False  # no pings from the card

    [bot_msg] = await tickets.get_bot_messages(ticket.id)
    assert bot_msg.kind == BotMessageKind.AUTO_REPLY


async def test_long_and_empty_titles(bot, cog):
    _, long_ticket, _ = await post_question(bot, cog, content="x" * 300 + "\nmore")
    assert long_ticket and len(long_ticket.title or "") == 90
    _, empty, _ = await post_question(
        bot, cog, author=make_user(name="Sam"), content=""
    )
    assert empty and empty.title == "Question from Sam"


async def test_ignored_messages(bot, cog):
    # bots (including our sticky), system messages, other guilds, other channels
    bot_msg = make_message(bot, make_user(bot=True))
    system = make_message(bot, make_user(), msg_type=discord.MessageType.pins_add)
    other_guild = make_message(bot, make_user())
    other_guild.guild.id = 1
    elsewhere = make_message(bot, make_user(), channel=type("C", (), {"id": 12345})())
    for m in (bot_msg, system, other_guild, elsewhere):
        await cog.on_message(m)
        m.create_thread.assert_not_awaited()
    assert await tickets.get_ticket_by_message(bot_msg.id) is None


async def test_thread_create_failure_makes_no_ticket(bot, cog):
    msg = make_message(bot, make_user())
    msg.create_thread.side_effect = discord.HTTPException(
        cast(Any, SimpleNamespace(status=400, reason="Bad")), "thread exists"
    )
    await cog.on_message(msg)
    assert await tickets.get_ticket_by_message(msg.id) is None


async def test_first_staff_reply_claims(bot, cog):
    _, ticket, _ = await post_question(bot, cog)
    await reply(bot, cog, ticket, make_user(name="helpful player"))
    t = await tickets.require_ticket(ticket.id)
    assert t.assigned_to is None and t.status == TicketStatus.OPEN

    mod = make_user(staff=True, name="mod")
    await reply(bot, cog, ticket, mod)
    await reply(bot, cog, ticket, make_user(staff=True, name="mod2"))
    t = await tickets.require_ticket(ticket.id)
    claimer = await User.objects().where(User.discord_id == mod.id).first()
    assert claimer and tickets.fk_id(t.assigned_to) == claimer.id
    assert t.status == TicketStatus.IN_PROGRESS


async def test_resolve_button_by_op(bot, cog):
    msg, ticket, op = await post_question(bot, cog)
    interaction = make_interaction(bot, op)
    await ResolveButton(ticket.id).callback(interaction)

    interaction.response.defer.assert_awaited_once()
    interaction.followup.send.assert_not_awaited()
    t = await tickets.require_ticket(ticket.id)
    assert t.status == TicketStatus.CLOSED

    thread = bot.world.threads[ticket.thread_id]
    closing = bot.sends(ticket.thread_id)[-1]
    assert closing.content == TRANSCRIPT.resolved.format(user=op.mention)
    assert button_ids(closing.view) == [f"cs:reopen:{ticket.id}"]
    assert bot.reactions_on(msg.id) == {"✅"}
    assert thread.archived is True


async def test_resolve_button_refused_for_other_players(bot, cog):
    _, ticket, _ = await post_question(bot, cog)
    interaction = make_interaction(bot, make_user(name="rando"))
    await ResolveButton(ticket.id).callback(interaction)

    interaction.followup.send.assert_awaited_once()
    assert interaction.followup.send.call_args.args[0] == TRANSCRIPT.not_allowed
    assert interaction.followup.send.call_args.kwargs["ephemeral"] is True
    t = await tickets.require_ticket(ticket.id)
    assert t.status == TicketStatus.OPEN


async def test_resolve_twice_says_already_resolved(bot, cog):
    _, ticket, op = await post_question(bot, cog)
    await ResolveButton(ticket.id).callback(make_interaction(bot, op))
    second = make_interaction(bot, make_user(staff=True))
    await ResolveButton(ticket.id).callback(second)
    assert second.followup.send.call_args.args[0] == TRANSCRIPT.already_resolved


async def test_reopen_button(bot, cog):
    msg, ticket, op = await post_question(bot, cog)
    await ResolveButton(ticket.id).callback(make_interaction(bot, op))

    mod = make_user(staff=True)
    await ReopenButton(ticket.id).callback(make_interaction(bot, mod))

    t = await tickets.require_ticket(ticket.id)
    assert t.status == TicketStatus.OPEN
    thread = bot.world.threads[ticket.thread_id]
    assert thread.archived is False
    assert bot.reactions_on(msg.id) == {"🤔"}
    assert bot.sends(ticket.thread_id)[-1].content == TRANSCRIPT.reopened.format(
        user=mod.mention
    )


async def test_op_reply_after_resolve_auto_reopens(bot, cog):
    msg, ticket, op = await post_question(bot, cog)
    await ResolveButton(ticket.id).callback(make_interaction(bot, op))

    # Someone else posting doesn't reopen
    await reply(bot, cog, ticket, make_user(name="other"))
    assert (await tickets.require_ticket(ticket.id)).status == TicketStatus.CLOSED

    await reply(bot, cog, ticket, op, "actually it broke again")
    t = await tickets.require_ticket(ticket.id)
    assert t.status == TicketStatus.OPEN
    assert bot.reactions_on(msg.id) == {"🤔"}
    assert bot.world.threads[ticket.thread_id].archived is False
    assert "re-opened" in bot.sends(ticket.thread_id)[-1].content


async def test_question_deleted_without_replies_cleans_up(bot, cog):
    msg, ticket, _ = await post_question(bot, cog)
    thread = bot.world.threads[ticket.thread_id]
    await cog.on_raw_message_delete(
        cast(Any, type("P", (), {"channel_id": msg.channel.id, "message_id": msg.id})())
    )
    assert thread.deleted
    assert await tickets.get_ticket(ticket.id) is None


async def test_question_deleted_with_replies_is_kept(bot, cog):
    msg, ticket, _ = await post_question(bot, cog)
    thread = bot.world.threads[ticket.thread_id]
    human = make_message(bot, make_user(), "here's how", channel=thread)
    thread.human_messages = [human]
    await flows.handle_question_deleted(cast(Any, bot), msg.id)
    assert not thread.deleted
    assert await tickets.get_ticket(ticket.id) is not None


async def test_thread_deleted_removes_ticket(bot, cog):
    _, ticket, _ = await post_question(bot, cog)
    payload = type(
        "P",
        (),
        {"parent_id": bot.settings.community_channel_id, "thread_id": ticket.thread_id},
    )()
    await cog.on_raw_thread_delete(cast(Any, payload))
    assert await tickets.get_ticket(ticket.id) is None


async def test_open_reaction_disabled():
    bot = FakeBot(make_settings(OPEN_REACTION=""))
    cog = CommunityHelp(cast(Any, bot))
    msg, _ticket, _ = await post_question(bot, cog)
    assert bot.reactions_on(msg.id) == set()
