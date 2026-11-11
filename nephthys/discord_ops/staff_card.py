"""Nephthys-style staff ticket cards in STAFF_CHANNEL_ID.

One card per ticket: the question, who asked (and how many tickets they've
opened before), status and claimer, category (with a select menu), the latest
AI suggestion, and a link to the thread. The card is edited whenever any of
that changes.
"""

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

import discord

from nephthys.core import ai_store
from nephthys.core import tickets
from nephthys.core.copytext import get_copy
from nephthys.core.users import get_user
from nephthys.database.enums import TicketStatus
from nephthys.database.tables import AISuggestion
from nephthys.database.tables import Ticket
from nephthys.database.tables import User
from nephthys.discord_ops.send import send_embed
from nephthys.discord_ops.threads import best_effort
from nephthys.discord_ops.threads import thread_link
from nephthys.ui.buttons import staff_card_view

if TYPE_CHECKING:
    from nephthys.bot import SupportBot

log = logging.getLogger(__name__)

DESCRIPTION_MAX = 1000

COLOUR_WAITING = discord.Colour.gold()
COLOUR_CLAIMED = discord.Colour.blurple()
COLOUR_RESOLVED = discord.Colour.green()


@dataclass(frozen=True)
class CardContext:
    ticket: Ticket
    opener: User | None
    claimer: User | None
    closer: User | None
    past_tickets: int
    suggestion: AISuggestion | None


async def load_card_context(ticket: Ticket) -> CardContext:
    async def user(value: object) -> User | None:
        user_id = tickets.fk_id_or_none(value)
        return await get_user(user_id) if user_id is not None else None

    return CardContext(
        ticket=ticket,
        opener=await user(ticket.opened_by),
        claimer=await user(ticket.assigned_to),
        closer=await user(ticket.closed_by),
        past_tickets=await tickets.count_past_tickets(ticket),
        suggestion=await ai_store.latest_posted_for_ticket(ticket.id),
    )


def mention(user: User | None) -> str:
    return f"<@{user.discord_id}>" if user else get_copy().card.someone


def status_line(ctx: CardContext) -> tuple[str, discord.Colour]:
    card = get_copy().card
    t = ctx.ticket
    reopened = card.reopened_suffix if t.reopened_at is not None else ""
    if t.status == TicketStatus.CLOSED:
        by = (
            card.resolved_automatically
            if ctx.closer is None
            else card.resolved_by.format(user=mention(ctx.closer))
        )
        credit = (
            card.credit.format(user=mention(ctx.claimer))
            if ctx.claimer
            else card.no_credit
        )
        return card.status_resolved.format(by=by, credit=credit), COLOUR_RESOLVED
    if ctx.claimer is not None:
        return (
            card.status_claimed.format(claimer=mention(ctx.claimer), reopened=reopened),
            COLOUR_CLAIMED,
        )
    return card.status_waiting.format(reopened=reopened), COLOUR_WAITING


def past_tickets_text(count: int) -> str:
    card = get_copy().card
    if count == 0:
        return card.first_ticket
    template = card.past_tickets_one if count == 1 else card.past_tickets_many
    return template.format(count=count)


def ai_outcome_label(outcome: str) -> str:
    card = get_copy().card
    return {
        ai_store.PENDING: card.ai_pending,
        ai_store.HELPFUL: card.ai_helpful,
        ai_store.NOT_HELPFUL: card.ai_not_helpful,
        ai_store.FIXED: card.ai_fixed,
        ai_store.NEEDS_HUMAN: card.ai_needs_human,
    }.get(outcome, outcome)


def ai_line(suggestion: AISuggestion | None) -> str | None:
    if suggestion is None:
        return None
    confidence = (
        f"{suggestion.confidence:.0%}" if suggestion.confidence is not None else "?"
    )
    return get_copy().card.ai_line.format(
        mode=suggestion.mode,
        confidence=confidence,
        outcome=ai_outcome_label(suggestion.outcome),
    )


def build_card_embed(ctx: CardContext, guild_id: int) -> discord.Embed:
    card = get_copy().card
    t = ctx.ticket
    status, colour = status_line(ctx)
    title = card.title.format(id=t.id, title=t.title or card.untitled)
    description = (t.description or "").strip() or card.no_text
    if len(description) > DESCRIPTION_MAX:
        description = description[: DESCRIPTION_MAX - 1] + "…"

    embed = discord.Embed(
        title=title[:256],
        url=thread_link(guild_id, t.thread_id),
        description=description,
        colour=colour,
        timestamp=t.created_at,
    )
    embed.add_field(
        name=card.field_asked_by,
        value=card.asked_by.format(
            user=mention(ctx.opener), past_tickets=past_tickets_text(ctx.past_tickets)
        ),
        inline=True,
    )
    embed.add_field(name=card.field_status, value=status, inline=True)
    if t.category:
        category = (
            card.category_ai.format(category=t.category)
            if t.category_source == "ai"
            else t.category
        )
    else:
        category = card.category_none
    embed.add_field(name=card.field_category, value=category, inline=True)
    if (line := ai_line(ctx.suggestion)) is not None:
        embed.add_field(name=card.field_ai, value=line, inline=False)
    embed.set_footer(text=card.footer.format(id=t.id))
    return embed


def build_card_view(ticket: Ticket, categories: tuple[str, ...], guild_id: int):
    return staff_card_view(
        ticket.id, categories, ticket.category, thread_link(guild_id, ticket.thread_id)
    )


async def render(bot: "SupportBot", ticket: Ticket):
    ctx = await load_card_context(ticket)
    embed = build_card_embed(ctx, bot.settings.guild_id)
    view = build_card_view(
        ticket, bot.settings.ticket_categories, bot.settings.guild_id
    )
    return embed, view


async def post_staff_card(bot: "SupportBot", ticket: Ticket) -> None:
    channel_id = bot.settings.staff_channel_id
    if not channel_id:
        return
    embed, view = await render(bot, ticket)
    async with best_effort("staff_card_post"):
        msg = await send_embed(
            bot.get_partial_messageable(channel_id), embed, view=view
        )
        await tickets.set_staff_card(ticket.id, channel_id, msg.id)


async def refresh_staff_card(bot: "SupportBot", ticket_id: int) -> None:
    ticket = await tickets.get_ticket(ticket_id)
    if (
        not ticket
        or not ticket.staff_card_channel_id
        or not ticket.staff_card_message_id
    ):
        return
    embed, view = await render(bot, ticket)
    async with best_effort("staff_card_edit"):
        await (
            bot.get_partial_messageable(ticket.staff_card_channel_id)
            .get_partial_message(ticket.staff_card_message_id)
            .edit(embed=embed, view=view)
        )


async def delete_staff_card(bot: "SupportBot", ticket: Ticket) -> None:
    if not ticket.staff_card_channel_id or not ticket.staff_card_message_id:
        return
    async with best_effort("staff_card_delete"):
        await (
            bot.get_partial_messageable(ticket.staff_card_channel_id)
            .get_partial_message(ticket.staff_card_message_id)
            .delete()
        )
