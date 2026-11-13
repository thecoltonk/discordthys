"""STAFF_CHANNEL_ID is for #tickets only: #community-help tickets get no staff
card, but still count in /stats, /waiting and the daily summary. Cards posted
for community tickets before that change keep refreshing."""

from datetime import datetime
from datetime import timedelta
from datetime import UTC
from typing import Any
from typing import cast

import pytest

from nephthys.cogs.community_help import CommunityHelp
from nephthys.cogs.staff import Staff
from nephthys.core import tickets
from nephthys.core.copytext import DEFAULT_COPY as C
from nephthys.core.copytext import parse_copy
from nephthys.discord_ops import flows
from nephthys.discord_ops import staff_card
from nephthys.tasks.daily_summary import build_daily_summary
from nephthys.tasks.daily_summary import send_daily_summary
from nephthys.ui.buttons import DYNAMIC_ITEMS
from nephthys.ui.buttons import ReopenButton
from nephthys.ui.buttons import ResolveButton
from tests.fakes import FakeBot
from tests.fakes import make_interaction
from tests.fakes import make_message
from tests.fakes import make_user

pytestmark = pytest.mark.usefixtures("db")


def fields(embed: Any) -> dict[str, str]:
    return {f.name: f.value for f in embed.fields}


async def ask(
    bot: FakeBot,
    cog: CommunityHelp,
    author=None,
    content="How do I claim land?",
    *,
    at: datetime | None = None,
):
    author = author or make_user(name="op")
    msg = make_message(bot, author, content)
    if at is not None:
        msg.created_at = at
    await cog.on_message(msg)
    ticket = await tickets.get_ticket_by_message(msg.id)
    assert ticket
    return msg, ticket, author


async def fresh(ticket):
    return await tickets.require_ticket(ticket.id)


async def test_community_tickets_post_no_staff_card():
    bot = FakeBot()
    cog = CommunityHelp(cast(Any, bot))
    _, ticket, op = await ask(bot, cog)
    mod = make_user(staff=True, name="mod")
    thread = bot.world.threads[ticket.thread_id]
    await cog.on_message(make_message(bot, mod, "try /claim", channel=thread))
    await ResolveButton(ticket.id).callback(make_interaction(bot, op))
    await ReopenButton(ticket.id).callback(make_interaction(bot, op))

    assert bot.sends(bot.settings.staff_channel_id or 0) == []
    assert (await fresh(ticket)).staff_card_message_id is None
    # The category select is gone for good
    assert not any(
        "cat" in str(c.__discord_ui_compiled_template__) for c in DYNAMIC_ITEMS
    )  # type: ignore[attr-defined]


async def test_community_activity_counts_everywhere_without_cards():
    """Opened, claimed, resolved, top helpers and waiting all include
    #community-help tickets, and nothing is posted to the staff channel."""
    bot = FakeBot()
    cog = CommunityHelp(cast(Any, bot))
    mod = make_user(staff=True, name="mod")

    # Answered by staff and resolved by the poster
    _, answered, op = await ask(bot, cog, content="how do i claim?")
    thread = bot.world.threads[answered.thread_id]
    await cog.on_message(make_message(bot, mod, "try /claim", channel=thread))
    await ResolveButton(answered.id).callback(make_interaction(bot, op))
    # Claimed, still open
    _, claimed, _ = await ask(bot, cog, content="lag spikes")
    thread = bot.world.threads[claimed.thread_id]
    await cog.on_message(make_message(bot, mod, "looking into it", channel=thread))
    # Nobody answered for 4 days
    _, waiting, _ = await ask(
        bot,
        cog,
        content="where is spawn?",
        at=datetime.now(UTC) - timedelta(days=4),
    )
    assert bot.sends(bot.settings.staff_channel_id or 0) == []

    # /stats
    i = make_interaction(bot, mod)
    await cast(Any, Staff.stats).callback(Staff(cast(Any, bot)), i, 7)
    [stats] = i.followup.send.call_args.kwargs["embeds"]
    f = fields(stats)
    assert f["Opened"] == "3"
    assert f["Claimed"] == "2"
    assert f["Resolved"] == "1 (0 without staff)"
    assert f["Waiting > 3 days"] == "1"
    assert f["Top helpers (resolved, by claimer)"] == f"1. <@{mod.id}> — 1"

    # /waiting
    i = make_interaction(bot, mod)
    await cast(Any, Staff.waiting).callback(Staff(cast(Any, bot)), i)
    listed = i.followup.send.call_args.kwargs["embed"].description
    assert f"[#{waiting.id} where is spawn?]" in listed
    assert f"#{answered.id} " not in listed and f"#{claimed.id} " not in listed

    # Daily summary (the 4-day-old question is outside the 24h window but waiting)
    [summary] = await build_daily_summary(
        now=datetime.now(UTC) + timedelta(minutes=1),
        guild_id=bot.settings.guild_id,
        ai_enabled=False,
        ai_mode="shadow",
    )
    f = fields(summary)
    assert f["Opened"] == "2"
    assert f["Claimed"] == "2"
    assert f["Resolved"] == "1 (0 without staff)"
    assert f"[#{waiting.id} where is spawn?]" in f["Waiting > 3 days (1)"]
    assert f["Top helpers (resolved, by claimer)"] == f"1. <@{mod.id}> — 1"

    # The summary is the only thing posted to the staff channel
    await send_daily_summary(cast(Any, bot))
    [only] = bot.sends(bot.settings.staff_channel_id or 0)
    assert [e.title for e in only.embeds] == ["📊 #community-help daily summary"]


async def legacy_card(bot: FakeBot, ticket) -> Any:
    """A card posted for a community ticket before cards were #tickets-only."""
    channel = bot.get_partial_messageable(bot.settings.staff_channel_id or 0)
    embed, view = await staff_card.render(cast(Any, bot), await fresh(ticket))
    msg = await channel.send(embed=embed, view=view)
    await tickets.set_staff_card(ticket.id, channel.id, msg.id)
    return msg


async def test_legacy_community_card_still_tracks_status():
    bot = FakeBot()
    cog = CommunityHelp(cast(Any, bot))
    _, ticket, op = await ask(bot, cog)
    card = await legacy_card(bot, ticket)
    # Link only: no category select
    [link] = card.view.children
    assert link.url.endswith(f"/{ticket.thread_id}")

    mod = make_user(staff=True, name="mod")
    thread = bot.world.threads[ticket.thread_id]
    await cog.on_message(make_message(bot, mod, "try /claim", channel=thread))
    assert fields(card.embed)["Status"] == f"🔵 Claimed by <@{mod.id}>"

    await ResolveButton(ticket.id).callback(make_interaction(bot, op))
    assert (
        fields(card.embed)["Status"]
        == f"✅ Resolved by <@{op.id}> · credit <@{mod.id}>"
    )


async def test_legacy_community_card_deleted_with_ticket():
    bot = FakeBot()
    cog = CommunityHelp(cast(Any, bot))
    msg, ticket, _ = await ask(bot, cog)
    card = await legacy_card(bot, ticket)
    await flows.handle_question_deleted(cast(Any, bot), msg.id)
    assert (bot.settings.staff_channel_id, card.id) in bot.world.deleted_messages


def test_removed_copy_key_is_ignored_not_fatal():
    copy = parse_copy('[card]\ncategory_placeholder = "Pick one"\n')
    assert copy.card == C.card
