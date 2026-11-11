from datetime import timedelta

import pytest

from nephthys.core import tickets
from nephthys.core.permissions import can_resolve
from nephthys.core.permissions import is_staff
from nephthys.database.enums import BotMessageKind
from nephthys.database.enums import TicketStatus
from nephthys.database.enums import UserType
from nephthys.database.tables import User
from nephthys.errors.errors import PermissionDenied
from nephthys.errors.errors import TicketAlreadyClosedError
from nephthys.errors.errors import TicketNotClosedError
from tests.factories import new_ticket
from tests.factories import player
from tests.factories import staff

pytestmark = pytest.mark.usefixtures("db")


def test_permissions():
    op = player(1)
    assert is_staff([1, 2, 300], 300)
    assert not is_staff([1, 2], 300)
    assert can_resolve(op, 1)
    assert not can_resolve(player(2), 1)
    assert can_resolve(staff(3), 1)


async def test_create_ticket_upserts_user():
    op = player(name="alice")
    ticket = await tickets.create_ticket(
        author=op,
        channel_id=200,
        message_id=11,
        thread_id=11,
        title="t",
        description="d",
    )
    assert ticket.status == TicketStatus.OPEN
    assert ticket.last_msg_by == UserType.AUTHOR
    user = await User.objects().where(User.discord_id == op.discord_id).first()
    assert user and user.username == "alice"
    assert await tickets.get_ticket_by_thread(11)
    assert await tickets.get_ticket_by_message(11)
    assert await tickets.count_tickets_opened_by(op.discord_id) == 1


async def test_username_and_staff_flag_refresh_on_upsert():
    actor = player(name="old")
    await new_ticket(actor)
    await new_ticket(type(actor)(actor.discord_id, "new", True))
    user = await User.objects().where(User.discord_id == actor.discord_id).first()
    assert user and user.username == "new" and user.helper is True


async def test_first_staff_reply_claims_and_later_staff_do_not_steal():
    ticket = await new_ticket()
    first, second = staff(name="first"), staff(name="second")

    r1 = await tickets.record_message(ticket, first)
    assert r1.claimed
    assert r1.ticket.status == TicketStatus.IN_PROGRESS
    assert r1.ticket.last_msg_by == UserType.HELPER
    claimed_at = r1.ticket.assigned_at

    r2 = await tickets.record_message(r1.ticket, second)
    assert not r2.claimed
    first_user = await User.objects().where(User.discord_id == first.discord_id).first()
    assert first_user
    assert tickets.fk_id(r2.ticket.assigned_to) == first_user.id
    assert r2.ticket.assigned_at == claimed_at


async def test_non_staff_replies_never_claim():
    ticket = await new_ticket()
    r = await tickets.record_message(ticket, player())
    assert not r.claimed
    assert r.ticket.assigned_to is None
    assert r.ticket.status == TicketStatus.OPEN
    assert r.ticket.last_msg_by == UserType.OTHER


async def test_staff_poster_does_not_claim_own_ticket():
    op = staff()
    ticket = await new_ticket(op)
    r = await tickets.record_message(ticket, op)
    assert not r.claimed
    assert r.ticket.last_msg_by == UserType.AUTHOR


async def test_claim_is_atomic():
    ticket = await new_ticket()
    a = await tickets.upsert_user(staff())
    b = await tickets.upsert_user(staff())
    assert await tickets.claim(ticket.id, a.id)
    assert not await tickets.claim(ticket.id, b.id)


async def test_resolve_by_op_without_staff_gives_no_credit():
    op = player()
    ticket = await new_ticket(op)
    result = await tickets.resolve(ticket.id, op)
    assert result.ticket.status == TicketStatus.CLOSED
    assert result.credited_to is None
    assert result.resolved_by and result.resolved_by.discord_id == op.discord_id
    assert result.ticket.closed_at is not None


async def test_resolve_credits_claimer_even_when_someone_else_resolves():
    op, claimer, other_staff = player(), staff(name="claimer"), staff(name="other")
    ticket = await new_ticket(op)
    await tickets.record_message(ticket, claimer)

    result = await tickets.resolve(ticket.id, other_staff)
    assert result.credited_to and result.credited_to.discord_id == claimer.discord_id
    assert (
        result.resolved_by and result.resolved_by.discord_id == other_staff.discord_id
    )


async def test_staff_resolving_unclaimed_ticket_gets_no_credit():
    ticket = await new_ticket()
    result = await tickets.resolve(ticket.id, staff())
    assert result.credited_to is None


async def test_resolve_permissions_and_double_resolve():
    ticket = await new_ticket()
    with pytest.raises(PermissionDenied):
        await tickets.resolve(ticket.id, player())
    await tickets.resolve(ticket.id, staff())
    with pytest.raises(TicketAlreadyClosedError):
        await tickets.resolve(ticket.id, staff())


async def test_system_resolve_skips_permissions():
    ticket = await new_ticket()
    result = await tickets.resolve(ticket.id, None)
    assert result.resolved_by is None
    assert result.ticket.closed_by is None


async def test_reopen_keeps_claim_and_restores_status():
    op, helper = player(), staff()
    ticket = await new_ticket(op)
    await tickets.record_message(ticket, helper)
    await tickets.resolve(ticket.id, op)

    reopened = await tickets.reopen(ticket.id, op)
    assert reopened.status == TicketStatus.IN_PROGRESS
    assert reopened.assigned_to is not None
    assert reopened.closed_at is None and reopened.closed_by is None
    assert reopened.reopened_at is not None


async def test_reopen_unclaimed_goes_back_to_open():
    op = player()
    ticket = await new_ticket(op)
    await tickets.resolve(ticket.id, op)
    reopened = await tickets.reopen(ticket.id, staff())
    assert reopened.status == TicketStatus.OPEN


async def test_reopen_errors():
    op = player()
    ticket = await new_ticket(op)
    with pytest.raises(TicketNotClosedError):
        await tickets.reopen(ticket.id, op)
    await tickets.resolve(ticket.id, op)
    with pytest.raises(PermissionDenied):
        await tickets.reopen(ticket.id, player())


async def test_op_reply_after_resolve_requests_reopen():
    op = player()
    ticket = await new_ticket(op)
    await tickets.resolve(ticket.id, op)
    ticket = await tickets.require_ticket(ticket.id)

    other = await tickets.record_message(ticket, player())
    assert not other.should_reopen
    staff_reply = await tickets.record_message(ticket, staff())
    assert not staff_reply.should_reopen
    assert not staff_reply.claimed  # can't claim a closed ticket

    mine = await tickets.record_message(ticket, op)
    assert mine.should_reopen


async def test_record_message_updates_last_message_time():
    ticket = await new_ticket()
    at = ticket.created_at + timedelta(hours=5)
    r = await tickets.record_message(ticket, player(), at=at)
    assert r.ticket.last_msg_at == at


async def test_bot_messages_and_delete_cascade():
    ticket = await new_ticket()
    await tickets.record_bot_message(ticket.id, 200, 1, BotMessageKind.AUTO_REPLY)
    await tickets.record_bot_message(ticket.id, 200, 1, BotMessageKind.AUTO_REPLY)
    await tickets.record_bot_message(ticket.id, 200, 2, BotMessageKind.RESOLVED)
    assert len(await tickets.get_bot_messages(ticket.id)) == 2
    resolved = await tickets.get_bot_messages(ticket.id, BotMessageKind.RESOLVED)
    assert [m.message_id for m in resolved] == [2]

    await tickets.delete_ticket(ticket.id)
    assert await tickets.get_ticket(ticket.id) is None
    assert await tickets.get_bot_messages(ticket.id) == []
