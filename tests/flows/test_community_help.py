from types import SimpleNamespace
from typing import Any
from typing import cast

import discord
import pytest

from nephthys.cogs.community_help import CommunityHelp
from nephthys.core import tickets
from nephthys.core.copytext import DEFAULT_COPY as C
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

    # No staff card: the staff channel is for #tickets
    assert bot.sends(bot.settings.staff_channel_id or 0) == []

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
    assert closing.content == C.community.resolved.format(user=op.mention)
    assert button_ids(closing.view) == [f"cs:reopen:{ticket.id}"]
    assert bot.reactions_on(msg.id) == {"✅"}
    assert thread.archived is True


async def test_resolve_button_refused_for_other_players(bot, cog):
    _, ticket, _ = await post_question(bot, cog)
    interaction = make_interaction(bot, make_user(name="rando"))
    await ResolveButton(ticket.id).callback(interaction)

    interaction.followup.send.assert_awaited_once()
    assert interaction.followup.send.call_args.args[0] == C.errors.not_allowed
    assert interaction.followup.send.call_args.kwargs["ephemeral"] is True
    t = await tickets.require_ticket(ticket.id)
    assert t.status == TicketStatus.OPEN


async def test_resolve_twice_says_already_resolved(bot, cog):
    _, ticket, op = await post_question(bot, cog)
    await ResolveButton(ticket.id).callback(make_interaction(bot, op))
    second = make_interaction(bot, make_user(staff=True))
    await ResolveButton(ticket.id).callback(second)
    assert second.followup.send.call_args.args[0] == C.errors.already_resolved


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
    assert bot.sends(ticket.thread_id)[-1].content == C.community.reopened.format(
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


async def test_staff_can_resolve_again_after_the_poster_reopens(bot, cog):
    """Reported live: staff resolved a post, the poster's reply auto-reopened
    it, then staff clicked the original "Mark as resolved" and were told
    "only the person who asked or a staff member can do that" (twice).

    Uses real `discord.Member` objects. The second click arrives with the
    Staff role in the payload but not in the bot's guild role cache (as while
    the guild is unavailable after a reconnect); `Member.roles` then drops it.
    Staff detection must use the payload's role IDs."""
    from tests.fakes import make_member

    settings = bot.settings
    staff_role = settings.staff_role_id
    _msg, ticket, op = await post_question(bot, cog)
    staff_id = 4242
    staff = make_member(settings, user_id=staff_id, role_ids=(staff_role,))
    await reply(bot, cog, ticket, staff, "try /claim")

    # 1. Staff resolve it from the original auto-reply
    [auto_reply] = [
        m for m in bot.sends(ticket.thread_id) if "someone should be along" in m.content
    ]
    resolve_id = button_ids(auto_reply.view)[0]
    first = make_interaction(bot, staff)
    await ResolveButton(ticket.id).callback(first)
    first.followup.send.assert_not_awaited()
    assert (await tickets.require_ticket(ticket.id)).status == TicketStatus.CLOSED

    # 2. The poster replies: auto-reopen
    await reply(bot, cog, ticket, op, "actually it's still broken")
    reopened = await tickets.require_ticket(ticket.id)
    assert reopened.status == TicketStatus.IN_PROGRESS
    assert bot.sends(ticket.thread_id)[-1].content == C.community.reopened.format(
        user=op.mention
    )

    # 3. Staff click the same original button again (twice, as reported)
    same_staff = make_member(
        settings, user_id=staff_id, role_ids=(staff_role,), cached_role_ids=()
    )
    assert [r.id for r in same_staff.roles] == []  # what discord.py reports
    for attempt in range(2):
        click = make_interaction(bot, same_staff)
        await ResolveButton(int(resolve_id.rsplit(":", 1)[1])).callback(click)
        sent = [c.args[0] for c in click.followup.send.call_args_list]
        assert C.errors.not_allowed not in sent
        expected = [] if attempt == 0 else [C.errors.already_resolved]
        assert sent == expected
    final = await tickets.require_ticket(ticket.id)
    assert final.status == TicketStatus.CLOSED
    closed_by = await tickets.get_user(tickets.fk_id(final.closed_by))
    assert closed_by is not None and closed_by.discord_id == staff_id


def test_staff_detection_ignores_the_role_cache():
    from nephthys.discord_ops.threads import actor_from_user
    from tests.fakes import make_member

    settings = make_settings()
    staff_role = settings.staff_role_id
    uncached = make_member(settings, role_ids=(staff_role,), cached_role_ids=())
    assert actor_from_user(uncached, staff_role).is_staff
    player = make_member(settings, role_ids=(1234,))
    assert not actor_from_user(player, staff_role).is_staff
