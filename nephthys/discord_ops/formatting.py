"""Formatting shared by /stats, /waiting and the daily summary (all via copy)."""

import discord

from nephthys.core.copytext import get_copy
from nephthys.core.stats import LeaderboardEntry
from nephthys.core.stats import UNCATEGORISED
from nephthys.core.stats import UNSET_PRIORITY
from nephthys.core.ticket_categories import get_ticket_categories
from nephthys.database.enums import TicketSurface
from nephthys.database.tables import Ticket
from nephthys.discord_ops.threads import thread_link

FIELD_MAX = 1024


def format_minutes(minutes: float | None) -> str:
    stats = get_copy().stats
    if minutes is None:
        return stats.not_available
    if minutes < 60:
        return stats.minutes.format(n=f"{minutes:.0f}")
    if minutes < 60 * 48:
        return stats.hours.format(n=f"{minutes / 60:.1f}")
    return stats.days.format(n=f"{minutes / 1440:.1f}")


def pct(value: float | None) -> str:
    return get_copy().stats.not_available if value is None else f"{value:.0%}"


def ticket_ref(ticket: Ticket) -> str:
    """#<id> for #community-help, T-<number> for #tickets."""
    if ticket.surface == TicketSurface.PRIVATE and ticket.number is not None:
        return f"T-{ticket.number}"
    return f"#{ticket.id}"


def short_title(ticket: Ticket, limit: int) -> str:
    title = (
        ticket.title
        or ticket.description
        or (
            get_ticket_categories().label_for(ticket.category)
            if ticket.category
            else None
        )
        or get_copy().card.untitled
    )
    title = discord.utils.escape_markdown(title.replace("\n", " "))
    return title if len(title) <= limit else title[: limit - 1] + "…"


def ticket_line(template: str, ticket: Ticket, guild_id: int, title_limit: int) -> str:
    return template.format(
        id=ticket.id,
        ref=ticket_ref(ticket),
        title=short_title(ticket, title_limit),
        link=thread_link(guild_id, ticket.thread_id),
        last_message=discord.utils.format_dt(ticket.last_msg_at, "R"),
    )


def categories_text(
    by_category: dict[str, int], surface: TicketSurface = TicketSurface.COMMUNITY
) -> str:
    """#community-help stores category names; #tickets stores keys (shown as labels)."""
    uncategorised = get_copy().stats.uncategorised
    cats = get_ticket_categories()

    def label(name: str) -> str:
        if name == UNCATEGORISED:
            return uncategorised
        if surface == TicketSurface.PRIVATE:
            return cats.label_for(name) or name
        return name

    return ", ".join(f"{label(name)} {count}" for name, count in by_category.items())[
        :FIELD_MAX
    ]


def priorities_text(by_priority: dict[str, int]) -> str:
    from nephthys.discord_ops.staff_card import priority_label

    unset = get_copy().stats.priority_unset
    return ", ".join(
        f"{unset if p == UNSET_PRIORITY else priority_label(p)} {count}"
        for p, count in by_priority.items()
    )[:FIELD_MAX]


def csat_text(average: float | None, count: int) -> str:
    stats = get_copy().stats
    if average is None:
        return stats.csat_none
    return stats.csat_value.format(average=f"{average:.1f}", count=count)


def surface_name(surface: TicketSurface) -> str:
    stats = get_copy().stats
    return (
        stats.surface_tickets
        if surface == TicketSurface.PRIVATE
        else stats.surface_community
    )


def leaderboard_text(entries: list[LeaderboardEntry]) -> str:
    stats = get_copy().stats
    lines = [
        stats.leaderboard_line.format(
            rank=i, user=f"<@{e.user.discord_id}>", count=e.count
        )
        for i, e in enumerate(entries, start=1)
    ]
    return ("\n".join(lines) or stats.leaderboard_empty)[:FIELD_MAX]
