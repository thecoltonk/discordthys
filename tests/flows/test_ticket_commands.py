from types import SimpleNamespace
from typing import Any
from typing import cast

import pytest

from nephthys.cogs.community_help import CommunityHelp
from nephthys.cogs.ticket_commands import TicketCommands
from nephthys.core import tickets
from nephthys.core.copytext import DEFAULT_COPY as C
from nephthys.core.queries import get_unanswered_tickets
from nephthys.database.enums import TicketStatus
from nephthys.database.enums import UserType
from tests.fakes import FakeBot
from tests.fakes import make_interaction
from tests.fakes import make_message
from tests.fakes import make_user
from tests.helpers import make_settings

pytestmark = pytest.mark.usefixtures("db")


async def setup(bot: FakeBot | None = None):
    bot = bot or FakeBot()
    help_cog = CommunityHelp(cast(Any, bot))
    commands = TicketCommands(cast(Any, bot))
    q = make_message(bot, make_user(name="op"))
    await help_cog.on_message(q)
    ticket = await tickets.get_ticket_by_message(q.id)
    assert ticket
    return bot, commands, ticket, bot.world.threads[ticket.thread_id], q


async def run(command: Any, cog: TicketCommands, interaction: Any) -> None:
    await cast(Any, command).callback(cog, interaction)


async def test_faq_posts_in_thread_without_claiming_or_clearing_waiting():
    bot, commands, ticket, thread, _ = await setup()
    i = make_interaction(bot, make_user(staff=True), channel=thread)
    await run(TicketCommands.faq, commands, i)

    assert bot.sends(ticket.thread_id)[-1].content == C.commands.faq_reply.format(
        faq_url="https://example.com/faq"
    )
    t = await tickets.require_ticket(ticket.id)
    assert t.assigned_to is None and t.status == TicketStatus.OPEN
    assert t.last_msg_by == UserType.AUTHOR
    assert [w.id for w in await get_unanswered_tickets()] == [ticket.id]


async def test_ip_and_unconfigured():
    bot, commands, ticket, thread, _ = await setup()
    await run(
        TicketCommands.ip,
        commands,
        make_interaction(bot, make_user(staff=True), channel=thread),
    )
    assert "play.example.com" in bot.sends(ticket.thread_id)[-1].content

    bot2, commands2, _, thread2, _ = await setup(
        FakeBot(make_settings(SERVER_ADDRESS=""))
    )
    i = make_interaction(bot2, make_user(staff=True), channel=thread2)
    await run(TicketCommands.ip, commands2, i)
    assert i.response.send_message.call_args.args[0] == C.commands.ip_unconfigured
    assert i.response.send_message.call_args.kwargs["ephemeral"] is True


async def test_commands_refuse_non_staff_and_non_ticket_channels():
    bot, commands, _, thread, _ = await setup()
    for command in (
        TicketCommands.faq,
        TicketCommands.ip,
        TicketCommands.resolve,
        TicketCommands.reopen,
    ):
        player = make_interaction(bot, make_user(), channel=thread)
        await run(command, commands, player)
        assert player.response.send_message.call_args.args[0] == C.errors.staff_only

        elsewhere = make_interaction(
            bot, make_user(staff=True), channel=SimpleNamespace(id=5)
        )
        await run(command, commands, elsewhere)
        assert (
            elsewhere.response.send_message.call_args.args[0]
            == C.errors.not_a_ticket_thread
        )


async def test_resolve_and_reopen_commands():
    bot, commands, ticket, thread, q = await setup()
    mod = make_user(staff=True)

    i = make_interaction(bot, mod, channel=thread)
    await run(TicketCommands.resolve, commands, i)
    assert i.followup.send.call_args.args[0] == C.commands.resolved
    t = await tickets.require_ticket(ticket.id)
    assert t.status == TicketStatus.CLOSED and t.assigned_to is None
    assert bot.reactions_on(q.id) == {"✅"} and thread.archived

    again = make_interaction(bot, mod, channel=thread)
    await run(TicketCommands.resolve, commands, again)
    assert again.followup.send.call_args.args[0] == C.errors.already_resolved

    i = make_interaction(bot, mod, channel=thread)
    await run(TicketCommands.reopen, commands, i)
    assert i.followup.send.call_args.args[0] == C.commands.reopened
    assert (await tickets.require_ticket(ticket.id)).status == TicketStatus.OPEN

    again = make_interaction(bot, mod, channel=thread)
    await run(TicketCommands.reopen, commands, again)
    assert again.followup.send.call_args.args[0] == C.errors.not_resolved


async def test_question_mark_messages_are_normal_replies_now():
    bot, _, ticket, thread, _ = await setup()
    cog = CommunityHelp(cast(Any, bot))
    mod = make_user(staff=True)
    msg = make_message(bot, mod, "?resolve", channel=thread)
    await cog.on_message(msg)
    msg.delete.assert_not_awaited()
    t = await tickets.require_ticket(ticket.id)
    assert t.status == TicketStatus.IN_PROGRESS  # it's a staff reply: claims
