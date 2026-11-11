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
from nephthys.database.enums import TicketStatus
from nephthys.database.tables import StickyMessage
from nephthys.database.tables import Ticket
from nephthys.tasks.close_stale import close_stale_tickets
from tests.fakes import FakeBot
from tests.fakes import make_interaction
from tests.fakes import make_message
from tests.fakes import make_user
from tests.helpers import make_settings

pytestmark = pytest.mark.usefixtures("db")


def sticky_bot() -> FakeBot:
    return FakeBot(make_settings(STICKY_ENABLED="true", STICKY_DEBOUNCE_SECONDS="0.5"))


def channel_embeds(bot: FakeBot) -> list[Any]:
    return [
        m
        for m in bot.sends(bot.settings.community_channel_id)
        if getattr(m, "embed", None)
    ]


async def test_sticky_is_debounced_and_replaces_previous():
    bot = sticky_bot()
    cog = CommunityHelp(cast(Any, bot))
    assert cog.sticky is not None

    for _ in range(3):
        await cog.on_message(make_message(bot, make_user()))
    await cog.sticky.wait_idle()

    stickies = channel_embeds(bot)
    assert len(stickies) == 1, "three quick questions should give one repost"
    assert "https://example.com/faq" in stickies[0].embed.description
    row = await StickyMessage.objects().first()
    assert row and row.message_id == stickies[0].id

    await cog.on_message(make_message(bot, make_user()))
    await cog.sticky.wait_idle()
    stickies = channel_embeds(bot)
    assert len(stickies) == 2
    # Old sticky deleted after the new one was posted
    assert (
        bot.settings.community_channel_id,
        stickies[0].id,
    ) in bot.world.deleted_messages
    row = await StickyMessage.objects().first()
    assert row and row.message_id == stickies[1].id


async def test_sticky_never_creates_a_ticket():
    bot = sticky_bot()
    cog = CommunityHelp(cast(Any, bot))
    assert cog.sticky is not None
    await cog.sticky.ensure()
    [sticky] = channel_embeds(bot)
    as_message = make_message(bot, make_user(bot=True), "How #community-help works")
    as_message.id = sticky.id
    await cog.on_message(as_message)
    as_message.create_thread.assert_not_awaited()
    assert await Ticket.count() == 0


async def test_activity_during_repost_triggers_another_repost():
    bot = sticky_bot()
    cog = CommunityHelp(cast(Any, bot))
    sticky = cog.sticky
    assert sticky is not None
    original = sticky.repost
    calls = 0

    async def slow_repost():
        nonlocal calls
        calls += 1
        if calls == 1:
            sticky.schedule()  # a new question lands mid-repost
        await original()

    sticky.repost = slow_repost  # type: ignore[method-assign]
    sticky.schedule()
    await sticky.wait_idle()
    assert calls == 2


async def test_sticky_disabled():
    cog = CommunityHelp(cast(Any, FakeBot()))
    assert cog.sticky is None


async def test_close_stale_tickets():
    bot = FakeBot(make_settings(STALE_TICKET_DAYS="3"))
    cog = CommunityHelp(cast(Any, bot))
    old_q = make_message(bot, make_user())
    new_q = make_message(bot, make_user())
    await cog.on_message(old_q)
    await cog.on_message(new_q)
    old = await tickets.get_ticket_by_message(old_q.id)
    assert old
    await Ticket.update(
        {Ticket.last_msg_at: datetime.now(UTC) - timedelta(days=4)}
    ).where(Ticket.id == old.id)

    closed = await close_stale_tickets(cast(Any, bot), delay=0)
    assert closed == 1
    t = await tickets.require_ticket(old.id)
    assert t.status == TicketStatus.CLOSED and t.closed_by is None
    assert bot.sends(old.thread_id)[-1].content == C.community.resolved_stale.format(
        days=3
    )
    assert bot.world.threads[old.thread_id].archived
    new = await tickets.get_ticket_by_message(new_q.id)
    assert new and new.status == TicketStatus.OPEN


async def test_close_stale_disabled():
    assert await close_stale_tickets(cast(Any, FakeBot()), delay=0) == 0


async def test_waiting_and_stats_commands():
    bot = FakeBot()
    help_cog = CommunityHelp(cast(Any, bot))
    staff_cog = Staff(cast(Any, bot))
    await help_cog.on_message(make_message(bot, make_user(), "first question"))

    player = make_interaction(bot, make_user())
    await cast(Any, Staff.waiting).callback(staff_cog, player)
    player.response.send_message.assert_awaited_once()
    assert player.response.send_message.call_args.kwargs["ephemeral"] is True

    mod = make_interaction(bot, make_user(staff=True))
    await cast(Any, Staff.waiting).callback(staff_cog, mod)
    embed = mod.followup.send.call_args.kwargs["embed"]
    assert "first question" in embed.description

    mod2 = make_interaction(bot, make_user(staff=True))
    await cast(Any, Staff.stats).callback(staff_cog, mod2, 7)
    embed = mod2.followup.send.call_args.kwargs["embed"]
    fields = {f.name: f.value for f in embed.fields}
    assert fields["Opened"] == "1"


async def test_waiting_empty():
    bot = FakeBot()
    staff_cog = Staff(cast(Any, bot))
    mod = make_interaction(bot, make_user(staff=True))
    await cast(Any, Staff.waiting).callback(staff_cog, mod)
    assert "nothing waiting" in mod.followup.send.call_args.args[0]
