from datetime import datetime
from datetime import timedelta
from datetime import UTC

import pytest

from nephthys.core import tickets
from nephthys.core.stats import calculate_stats
from tests.factories import new_ticket
from tests.factories import player

pytestmark = pytest.mark.usefixtures("db")

CATS = ("Gameplay", "Bug", "Claims", "Economy", "Store", "Other")


async def test_staff_category_and_ai_never_overrides():
    t = await new_ticket()
    assert await tickets.set_ai_category(t.id, "Bug", allowed=CATS)
    t = await tickets.require_ticket(t.id)
    assert (t.category, t.category_source) == ("Bug", "ai")

    # Staff override always wins; AI can't overwrite afterwards
    t = await tickets.set_category(t.id, "Claims", source="staff", allowed=CATS)
    assert (t.category, t.category_source) == ("Claims", "staff")
    assert not await tickets.set_ai_category(t.id, "Economy", allowed=CATS)
    assert (await tickets.require_ticket(t.id)).category == "Claims"


async def test_invalid_category_rejected():
    t = await new_ticket()
    with pytest.raises(tickets.InvalidCategoryError):
        await tickets.set_category(t.id, "Minigames", source="staff", allowed=CATS)
    with pytest.raises(tickets.InvalidCategoryError):
        await tickets.set_ai_category(t.id, "Minigames", allowed=CATS)


async def test_count_past_tickets():
    op = player()
    first = await new_ticket(op)
    await new_ticket()  # someone else
    second = await new_ticket(op)
    assert await tickets.count_past_tickets(first) == 0
    assert await tickets.count_past_tickets(second) == 1


async def test_stats_by_category():
    now = datetime.now(UTC)
    a = await new_ticket(created_at=now - timedelta(hours=1))
    b = await new_ticket(created_at=now - timedelta(hours=2))
    await new_ticket(created_at=now - timedelta(hours=3))
    await tickets.set_category(a.id, "Bug", source="staff", allowed=CATS)
    await tickets.set_category(b.id, "Bug", source="staff", allowed=CATS)
    s = await calculate_stats(since=now - timedelta(days=1), until=now)
    assert s.by_category == {"Bug": 2, "Uncategorised": 1}
