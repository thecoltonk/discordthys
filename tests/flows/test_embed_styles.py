"""[embeds.*] styles reach every embed; defaults keep the old look; opt-in
opener/closing embeds; the daily summary is @silent."""

from datetime import datetime
from datetime import UTC
from typing import Any
from typing import cast

import discord
import pytest

from nephthys.cogs.community_help import CommunityHelp
from nephthys.cogs.staff import Staff
from nephthys.cogs.staff import stats_embed
from nephthys.cogs.tickets import panel_embed
from nephthys.core import tickets
from nephthys.core.copytext import DEFAULT_COPY as C
from nephthys.core.copytext import parse_copy
from nephthys.core.copytext import use_copy
from nephthys.core.stats import StatsSummary
from nephthys.database.tables import AISuggestion
from nephthys.discord_ops.ai_flow import public_embed
from nephthys.discord_ops.ai_flow import shadow_embed
from nephthys.discord_ops.sticky import build_sticky_embed
from nephthys.tasks.daily_summary import send_daily_summary
from nephthys.ui.ticket_buttons import CloseTicketButton
from tests.fakes import FakeBot
from tests.fakes import make_interaction
from tests.fakes import make_message
from tests.fakes import make_user
from tests.flows.test_tickets import create
from tests.flows.test_tickets import pick
from tests.flows.test_tickets import tickets_bot

pytestmark = pytest.mark.usefixtures("db")

THUMB = "https://cdn.example.org/logo.png"


def colour(embed: discord.Embed) -> int | None:
    return embed.colour.value if embed.colour else None


async def community_card(bot: FakeBot):
    cog = CommunityHelp(cast(Any, bot))
    q = make_message(bot, make_user(name="op"), "how do i claim?")
    await cog.on_message(q)
    ticket = await tickets.get_ticket_by_message(q.id)
    assert ticket
    card = bot.message(
        bot.settings.staff_channel_id or 0, ticket.staff_card_message_id or 0
    )
    return cog, ticket, card


def fake_suggestion() -> AISuggestion:
    return AISuggestion(
        id=1,
        ticket=1,
        mode="shadow",
        model="m",
        answer="use /claim",
        confidence=0.9,
        kb_sources=["faq.md"],
    )


async def test_defaults_keep_the_old_look():
    bot = FakeBot()
    sticky = build_sticky_embed("https://faq")
    assert (sticky.title, colour(sticky)) == ("How #community-help works", 0x5865F2)
    assert sticky.thumbnail.url is None and sticky.footer.text is None
    panel = panel_embed(200)
    assert (panel.title, colour(panel)) == ("Need help?", 0x5865F2)

    _, ticket, card = await community_card(bot)
    assert colour(card.embed) == 0xF1C40F  # waiting: gold
    assert card.embed.footer.text == f"Ticket #{ticket.id}"
    assert card.embed.title == f"#{ticket.id} · how do i claim?"
    assert card.embed.description == "how do i claim?"

    s = stats_embed(StatsSummary(since=datetime.now(UTC), until=datetime.now(UTC)), 7)
    assert (s.title, colour(s)) == ("#community-help — last 7 days", 0x5865F2)

    ticket_ = tickets.Ticket(id=3, title="t", thread_id=9)
    shadow = shadow_embed(ticket_, fake_suggestion(), 100)
    assert (
        colour(shadow) == 0x11806A and shadow.footer.text == C.embeds.ai_shadow.footer
    )
    public = public_embed(fake_suggestion())
    assert public.title == "🤖 Automated answer" and "Sources: faq.md" in (
        public.footer.text or ""
    )

    i = make_interaction(bot, make_user(staff=True))
    await cast(Any, Staff.waiting).callback(Staff(cast(Any, bot)), i)
    waiting = i.followup.send.call_args.kwargs["embed"]
    assert (waiting.title, colour(waiting)) == ("Waiting for staff", 0xE67E22)


