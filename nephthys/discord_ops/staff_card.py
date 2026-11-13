"""Staff ticket cards in STAFF_CHANNEL_ID, for #tickets only.

One card per private ticket: the question, who asked (and how many tickets
they've opened before), status and claimer, priority, CSAT, and links to the
thread. The card is edited whenever any of that changes.

#community-help tickets no longer get cards (they're counted in /stats,
/waiting and the daily summary instead). Cards posted for them before that
change still exist, so rendering, refreshing and deleting a community card
keeps working for those.
"""

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

import discord

from nephthys.core import ai_store
from nephthys.core import private_tickets
from nephthys.core import tickets
from nephthys.core.copytext import get_copy
from nephthys.core.ticket_categories import get_ticket_categories
from nephthys.core.users import get_user
from nephthys.database.enums import TicketStatus
from nephthys.database.enums import TicketSurface
from nephthys.database.tables import AISuggestion
from nephthys.database.tables import Ticket
from nephthys.database.tables import User
from nephthys.discord_ops.embeds import make_embed
from nephthys.discord_ops.formatting import ticket_ref
from nephthys.discord_ops.send import send_embed
from nephthys.discord_ops.threads import best_effort
from nephthys.discord_ops.threads import thread_link
from nephthys.ui.buttons import link_only_view

if TYPE_CHECKING:
    from nephthys.bot import SupportBot

log = logging.getLogger(__name__)

DESCRIPTION_MAX = 1000


@dataclass(frozen=True)
class CardContext:
    ticket: Ticket
    opener: User | None
    claimer: User | None
    closer: User | None
    past_tickets: int
    suggestion: AISuggestion | None
    escalated_from: Ticket | None = None
    escalated_to: Ticket | None = None


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
        escalated_from=(
            await tickets.get_ticket(ticket.escalated_from)
            if ticket.escalated_from is not None
            else None
        ),
        escalated_to=await private_tickets.escalation_of(ticket.id),
    )


def mention(user: User | None) -> str:
    return f"<@{user.discord_id}>" if user else get_copy().card.someone


def zammad_agent(name: str) -> str:
    return get_copy().card.zammad_agent.format(name=discord.utils.escape_markdown(name))


def zammad_ticket_url(public_url: str | None, zammad_ticket_id: int) -> str | None:
    if not public_url:
        return None
    return f"{public_url}/#ticket/zoom/{zammad_ticket_id}"


def status_line(ctx: CardContext) -> tuple[str, str]:
    """The status text and its hex colour ([embeds.staff_card] color_*)."""
    card = get_copy().card
    style = get_copy().embeds.staff_card
    t = ctx.ticket
    reopened = card.reopened_suffix if t.reopened_at is not None else ""
    if t.status == TicketStatus.CLOSED:
        if ctx.closer is not None:
            by = card.resolved_by.format(user=mention(ctx.closer))
        elif t.closed_by_name:
            by = card.resolved_by.format(user=zammad_agent(t.closed_by_name))
        else:
            by = card.resolved_automatically
        if ctx.claimer:
            credit = card.credit.format(user=mention(ctx.claimer))
        elif t.assigned_name:
            credit = card.credit.format(user=zammad_agent(t.assigned_name))
        else:
            credit = card.no_credit
        return card.status_resolved.format(by=by, credit=credit), style.color_resolved
    if ctx.claimer is not None or t.assigned_name:
        claimer = (
            mention(ctx.claimer)
            if ctx.claimer is not None
            else zammad_agent(t.assigned_name or "")
        )
        return (
            card.status_claimed.format(claimer=claimer, reopened=reopened),
            style.color_claimed,
        )
    return card.status_waiting.format(reopened=reopened), style.color_waiting


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


def priority_label(priority: str | None) -> str:
    copy = get_copy().tickets
    return {
        "low": copy.priority_low,
        "medium": copy.priority_medium,
        "high": copy.priority_high,
    }.get(priority or "", priority or "?")


def private_category_text(ticket: Ticket) -> str:
    cats = get_ticket_categories()
    category = cats.get(ticket.category)
    if not ticket.category:
        return get_copy().card.category_pending
    parts = [category.display if category else ticket.category]
    sub = category.sub(ticket.subcategory) if category else None
    if sub is not None:
        parts.append(sub.display)
    elif ticket.subcategory:
        parts.append(ticket.subcategory)
    return " › ".join(parts)


