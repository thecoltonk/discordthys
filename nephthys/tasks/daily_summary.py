"""Daily summary post in STAFF_CHANNEL_ID (default 09:00 America/Phoenix)."""

import logging
from datetime import datetime
from datetime import timedelta
from datetime import UTC
from typing import TYPE_CHECKING

import discord

from nephthys.core import ai_store
from nephthys.core.ai_store import AIAccuracy
from nephthys.core.copytext import get_copy
from nephthys.core.stats import calculate_stats
from nephthys.core.stats import StatsSummary
from nephthys.core.stats import WAITING_THRESHOLD
from nephthys.database.enums import TicketSurface
from nephthys.database.tables import Ticket
from nephthys.discord_ops.embeds import make_embed
from nephthys.discord_ops.formatting import categories_text
from nephthys.discord_ops.formatting import csat_text
from nephthys.discord_ops.formatting import FIELD_MAX
from nephthys.discord_ops.formatting import format_minutes
from nephthys.discord_ops.formatting import leaderboard_text
from nephthys.discord_ops.formatting import pct
from nephthys.discord_ops.formatting import priorities_text
from nephthys.discord_ops.formatting import surface_name
from nephthys.discord_ops.formatting import ticket_line
from nephthys.discord_ops.send import send_embeds

if TYPE_CHECKING:
    from nephthys.bot import SupportBot

log = logging.getLogger(__name__)

WINDOW = timedelta(days=1)
AI_TREND_WINDOW = timedelta(days=7)
MAX_WAITING_LISTED = 10
TITLE_MAX = 50


def waiting_text(tickets: list[Ticket], guild_id: int) -> str:
    summary = get_copy().summary
    if not tickets:
        return summary.waiting_none
    lines = [
        ticket_line(summary.waiting_line, t, guild_id, TITLE_MAX)
        for t in tickets[:MAX_WAITING_LISTED]
    ]
    more = (
        summary.waiting_more.format(count=len(tickets) - MAX_WAITING_LISTED)
        if len(tickets) > MAX_WAITING_LISTED
        else None
    )
    # Embed field values are capped at 1024 characters
    while lines and len("\n".join([*lines, *([more] if more else [])])) > FIELD_MAX:
        lines.pop()
    return "\n".join([*lines, *([more] if more else [])])[:FIELD_MAX]


def ai_text(day: AIAccuracy, week: AIAccuracy, mode: str) -> str:
    summary = get_copy().summary
    lines = []
    if mode == ai_store.SHADOW or day.shadow_posted or week.shadow_posted:
        lines.append(
            summary.ai_shadow_accuracy.format(
                day_pct=pct(day.shadow_accuracy),
                day_helpful=day.helpful,
                day_rated=day.helpful + day.not_helpful,
                week_pct=pct(week.shadow_accuracy),
                week_helpful=week.helpful,
                week_rated=week.helpful + week.not_helpful,
            )
        )
        lines.append(
            summary.ai_shadow_counts.format(
                posted=day.shadow_posted, unrated=day.shadow_unrated
            )
        )
    if mode == ai_store.PUBLIC or day.public_posted or week.public_posted:
        lines.append(
            summary.ai_public.format(
                posted=day.public_posted,
                fixed=day.fixed,
                needs_human=day.needs_human,
                no_response=day.public_no_response,
                week_fix_rate=pct(week.public_fix_rate),
            )
        )
    if day.attempts:
        skipped = sum(day.skipped.values())
        reasons = ", ".join(f"{k} {v}" for k, v in sorted(day.skipped.items()))
        if reasons:
            lines.append(
                summary.ai_attempts_reasons.format(
                    attempts=day.attempts, skipped=skipped, reasons=reasons
                )
            )
        else:
            lines.append(
                summary.ai_attempts.format(attempts=day.attempts, skipped=skipped)
            )
    return "\n".join(lines) or summary.ai_none


