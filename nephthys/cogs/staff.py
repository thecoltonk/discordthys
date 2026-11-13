"""Staff slash commands (replacing Nephthys's App Home)."""

import logging
from datetime import datetime
from datetime import timedelta
from datetime import UTC

import discord
from discord import app_commands
from discord.ext import commands

from nephthys.bot import SupportBot
from nephthys.core.copytext import get_copy
from nephthys.core.queries import get_unanswered_tickets
from nephthys.core.stats import calculate_stats
from nephthys.core.stats import StatsSummary
from nephthys.core.stats import WAITING_THRESHOLD
from nephthys.database.enums import TicketSurface
from nephthys.database.tables import Ticket
from nephthys.discord_ops.embeds import make_embed
from nephthys.discord_ops.formatting import categories_text
from nephthys.discord_ops.formatting import csat_text
from nephthys.discord_ops.formatting import format_minutes
from nephthys.discord_ops.formatting import leaderboard_text
from nephthys.discord_ops.formatting import priorities_text
from nephthys.discord_ops.formatting import surface_name
from nephthys.discord_ops.formatting import ticket_line
from nephthys.discord_ops.send import followup_embed
from nephthys.discord_ops.send import followup_embeds
from nephthys.discord_ops.send import followup_text
from nephthys.discord_ops.send import respond_text
from nephthys.discord_ops.threads import actor_from_user
from nephthys.zammad.outbox import OutboxStats
from nephthys.zammad.outbox import stats as outbox_stats

log = logging.getLogger(__name__)

WAITING_LIMIT = 15
TITLE_MAX = 60


def waiting_lines(guild_id: int, waiting: list[Ticket]) -> list[str]:
    template = get_copy().commands.waiting_line
    return [ticket_line(template, t, guild_id, TITLE_MAX) for t in waiting]


def zammad_sync_text(sync: OutboxStats) -> str:
    stats = get_copy().stats
    if sync.healthy:
        return stats.zammad_ok
    return stats.zammad_backlog.format(
        pending=sync.pending,
        failed=sync.failed,
        oldest=format_minutes(
            sync.oldest_pending_seconds / 60
            if sync.oldest_pending_seconds is not None
            else None
        ),
    )


def stats_embed(
    summary: StatsSummary,
    days: int,
    surface: TicketSurface = TicketSurface.COMMUNITY,
    zammad: OutboxStats | None = None,
) -> discord.Embed:
    stats = get_copy().stats
    style = get_copy().embeds.stats
    name = surface_name(surface)
    title = (
        style.title_one_day.format(surface=name)
        if days == 1
        else style.title.format(surface=name, days=days)
    )
    embed = make_embed(
        style,
        title=title,
        description=style.description.format(surface=name, days=days),
    )
    embed.add_field(name=stats.field_opened, value=str(summary.opened))
    embed.add_field(name=stats.field_claimed, value=str(summary.claimed))
    embed.add_field(
        name=stats.field_resolved,
        value=stats.resolved_value.format(
            resolved=summary.resolved, without_staff=summary.resolved_without_staff
        ),
    )
    embed.add_field(
        name=stats.field_median,
        value=format_minutes(summary.median_first_response_minutes),
    )
    embed.add_field(name=stats.field_open_now, value=str(summary.open_now))
    embed.add_field(name=stats.field_in_progress, value=str(summary.in_progress_now))
    embed.add_field(
        name=stats.field_waiting.format(days=WAITING_THRESHOLD.days),
        value=str(summary.waiting_over_threshold),
    )
    if surface == TicketSurface.PRIVATE:
        embed.add_field(
            name=stats.field_csat,
            value=csat_text(summary.csat_average, summary.csat_count),
        )
        if summary.by_priority:
            embed.add_field(
                name=stats.field_by_priority,
                value=priorities_text(summary.by_priority),
                inline=False,
            )
    if summary.by_category:
        embed.add_field(
            name=stats.field_by_category,
            value=categories_text(summary.by_category, surface),
            inline=False,
        )
    embed.add_field(
        name=stats.field_top_helpers,
        value=leaderboard_text(summary.top_claimers),
        inline=False,
    )
    if zammad is not None:
        embed.add_field(
            name=stats.field_zammad, value=zammad_sync_text(zammad), inline=False
        )
    return embed


class Staff(commands.Cog):
    def __init__(self, bot: SupportBot):
        self.bot = bot

    def is_staff(self, interaction: discord.Interaction) -> bool:
        return actor_from_user(
            interaction.user, self.bot.settings.staff_role_id
        ).is_staff

    @app_commands.command(
        name="waiting", description="Tickets waiting for a staff reply, oldest first"
    )
    @app_commands.guild_only()
    async def waiting(self, interaction: discord.Interaction) -> None:
        if not self.is_staff(interaction):
            await respond_text(
                interaction, get_copy().errors.staff_only, ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        waiting = await get_unanswered_tickets(limit=WAITING_LIMIT + 1)
        if not waiting:
            await followup_text(interaction, get_copy().commands.waiting_empty)
            return
        lines = waiting_lines(self.bot.settings.guild_id, waiting[:WAITING_LIMIT])
        if len(waiting) > WAITING_LIMIT:
            lines.append(get_copy().commands.waiting_more.format(limit=WAITING_LIMIT))
        style = get_copy().embeds.waiting
        embed = make_embed(
            style,
            title=style.title,
            description=style.description.format(lines="\n".join(lines)),
        )
        await followup_embed(interaction, embed)

    @app_commands.command(name="stats", description="Support stats for #community-help")
    @app_commands.describe(days="How many days to look back (default 7)")
    @app_commands.guild_only()
    async def stats(
        self,
        interaction: discord.Interaction,
        days: app_commands.Range[int, 1, 365] = 7,
    ) -> None:
        if not self.is_staff(interaction):
            await respond_text(
                interaction, get_copy().errors.staff_only, ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        now = datetime.now(UTC)
        since = now - timedelta(days=days)
        embeds = [stats_embed(await calculate_stats(since=since, until=now), days)]
        if self.bot.settings.tickets_enabled:
            private = await calculate_stats(
                since=since, until=now, surface=TicketSurface.PRIVATE
            )
            sync = await outbox_stats() if self.bot.settings.zammad_enabled else None
            embeds.append(stats_embed(private, days, TicketSurface.PRIVATE, sync))
        await followup_embeds(interaction, embeds)


async def setup(bot: SupportBot) -> None:
    await bot.add_cog(Staff(bot))
