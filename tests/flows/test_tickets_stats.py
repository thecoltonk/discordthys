"""#tickets in /stats, /waiting and the daily summary."""

from datetime import datetime
from datetime import timedelta
from datetime import UTC
from typing import Any
from typing import cast

import pytest

from nephthys.cogs.staff import Staff
from nephthys.core import private_tickets as pt
from nephthys.core import tickets
from nephthys.database.tables import Ticket
from nephthys.discord_ops.formatting import csat_text
from nephthys.discord_ops.formatting import priorities_text
from nephthys.discord_ops.formatting import ticket_ref
from nephthys.tasks.daily_summary import build_daily_summary
from nephthys.tasks.daily_summary import send_daily_summary
from tests.factories import new_ticket
from tests.factories import player
from tests.factories import snowflake
from tests.factories import staff
from tests.fakes import FakeBot
from tests.fakes import make_interaction
from tests.fakes import make_user
from tests.helpers import make_settings

pytestmark = pytest.mark.usefixtures("db")


def now() -> datetime:
    # Read the clock per call: a module-level timestamp goes stale when the suite
    # runs long, and CSAT is stamped with the real time when it's given
    return datetime.now(UTC)


def fields(embed: Any) -> dict[str, str]:
    return {f.name: f.value for f in embed.fields}


async def seed() -> list[Ticket]:
    start = now()
    """Three private tickets (store/medium rated 5, urgent/high rated 3, one
    unpicked and waiting 4 days) and one community ticket."""
    await pt.align_number_sequence(26)
    mod = staff(name="mod")
    made = []
    for hours_ago in (2, 3, 96):
        op = player()
        t = await pt.create_private_ticket(
            author=op,
            channel_id=600,
            thread_id=snowflake(),
            number=await pt.allocate_number(26),
            created_at=start - timedelta(hours=hours_ago),
        )
        made.append((t, op))
    (store, store_op), (urgent, urgent_op), _ = made
    await pt.pick_category(store.id, "store", leaf_priority="medium")
    await pt.pick_category(urgent.id, "server_issue", leaf_priority=None)
    await pt.pick_subcategory(urgent.id, "server_issue", "urgent", priority="high")
    await tickets.record_message(
        store, mod, at=store.created_at + timedelta(minutes=10)
    )
    for t, op, rating in ((store, store_op, 5), (urgent, urgent_op, 3)):
        await tickets.resolve(t.id, op)
        await pt.rate_csat(t.id, rating, op)
    await new_ticket(created_at=start - timedelta(hours=1))
    return [t for t, _ in made]


def test_formatting_helpers():
    assert ticket_ref(Ticket(id=5, surface="PRIVATE", number=31)) == "T-31"
    assert ticket_ref(Ticket(id=5, surface="COMMUNITY")) == "#5"
    assert priorities_text({"high": 1, "medium": 2, "unset": 1}) == (
        "🔴 High 1, 🟡 Medium 2, not picked yet 1"
    )
    assert csat_text(4.25, 4) == "4.2/5 from 4 rating(s)"
    assert csat_text(None, 0) == "no ratings yet"


async def test_stats_includes_tickets_when_enabled():
    await seed()
    bot = FakeBot(make_settings(TICKETS_CHANNEL_ID="600"))
    cog = Staff(cast(Any, bot))
    i = make_interaction(bot, make_user(staff=True))
    await cast(Any, Staff.stats).callback(cog, i, 7)
    community, private = i.followup.send.call_args.kwargs["embeds"]
    assert community.title == "#community-help — last 7 days"
    assert fields(community)["Opened"] == "1"
    assert private.title == "#tickets — last 7 days"
    f = fields(private)
    assert f["Opened"] == "3"
    assert f["CSAT average"] == "4.0/5 from 2 rating(s)"
    assert f["By priority"] == "🔴 High 1, 🟡 Medium 1, not picked yet 1"
    assert f["Opened by category"] == "❗ Server Issue 1, 🛒 Store 1, Uncategorised 1"
    assert f["Median first staff response"] == "10 min"

    off = FakeBot()
    i = make_interaction(off, make_user(staff=True))
    await cast(Any, Staff.stats).callback(Staff(cast(Any, off)), i, 1)
    [only] = i.followup.send.call_args.kwargs["embeds"]
    assert only.title == "#community-help — last 1 day"


async def test_waiting_lists_private_tickets_with_t_numbers():
    made = await seed()
    bot = FakeBot(make_settings(TICKETS_CHANNEL_ID="600"))
    i = make_interaction(bot, make_user(staff=True))
    await cast(Any, Staff.waiting).callback(Staff(cast(Any, bot)), i)
    description = i.followup.send.call_args.kwargs["embed"].description
    waiting_private = made[2]
    assert f"[T-{waiting_private.number} " in description
    assert f"/{waiting_private.thread_id})" in description


async def test_daily_summary_has_a_tickets_embed():
    made = await seed()
    community, private = await build_daily_summary(
        now=now() + timedelta(minutes=1),
        guild_id=100,
        ai_enabled=False,
        ai_mode="shadow",
        tickets_enabled=True,
    )
    assert community.title == "📊 #community-help daily summary"
    assert private.title == "📊 #tickets daily summary"
    f = fields(private)
    assert f["Opened"] == "2"  # the 4-day-old one is outside the window
    assert f["CSAT average"] == "4.0/5 from 2 rating(s)"
    assert f["By priority"] == "🔴 High 1, 🟡 Medium 1"
    assert f["By category"] == "❗ Server Issue 1, 🛒 Store 1"
    waiting = f["Waiting > 3 days (1)"]
    assert f"[T-{made[2].number} " in waiting
    assert "🤖 AI" not in f

    bot = FakeBot(make_settings(TICKETS_CHANNEL_ID="600"))
    await send_daily_summary(cast(Any, bot), now=now())
    [msg] = bot.sends(bot.settings.staff_channel_id or 0)
    assert len(msg.embeds) == 2 and msg.content is None