def build_summary_embed(
    stats: StatsSummary,
    *,
    guild_id: int,
    ai_day: AIAccuracy | None,
    ai_week: AIAccuracy | None,
    ai_mode: str,
    surface: TicketSurface = TicketSurface.COMMUNITY,
) -> discord.Embed:
    copy = get_copy()
    style = copy.embeds.daily_summary
    embed = make_embed(
        style,
        title=style.title.format(surface=surface_name(surface)),
        description=style.description.format(
            since=discord.utils.format_dt(stats.since, "f"),
            until=discord.utils.format_dt(stats.until, "f"),
        ),
    )
    embed.add_field(name=copy.stats.field_opened, value=str(stats.opened))
    embed.add_field(
        name=copy.stats.field_resolved,
        value=copy.stats.resolved_value.format(
            resolved=stats.resolved, without_staff=stats.resolved_without_staff
        ),
    )
    embed.add_field(
        name=copy.stats.field_median,
        value=format_minutes(stats.median_first_response_minutes),
    )
    if surface == TicketSurface.PRIVATE:
        embed.add_field(
            name=copy.stats.field_csat,
            value=csat_text(stats.csat_average, stats.csat_count),
        )
        if stats.by_priority:
            embed.add_field(
                name=copy.stats.field_by_priority,
                value=priorities_text(stats.by_priority),
                inline=False,
            )
    if stats.by_category:
        embed.add_field(
            name=copy.summary.field_by_category,
            value=categories_text(stats.by_category, surface),
            inline=False,
        )
    embed.add_field(
        name=copy.summary.field_waiting.format(
            days=WAITING_THRESHOLD.days, count=stats.waiting_over_threshold
        ),
        value=waiting_text(stats.waiting_tickets, guild_id),
        inline=False,
    )
    embed.add_field(
        name=copy.stats.field_top_helpers,
        value=leaderboard_text(stats.top_claimers),
        inline=False,
    )
    if ai_day is not None and ai_week is not None:
        embed.add_field(
            name=copy.summary.field_ai,
            value=ai_text(ai_day, ai_week, ai_mode)[:FIELD_MAX],
            inline=False,
        )
    return embed


async def build_daily_summary(
    *,
    now: datetime,
    guild_id: int,
    ai_enabled: bool,
    ai_mode: str,
    tickets_enabled: bool = False,
) -> list[discord.Embed]:
    """One embed for #community-help, plus one for #tickets when it's enabled."""
    stats = await calculate_stats(since=now - WINDOW, until=now)
    ai_day = ai_week = None
    if ai_enabled:
        ai_day = await ai_store.accuracy(now - WINDOW, now)
        ai_week = await ai_store.accuracy(now - AI_TREND_WINDOW, now)
    embeds = [
        build_summary_embed(
            stats, guild_id=guild_id, ai_day=ai_day, ai_week=ai_week, ai_mode=ai_mode
        )
    ]
    if tickets_enabled:
        private = await calculate_stats(
            since=now - WINDOW, until=now, surface=TicketSurface.PRIVATE
        )
        embeds.append(
            build_summary_embed(
                private,
                guild_id=guild_id,
                ai_day=None,
                ai_week=None,
                ai_mode=ai_mode,
                surface=TicketSurface.PRIVATE,
            )
        )
    return embeds


async def send_daily_summary(bot: "SupportBot", now: datetime | None = None) -> None:
    channel_id = bot.settings.staff_channel_id
    if not channel_id:
        return
    embeds = await build_daily_summary(
        now=now or datetime.now(UTC),
        guild_id=bot.settings.guild_id,
        ai_enabled=bot.settings.ai_answers_enabled,
        ai_mode=bot.settings.ai_answer_mode,
        tickets_enabled=bot.settings.tickets_enabled,
    )
    # @silent: the summary is for reading, not a reason to ping everyone's phone
    await send_embeds(bot.get_partial_messageable(channel_id), embeds, silent=True)
    log.info("Posted daily summary")
