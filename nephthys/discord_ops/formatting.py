"""Formatting shared by /stats, /waiting and the daily summary (all via copy)."""

import discord

from nephthys.core.copytext import get_copy
from nephthys.core.stats import LeaderboardEntry
from nephthys.core.stats import UNCATEGORISED
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


def short_title(ticket: Ticket, limit: int) -> str:
    title = ticket.title or ticket.description or get_copy().card.untitled
    title = discord.utils.escape_markdown(title.replace("\n", " "))
    return title if len(title) <= limit else title[: limit - 1] + "…"


def ticket_line(template: str, ticket: Ticket, guild_id: int, title_limit: int) -> str:
    return template.format(
        id=ticket.id,
        title=short_title(ticket, title_limit),
        link=thread_link(guild_id, ticket.thread_id),
        last_message=discord.utils.format_dt(ticket.last_msg_at, "R"),
    )


def categories_text(by_category: dict[str, int]) -> str:
    label = get_copy().stats.uncategorised
    return ", ".join(
        f"{label if name == UNCATEGORISED else name} {count}"
        for name, count in by_category.items()
    )[:FIELD_MAX]


def leaderboard_text(entries: list[LeaderboardEntry]) -> str:
    stats = get_copy().stats
    lines = [
        stats.leaderboard_line.format(
            rank=i, user=f"<@{e.user.discord_id}>", count=e.count
        )
        for i, e in enumerate(entries, start=1)
    ]
    return ("\n".join(lines) or stats.leaderboard_empty)[:FIELD_MAX]
