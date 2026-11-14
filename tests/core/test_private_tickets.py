from datetime import datetime
from datetime import timedelta
from datetime import UTC

import pytest

from nephthys.core import private_tickets as pt
from nephthys.core import tickets
from nephthys.core.stats import calculate_stats
from nephthys.database.enums import TicketStatus
from nephthys.database.enums import TicketSurface
from nephthys.errors.errors import PrivateTicketReopenError
from tests.factories import new_ticket
from tests.factories import player
from tests.factories import snowflake
from tests.factories import staff

pytestmark = pytest.mark.usefixtures("db")


async def private(author=None, number: int | None = None, **kw):
    n = number if number is not None else await pt.allocate_number(26)
    return await pt.create_private_ticket(
        author=author or player(), channel_id=600, thread_id=snowflake(), number=n, **kw
    )


async def test_numbers_start_at_26_and_never_go_backwards():
    await pt.align_number_sequence(26)
    assert [await pt.allocate_number(26) for _ in range(3)] == [26, 27, 28]
    await pt.align_number_sequence(26)  # restart: continues, doesn't reuse
    assert await pt.allocate_number(26) == 29
    await pt.align_number_sequence(100)  # raising the start jumps ahead
    assert await pt.allocate_number(100) == 100


async def test_allocate_without_alignment_still_respects_start():
    assert await pt.allocate_number(26) == 26


async def test_alignment_skips_past_existing_numbers():
    await private(number=40)
    await pt.align_number_sequence(26)
    assert await pt.allocate_number(26) == 41


async def test_private_ticket_basics():
    op = player()
    t = await private(op)
    assert t.surface == TicketSurface.PRIVATE and t.number == 26
    assert t.message_id is None and t.description == "" and t.title is None
    assert await pt.count_open_private(op.discord_id) == 1
    assert [x.id for x in await pt.open_private_tickets(op.discord_id)] == [t.id]
    await tickets.resolve(t.id, op)
    assert await pt.count_open_private(op.discord_id) == 0
    assert await pt.count_open_private(snowflake()) == 0


async def test_category_flow_first_pick_wins():
    t = await private()
    assert await pt.pick_category(t.id, "server_issue", leaf_priority=None)
    assert not await pt.pick_category(t.id, "store", leaf_priority="medium")
    assert not await pt.pick_subcategory(t.id, "store", "urgent", priority="high")
    assert await pt.pick_subcategory(t.id, "server_issue", "urgent", priority="high")
    assert not await pt.pick_subcategory(
        t.id, "server_issue", "bug_report", priority="medium"
    )
    t = await tickets.require_ticket(t.id)
    assert (t.category, t.subcategory, t.priority, t.category_source) == (
        "server_issue",
        "urgent",
        "high",
        "player",
    )


async def test_leaf_pick_sets_priority_and_closed_tickets_refuse():
    t = await private()
    assert await pt.pick_category(t.id, "store", leaf_priority="medium")
    assert (await tickets.require_ticket(t.id)).priority == "medium"
    closed = await private()
    await tickets.resolve(closed.id, None)
    assert not await pt.pick_category(closed.id, "store", leaf_priority="medium")


async def test_first_message():
    t = await private()
    assert await pt.set_first_message(
        t.id, "  my rank is missing\nbought it yesterday "
    )
    assert not await pt.set_first_message(t.id, "second message")
    assert not await pt.set_first_message(t.id, "   ")
    t = await tickets.require_ticket(t.id)
    assert t.title == "my rank is missing"
    assert t.description == "my rank is missing\nbought it yesterday"


async def test_escalation_link():
    community = await new_ticket()
    escalated = await private(escalated_from=community.id)
    found = await pt.escalation_of(community.id)
    assert found and found.id == escalated.id
    assert await pt.escalation_of(escalated.id) is None


async def test_private_stats():
    now = datetime.now(UTC)
    ops = [player() for _ in range(3)]
    ts = [
        await private(op, created_at=now - timedelta(hours=i + 1))
        for i, op in enumerate(ops)
    ]
    await pt.pick_category(ts[0].id, "store", leaf_priority="medium")
    await pt.pick_category(ts[1].id, "server_issue", leaf_priority=None)
    await pt.pick_subcategory(ts[1].id, "server_issue", "urgent", priority="high")
    for t, op in ((ts[0], ops[0]), (ts[1], ops[1])):
        await tickets.resolve(t.id, op)
    await new_ticket(created_at=now - timedelta(hours=1))  # community: excluded

    s = await calculate_stats(
        since=now - timedelta(days=1),
        until=now + timedelta(minutes=1),
        surface=TicketSurface.PRIVATE,
    )
    assert s.opened == 3
    assert s.by_category == {"server_issue": 1, "store": 1, "Uncategorised": 1}
    assert s.by_priority == {"high": 1, "medium": 1, "unset": 1}
    assert s.resolved == 2

    community = await calculate_stats(
        since=now - timedelta(days=1), until=now + timedelta(minutes=1)
    )
    assert community.opened == 1


async def test_status_flow_reuses_core():
    op, mod = player(), staff()
    t = await private(op)
    r = await tickets.record_message(t, mod)
    assert r.claimed
    res = await tickets.resolve(t.id, op)
    assert res.credited_to and res.credited_to.discord_id == mod.discord_id
    # Closed private tickets stay closed, for staff and the opener alike
    for who in (mod, op):
        with pytest.raises(PrivateTicketReopenError):
            await tickets.reopen(t.id, who)
    assert (await tickets.require_ticket(t.id)).status == TicketStatus.CLOSED