async def test_custom_styles_reach_every_embed():
    use_copy(
        parse_copy(
            f"""
[embeds.sticky]
title = "Read me"
color = "#000001"
footer = "sticky footer"
thumbnail_url = "{THUMB}"

[embeds.tickets_panel]
color = "#000002"
footer = "panel footer"
thumbnail_url = "{THUMB}"

[embeds.staff_card]
title = "Q{{id}}: {{title}}"
description = ">>> {{question}}"
color_waiting = "#000003"
color_claimed = "#000004"
footer = "{{ref}}"
thumbnail_url = "{THUMB}"

[embeds.stats]
description = "window: {{days}}d"
color = "#000005"
thumbnail_url = "{THUMB}"

[embeds.waiting]
title = "Queue"
description = "Oldest first:\\n{{lines}}"
color = ""

[embeds.daily_summary]
footer = "summary footer"
thumbnail_url = "{THUMB}"

[embeds.ai_public]
footer = "bot answer · {{sources}}"
color = "#000006"
"""
        )
    )
    bot = FakeBot()
    sticky = build_sticky_embed("")
    assert (sticky.title, colour(sticky), sticky.footer.text, sticky.thumbnail.url) == (
        "Read me",
        1,
        "sticky footer",
        THUMB,
    )
    panel = panel_embed(200)
    assert (colour(panel), panel.footer.text, panel.thumbnail.url) == (
        2,
        "panel footer",
        THUMB,
    )

    cog, ticket, card = await community_card(bot)
    assert card.embed.title == f"Q{ticket.id}: how do i claim?"
    assert card.embed.description == ">>> how do i claim?"
    assert (colour(card.embed), card.embed.footer.text, card.embed.thumbnail.url) == (
        3,
        f"#{ticket.id}",
        THUMB,
    )
    mod = make_user(staff=True)
    await cog.on_message(
        make_message(bot, mod, "hi", channel=bot.world.threads[ticket.thread_id])
    )
    assert colour(card.embed) == 4  # claimed colour

    s = stats_embed(StatsSummary(since=datetime.now(UTC), until=datetime.now(UTC)), 7)
    assert (s.description, colour(s), s.thumbnail.url) == ("window: 7d", 5, THUMB)

    await cog.on_message(make_message(bot, make_user(), "still waiting"))
    i = make_interaction(bot, mod)
    await cast(Any, Staff.waiting).callback(Staff(cast(Any, bot)), i)
    waiting = i.followup.send.call_args.kwargs["embed"]
    assert waiting.title == "Queue" and waiting.description.startswith(
        "Oldest first:\n• [#"
    )
    assert waiting.colour is None  # "" = no colour

    await send_daily_summary(cast(Any, bot))
    summary = bot.sends(bot.settings.staff_channel_id or 0)[-1]
    assert all(
        e.footer.text == "summary footer" and e.thumbnail.url == THUMB
        for e in summary.embeds
    )

    public = public_embed(fake_suggestion())
    assert (public.footer.text, colour(public)) == ("bot answer · faq.md", 6)


async def test_daily_summary_is_silent():
    bot = FakeBot()
    await send_daily_summary(cast(Any, bot))
    [msg] = bot.sends(bot.settings.staff_channel_id or 0)
    assert msg.silent is True


async def test_ticket_opener_and_closing_as_embeds():
    use_copy(
        parse_copy(
            """
[embeds.ticket_opener]
enabled = true
footer = "T-{number} · Cloudsail Support"
color = "#123456"

[embeds.ticket_closed]
enabled = true
title = "Ticket closed"
"""
        )
    )
    bot = tickets_bot()
    ticket, _thread, user, _ = await create(bot)
    first = bot.message(ticket.thread_id, ticket.opener_message_id)
    # Pings the player via content (mentions in embeds don't ping); no link → no unfurl
    assert first.content == f"<@{user.id}>"
    assert [u.id for u in first.allowed_mentions.users] == [user.id]
    assert first.embed.title == "Ticket T-26"
    assert first.embed.description.startswith(
        C.tickets.welcome.format(user=f"<@{user.id}>")
    )
    assert "## Ticket T-26" not in first.embed.description  # header is the title now
    assert first.embed.footer.text == "T-26 · Cloudsail Support"
    assert colour(first.embed) == 0x123456
    assert [c.custom_id for c in first.view.children][-1] == f"cs:tclose:{ticket.id}"

    await pick(bot, ticket, user, "store")
    ticket = await tickets.require_ticket(ticket.id)
    repost = bot.message(ticket.thread_id, ticket.opener_message_id)
    assert repost.content is None  # reposts don't ping
    assert "You have selected: **🛒 Store**" in repost.embed.description

    await CloseTicketButton(ticket.id).callback(make_interaction(bot, user))
    closing, csat = bot.sends(ticket.thread_id)[-2:]
    assert closing.content is None
    assert closing.embed.title == "Ticket closed"
    assert closing.embed.description == C.tickets.closed.format(user=user.mention)
    assert colour(closing.embed) == 0x2ECC71
    assert csat.content == C.tickets.csat_prompt  # CSAT prompt stays text


async def test_ticket_opener_defaults_to_text():
    bot = tickets_bot()
    ticket, *_ = await create(bot)
    first = bot.message(ticket.thread_id, ticket.opener_message_id)
    assert (
        first.content.startswith("## Ticket T-26")
        and getattr(first, "embed", None) is None
    )
