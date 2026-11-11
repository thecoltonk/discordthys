from datetime import datetime
from datetime import timedelta
from datetime import UTC

import pytest

from nephthys.core import tickets
from nephthys.core.queries import get_stale_tickets
from nephthys.core.queries import get_unanswered_tickets
from nephthys.core.stats import calculate_stats
from tests.factories import new_ticket
from tests.factories import player
from tests.factories import staff

pytestmark = pytest.mark.usefixtures("db")

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)


async def test_unanswered_and_stale_queries():
    old = await new_ticket(created_at=NOW - timedelta(days=5))
    answered = await new_ticket(created_at=NOW - timedelta(days=5))
    await tickets.record_message(answered, staff(), at=NOW - timedelta(days=4))
    fresh = await new_ticket(created_at=NOW - timedelta(hours=1))
    closed = await new_ticket(created_at=NOW - timedelta(days=9))
    await tickets.resolve(closed.id, None)

    waiting = await get_unanswered_tickets()
    assert [t.id for t in waiting] == [old.id, fresh.id]

    waiting_3d = await get_unanswered_tickets(
        last_message_before=NOW - timedelta(days=3)
    )
    assert [t.id for t in waiting_3d] == [old.id]

    stale = await get_stale_tickets(NOW - timedelta(days=3))
    assert {t.id for t in stale} == {old.id, answered.id}


async def test_calculate_stats():
    helper_a, helper_b = staff(name="a"), staff(name="b")
    since = NOW - timedelta(days=1)

    # Claimed after 10 minutes, resolved by OP: credit to helper_a
    op1 = player()
    t1 = await new_ticket(op1, created_at=NOW - timedelta(hours=10))
    await tickets.record_message(t1, helper_a, at=t1.created_at + timedelta(minutes=10))
    await tickets.resolve(t1.id, op1, at=NOW - timedelta(hours=2))

    # Claimed after 30 minutes by helper_a, resolved by helper_b: credit to helper_a
    t2 = await new_ticket(created_at=NOW - timedelta(hours=8))
    await tickets.record_message(t2, helper_a, at=t2.created_at + timedelta(minutes=30))
    await tickets.resolve(t2.id, helper_b, at=NOW - timedelta(hours=1))

    # Claimed after 50 minutes by helper_b, still in progress
    t3 = await new_ticket(created_at=NOW - timedelta(hours=6))
    await tickets.record_message(t3, helper_b, at=t3.created_at + timedelta(minutes=50))

    # Self-resolved without staff: no credit
    op4 = player()
    t4 = await new_ticket(op4, created_at=NOW - timedelta(hours=5))
    await tickets.resolve(t4.id, op4, at=NOW - timedelta(hours=4))

    # Open and waiting for 4 days (outside the window)
    await new_ticket(created_at=NOW - timedelta(days=4))

    s = await calculate_stats(since=since, until=NOW)
    assert s.opened == 4
    assert s.resolved == 3
    assert s.resolved_without_staff == 1
    assert s.median_first_response_minutes == 30
    assert s.open_now == 1
    assert s.in_progress_now == 1
    assert s.waiting_over_threshold == 1
    assert [(e.user.username, e.count) for e in s.top_claimers] == [("a", 2)]


async def test_stats_empty():
    s = await calculate_stats(since=NOW - timedelta(days=1), until=NOW)
    assert s.opened == 0 and s.resolved == 0
    assert s.median_first_response_minutes is None
    assert s.top_claimers == []