def zammad_field(ticket: Ticket, public_url: str | None) -> str | None:
    """The Zammad ticket (linked when ZAMMAD_PUBLIC_URL is set) and its group."""
    if ticket.zammad_ticket_id is None:
        return None
    card = get_copy().card
    number = ticket.zammad_ticket_number or str(ticket.zammad_ticket_id)
    url = zammad_ticket_url(public_url, ticket.zammad_ticket_id)
    value = (
        card.zammad_value.format(number=number, link=url)
        if url
        else card.zammad_value_no_link.format(number=number)
    )
    if ticket.zammad_group:
        value += card.zammad_group.format(group=ticket.zammad_group)
    return value


def build_card_embed(
    ctx: CardContext, guild_id: int, zammad_public_url: str | None = None
) -> discord.Embed:
    card = get_copy().card
    style = get_copy().embeds.staff_card
    t = ctx.ticket
    private = t.surface == TicketSurface.PRIVATE
    status, colour = status_line(ctx)
    if private:
        title = style.private_title.format(
            number=t.number, category=private_category_text(t)
        )
        question = (t.description or "").strip() or card.awaiting_first_message
    else:
        title = style.title.format(id=t.id, title=t.title or card.untitled)
        question = (t.description or "").strip() or card.no_text
    if len(question) > DESCRIPTION_MAX:
        question = question[: DESCRIPTION_MAX - 1] + "…"

    embed = make_embed(
        style,
        title=title,
        description=style.description.format(question=question),
        footer=style.footer.format(id=t.id, ref=ticket_ref(t)),
        color=colour,
        url=thread_link(guild_id, t.thread_id),
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
    if private:
        embed.add_field(
            name=card.field_priority, value=priority_label(t.priority), inline=True
        )
        if t.csat_rating is not None:
            embed.add_field(
                name=card.field_csat,
                value=card.csat_value.format(rating=t.csat_rating),
                inline=True,
            )
    else:
        if t.category:
            category = (
                card.category_ai.format(category=t.category)
                if t.category_source == "ai"
                else t.category
            )
        else:
            category = card.category_none
        embed.add_field(name=card.field_category, value=category, inline=True)
    if (zammad := zammad_field(t, zammad_public_url)) is not None:
        embed.add_field(name=card.field_zammad, value=zammad, inline=True)
    if ctx.escalated_from is not None:
        embed.add_field(
            name=card.field_escalated,
            value=card.escalated_value.format(
                id=ctx.escalated_from.id,
                link=thread_link(guild_id, ctx.escalated_from.thread_id),
            ),
            inline=True,
        )
    if ctx.escalated_to is not None:
        embed.add_field(
            name=card.field_escalated_to,
            value=card.escalated_to_value.format(
                number=ctx.escalated_to.number,
                link=thread_link(guild_id, ctx.escalated_to.thread_id),
            ),
            inline=True,
        )
    if (line := ai_line(ctx.suggestion)) is not None:
        embed.add_field(name=card.field_ai, value=line, inline=False)
    return embed


def build_card_view(ticket: Ticket, guild_id: int):
    return link_only_view(thread_link(guild_id, ticket.thread_id))


async def render(bot: "SupportBot", ticket: Ticket):
    ctx = await load_card_context(ticket)
    embed = build_card_embed(ctx, bot.settings.guild_id, bot.settings.zammad_public_url)
    view = build_card_view(ticket, bot.settings.guild_id)
    return embed, view


async def post_staff_card(
    bot: "SupportBot",
    ticket: Ticket,
    *,
    ping_content: str | None = None,
    ping_roles: list[int] | None = None,
) -> None:
    channel_id = bot.settings.staff_channel_id
    if not channel_id or ticket.surface != TicketSurface.PRIVATE:
        return
    embed, view = await render(bot, ticket)
    async with best_effort("staff_card_post"):
        msg = await send_embed(
            bot.get_partial_messageable(channel_id),
            embed,
            view=view,
            ping_content=ping_content,
            ping_roles=ping_roles,
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
