from datetime import datetime
from datetime import timedelta
from datetime import UTC

import pytest

from nephthys.core import ai_store
from nephthys.core import tickets
from nephthys.core.stats import calculate_stats
from nephthys.core.users import upsert_user
from tests.factories import new_ticket
from tests.factories import player
from tests.factories import staff

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


async def attempt(
    ticket_id: int, mode: str, *, posted: bool = True, skip: str | None = None
):
    return await ai_store.record_attempt(
        ticket_id=ticket_id,
        mode=mode,
        model="m",
        answer="a" if posted else None,
        confidence=0.9,
        kb_sources=["faq.md"],
        posted=posted,
        skip_reason=skip,
    )


async def test_outcomes_are_set_once_and_validated():
    t = await new_ticket()
    rater = await upsert_user(staff())
    s = await attempt(t.id, "shadow")
    assert list(s.kb_sources) == ["faq.md"]
    with pytest.raises(ValueError):
        await ai_store.set_outcome(
            s.id, ai_store.FIXED, rater.id
        )  # public-only outcome
    updated = await ai_store.set_outcome(s.id, ai_store.HELPFUL, rater.id)
    assert updated.outcome == ai_store.HELPFUL and updated.rated_at is not None
    with pytest.raises(ai_store.OutcomeAlreadySetError):
        await ai_store.set_outcome(s.id, ai_store.NOT_HELPFUL, rater.id)
    with pytest.raises(LookupError):
        await ai_store.set_outcome(999_999, ai_store.HELPFUL, rater.id)
    assert (await ai_store.latest_posted_for_ticket(t.id)).id == s.id  # type: ignore[union-attr]


async def test_accuracy():
    t = await new_ticket()
    rater = await upsert_user(staff())
    shadow = [await attempt(t.id, "shadow") for _ in range(4)]
    await ai_store.set_outcome(shadow[0].id, ai_store.HELPFUL, rater.id)
    await ai_store.set_outcome(shadow[1].id, ai_store.HELPFUL, rater.id)
    await ai_store.set_outcome(shadow[2].id, ai_store.NOT_HELPFUL, rater.id)
    public = [await attempt(t.id, "public") for _ in range(3)]
    await ai_store.set_outcome(public[0].id, ai_store.FIXED, rater.id)
    await ai_store.set_outcome(public[1].id, ai_store.NEEDS_HUMAN, rater.id)
    await attempt(t.id, "shadow", posted=False, skip="low_confidence")
    await attempt(t.id, "shadow", posted=False, skip="no_answer")

    now = datetime.now(UTC)
    acc = await ai_store.accuracy(now - timedelta(hours=1), now + timedelta(seconds=1))
    assert acc.attempts == 9
    assert acc.skipped == {"low_confidence": 1, "no_answer": 1}
    assert (acc.shadow_posted, acc.helpful, acc.not_helpful, acc.shadow_unrated) == (
        4,
        2,
        1,
        1,
    )
    assert acc.shadow_accuracy == pytest.approx(2 / 3)
    assert (acc.public_posted, acc.fixed, acc.needs_human, acc.public_no_response) == (
        3,
        1,
        1,
        1,
    )
    assert acc.public_fix_rate == 0.5

    empty = await ai_store.accuracy(now - timedelta(days=9), now - timedelta(days=8))
    assert empty.shadow_accuracy is None and empty.public_fix_rate is None
