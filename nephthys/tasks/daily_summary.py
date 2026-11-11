"""Daily summary post in STAFF_CHANNEL_ID (default 09:00 America/Phoenix)."""

import logging
from datetime import datetime
from datetime import timedelta
from datetime import UTC
from typing import TYPE_CHECKING

import discord

from nephthys.core import ai_store
from nephthys.core.ai_store import AIAccuracy
from nephthys.core.stats import calculate_stats
from nephthys.core.stats import StatsSummary
from nephthys.core.stats import WAITING_THRESHOLD
from nephthys.database.tables import Ticket
from nephthys.discord_ops.threads import thread_link

if TYPE_CHECKING:
    from nephthys.bot import SupportBot

log = logging.getLogger(__name__)

WINDOW = timedelta(days=1)
AI_TREND_WINDOW = timedelta(days=7)
MAX_WAITING_LISTED = 10
FIELD_MAX = 1024


def format_minutes(minutes: float | None) -> str:
    if minutes is None:
        return "n/a"
    if minutes < 60:
        return f"{minutes:.0f} min"
    if minutes < 60 * 48:
        return f"{minutes / 60:.1f} h"
    return f"{minutes / 1440:.1f} days"


def pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def waiting_text(tickets: list[Ticket], guild_id: int) -> str:
    if not tickets:
        return "none 🎉"
    lines = []
    for t in tickets[:MAX_WAITING_LISTED]:
        title = discord.utils.escape_markdown((t.title or f"Ticket #{t.id}")[:50])
        lines.append(
            f"• [#{t.id} {title}]({thread_link(guild_id, t.thread_id)}) — "
            f"{discord.utils.format_dt(t.last_msg_at, 'R')}"
        )
    if len(tickets) > MAX_WAITING_LISTED:
        lines.append(f"…and {len(tickets) - MAX_WAITING_LISTED} more (see /waiting)")
    text = "\n".join(lines)
    # Embed field values are capped at 1024 characters
    while len(text) > FIELD_MAX and len(lines) > 1:
        lines.pop(-2 if lines[-1].startswith("…") else -1)
        text = "\n".join(lines)
    return text


def ai_text(day: AIAccuracy, week: AIAccuracy, mode: str) -> str:
    lines = []
    if mode == ai_store.SHADOW or day.shadow_posted or week.shadow_posted:
        lines.append(
            f"**Shadow accuracy** (👍 / rated): 24h {pct(day.shadow_accuracy)} "
            f"({day.helpful}/{day.helpful + day.not_helpful}), "
            f"7d {pct(week.shadow_accuracy)} ({week.helpful}/{week.helpful + week.not_helpful})"
        )
        lines.append(
            f"Suggestions 24h: {day.shadow_posted} posted, {day.shadow_unrated} unrated"
        )
    if mode == ai_store.PUBLIC or day.public_posted or week.public_posted:
        lines.append(
            f"**Public answers** 24h: {day.public_posted} posted — {day.fixed} fixed, "
            f"{day.needs_human} needed a human, {day.public_no_response} no response; "
            f"7d fix rate {pct(week.public_fix_rate)}"
        )
    skipped = sum(day.skipped.values())
    if day.attempts:
        reasons = ", ".join(f"{k} {v}" for k, v in sorted(day.skipped.items()))
        lines.append(
            f"Attempts 24h: {day.attempts} ({skipped} not posted{': ' + reasons if reasons else ''})"
        )
    return "\n".join(lines) or "no AI activity"


def build_summary_embed(
    stats: StatsSummary,
    *,
    guild_id: int,
    ai_day: AIAccuracy | None,
    ai_week: AIAccuracy | None,
    ai_mode: str,
) -> discord.Embed:
    embed = discord.Embed(
        title="📊 #community-help daily summary",
        description=(
            f"{discord.utils.format_dt(stats.since, 'f')} → "
            f"{discord.utils.format_dt(stats.until, 'f')}"
        ),
        colour=discord.Colour.blurple(),
    )
    embed.add_field(name="Opened", value=str(stats.opened))
    embed.add_field(
        name="Resolved",
        value=f"{stats.resolved} ({stats.resolved_without_staff} without staff)",
    )
    embed.add_field(
        name="Median first staff response",
        value=format_minutes(stats.median_first_response_minutes),
    )
    if stats.by_category:
        embed.add_field(
            name="By category",
            value=", ".join(f"{k} {v}" for k, v in stats.by_category.items())[
                :FIELD_MAX
            ],
            inline=False,
        )
    embed.add_field(
        name=f"Waiting > {WAITING_THRESHOLD.days} days ({stats.waiting_over_threshold})",
        value=waiting_text(stats.waiting_tickets, guild_id),
        inline=False,
    )
    leaderboard = (
        "\n".join(
            f"{i}. <@{e.user.discord_id}> — {e.count}"
            for i, e in enumerate(stats.top_claimers, start=1)
        )
        or "nobody yet"
    )
    embed.add_field(
        name="Top helpers (resolved, by claimer)", value=leaderboard, inline=False
    )
    if ai_day is not None and ai_week is not None:
        embed.add_field(
            name="🤖 AI",
            value=ai_text(ai_day, ai_week, ai_mode)[:FIELD_MAX],
            inline=False,
        )
    return embed


async def build_daily_summary(
    *, now: datetime, guild_id: int, ai_enabled: bool, ai_mode: str
) -> discord.Embed:
    stats = await calculate_stats(since=now - WINDOW, until=now)
    ai_day = ai_week = None
    if ai_enabled:
        ai_day = await ai_store.accuracy(now - WINDOW, now)
        ai_week = await ai_store.accuracy(now - AI_TREND_WINDOW, now)
    return build_summary_embed(
        stats, guild_id=guild_id, ai_day=ai_day, ai_week=ai_week, ai_mode=ai_mode
    )


async def send_daily_summary(bot: "SupportBot", now: datetime | None = None) -> None:
    channel_id = bot.settings.staff_channel_id
    if not channel_id:
        return
    embed = await build_daily_summary(
        now=now or datetime.now(UTC),
        guild_id=bot.settings.guild_id,
        ai_enabled=bot.settings.ai_answers_enabled,
        ai_mode=bot.settings.ai_answer_mode,
    )
    await bot.get_partial_messageable(channel_id).send(
        embed=embed, allowed_mentions=discord.AllowedMentions.none()
    )
    log.info("Posted daily summary")
