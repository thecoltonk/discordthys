"""#tickets: closed threads stay open for CSAT, then archive on rating or timeout."""

from datetime import datetime
from datetime import timedelta
from datetime import UTC
from typing import Any
from typing import cast
from unittest.mock import MagicMock

import discord
import pytest

from nephthys.cogs.ticket_commands import TicketCommands
from nephthys.core import tickets
from nephthys.core.copytext import DEFAULT_COPY as C
from nephthys.database.enums import TicketStatus
from nephthys.database.tables import Ticket
from nephthys.tasks.archive_closed import archive_expired_tickets
from nephthys.ui.ticket_buttons import CloseTicketButton
from nephthys.ui.ticket_buttons import CSATButton
from tests.fakes import make_interaction
from tests.fakes import make_user
from tests.flows.test_tickets import create
from tests.flows.test_tickets import pick
from tests.flows.test_tickets import tickets_bot

pytestmark = pytest.mark.usefixtures("db")


async def closed_ticket(bot, user=None):
    ticket, thread, user, _ = await create(bot, user)
    await pick(bot, ticket, user, "store")
    await CloseTicketButton(ticket.id).callback(make_interaction(bot, user))
    return await tickets.require_ticket(ticket.id), thread, user


async def age(ticket, hours: float) -> None:
    await Ticket.update(
        {Ticket.closed_at: datetime.now(UTC) - timedelta(hours=hours)}
    ).where(Ticket.id == ticket.id)


async def test_timeout_archives_only_expired_unrated_tickets():
    bot = tickets_bot()
    old, old_thread, _ = await closed_ticket(bot)
    recent, recent_thread, _ = await closed_ticket(bot)
    await age(old, 25)
    await age(recent, 23)
    _open_ticket, open_thread, _, _ = await create(bot)

    assert await archive_expired_tickets(cast(Any, bot)) == 1
    assert old_thread.archived and old_thread.locked
    assert (await tickets.require_ticket(old.id)).archived_at is not None
    assert not recent_thread.archived
    assert not open_thread.archived

    # Already-archived tickets aren't touched again
    edits = old_thread.edit.await_count
    assert await archive_expired_tickets(cast(Any, bot)) == 0
    assert old_thread.edit.await_count == edits


async def test_custom_timeout():
    bot = tickets_bot(CSAT_TIMEOUT_HOURS="0.5")
    ticket, thread, _ = await closed_ticket(bot)
    await age(ticket, 1)
    assert await archive_expired_tickets(cast(Any, bot)) == 1
    assert thread.archived


async def test_reopen_in_between_cancels_the_pending_archive():
    bot = tickets_bot()
    ticket, thread, user = await closed_ticket(bot)
    await age(ticket, 30)
    mod = make_user(staff=True)
    i = make_interaction(bot, mod, channel=thread)
    await cast(Any, TicketCommands.reopen).callback(TicketCommands(cast(Any, bot)), i)
    assert i.followup.send.call_args.args[0] == C.commands.reopened
    ticket = await tickets.require_ticket(ticket.id)
    assert ticket.status == TicketStatus.OPEN and ticket.archived_at is None
    assert not thread.locked and not thread.archived

    assert await archive_expired_tickets(cast(Any, bot)) == 0
    assert not thread.archived

    # A rating left on the old prompt is recorded but doesn't archive an open ticket
    await CSATButton(ticket.id, 5).callback(make_interaction(bot, user))
    ticket = await tickets.require_ticket(ticket.id)
    assert ticket.csat_rating == 5 and not thread.archived


async def test_rating_after_timeout_archive_is_tolerated():
    bot = tickets_bot()
    ticket, thread, user = await closed_ticket(bot)
    await age(ticket, 48)
    await archive_expired_tickets(cast(Any, bot))
    assert thread.archived
    edits = thread.edit.await_count

    # Discord refuses to edit a message in the archived thread
    vote = make_interaction(bot, user)
    vote.response.edit_message.side_effect = discord.HTTPException(
        MagicMock(status=400, reason="Bad"), "Thread is archived"
    )
    await CSATButton(ticket.id, 3).callback(vote)
    assert (await tickets.require_ticket(ticket.id)).csat_rating == 3
    assert vote.response.send_message.call_args.args[0] == "thanks! you rated 3/5 😐"
    assert vote.response.send_message.call_args.kwargs["ephemeral"] is True
    assert thread.edit.await_count == edits  # no second archive attempt


async def test_lock_on_close_can_be_disabled():
    bot = tickets_bot(TICKETS_LOCK_ON_CLOSE="false")
    ticket, thread, user = await closed_ticket(bot)
    assert not thread.locked and not thread.archived
    await CSATButton(ticket.id, 5).callback(make_interaction(bot, user))
    assert thread.locked and thread.archived


async def test_archive_tolerates_missing_thread():
    bot = tickets_bot()
    ticket, thread, _ = await closed_ticket(bot)
    await thread.delete()
    await age(ticket, 25)
    assert await archive_expired_tickets(cast(Any, bot)) == 1
    assert (await tickets.require_ticket(ticket.id)).archived_at is not None
