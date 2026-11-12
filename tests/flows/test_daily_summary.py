from datetime import datetime
from datetime import timedelta
from datetime import UTC
from typing import Any
from typing import cast

import pytest

from nephthys.core import ai_store
from nephthys.core import tickets
from nephthys.core.users import upsert_user
from nephthys.database.tables import Ticket
from nephthys.tasks.daily_summary import build_daily_summary
from nephthys.tasks.daily_summary import send_daily_summary
from nephthys.tasks.daily_summary import waiting_text
from tests.factories import new_ticket
from tests.factories import player
from tests.factories import staff
from tests.fakes import FakeBot
from tests.helpers import make_settings

pytestmark = pytest.mark.usefixtures("db")

NOW = datetime(2026, 11, 12, 16, 0, tzinfo=UTC)


def fields(embed: Any) -> dict[str, str]:
    return {f.name: f.value for f in embed.fields}


async def seed():
    helper = staff(name="helper")
    op = player()
    t1 = await new_ticket(op, created_at=NOW - timedelta(hours=5))
    await tickets.record_message(t1, helper, at=t1.created_at + timedelta(minutes=20))
    await tickets.resolve(t1.id, op, at=NOW - timedelta(hours=1))
    await tickets.set_category(t1.id, "Bug", source="staff", allowed=("Bug",))
    old = await new_ticket(created_at=NOW - timedelta(days=5))
    shadow = await ai_store.record_attempt(
        ticket_id=t1.id,
        mode="shadow",
        model="m",
        answer="a",
        confidence=0.9,
        kb_sources=["faq.md"],
        posted=True,
        skip_reason=None,
    )
    rater = await upsert_user(staff())
    await ai_store.set_outcome(shadow.id, ai_store.HELPFUL, rater.id)
    # Make the AI rows fall inside the window
    from nephthys.database.tables import AISuggestion

    await AISuggestion.update(
        {AISuggestion.created_at: NOW - timedelta(hours=2)}
    ).where(AISuggestion.id == shadow.id)
    return t1, old


async def test_summary_contents():
    _t1, old = await seed()
    [embed] = await build_daily_summary(
        now=NOW, guild_id=100, ai_enabled=True, ai_mode="shadow"
    )
    f = fields(embed)
    assert f["Opened"] == "1"
    assert f["Resolved"] == "1 (0 without staff)"
    assert f["Median first staff response"] == "20 min"
    assert f["By category"] == "Bug 1"
    waiting = f["Waiting > 3 days (1)"]
    assert f"[#{old.id}" in waiting and f"/100/{old.thread_id})" in waiting
    assert "<@" in f["Top helpers (resolved, by claimer)"]
    assert "Shadow accuracy" in f["🤖 AI"] and "24h 100% (1/1)" in f["🤖 AI"]


async def test_summary_without_ai():
    await seed()
    [embed] = await build_daily_summary(
        now=NOW, guild_id=100, ai_enabled=False, ai_mode="shadow"
    )
    assert "🤖 AI" not in fields(embed)


async def test_send_daily_summary():
    await seed()
    bot = FakeBot()
    await send_daily_summary(cast(Any, bot), now=NOW)
    [msg] = bot.sends(bot.settings.staff_channel_id or 0)
    assert [e.title for e in msg.embeds] == ["📊 #community-help daily summary"]

    no_channel = FakeBot(make_settings(STAFF_CHANNEL_ID=""))
    await send_daily_summary(cast(Any, no_channel), now=NOW)
    assert not no_channel.world.channel_sends


async def test_waiting_text_truncates():
    many = [await new_ticket(created_at=NOW - timedelta(days=5)) for _ in range(12)]
    rows = (
        await Ticket.objects()
        .where(Ticket.id.is_in([t.id for t in many]))
        .order_by(Ticket.id)
    )
    text = waiting_text(rows, 100)
    assert text.count("\n• ") + 1 == 10
    assert "…and 2 more" in text
    assert len(text) <= 1024
    assert waiting_text([], 100) == "none 🎉"
