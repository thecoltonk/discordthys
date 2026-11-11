"""Staff slash commands (replacing Nephthys's App Home)."""

import logging
from datetime import datetime
from datetime import timedelta
from datetime import UTC

import discord
from discord import app_commands
from discord.ext import commands

from nephthys.bot import SupportBot
from nephthys.core.queries import get_unanswered_tickets
from nephthys.core.stats import calculate_stats
from nephthys.core.stats import StatsSummary
from nephthys.core.stats import WAITING_THRESHOLD
from nephthys.database.tables import Ticket
from nephthys.discord_ops.threads import actor_from_user
from nephthys.discord_ops.threads import thread_link

log = logging.getLogger(__name__)

WAITING_LIMIT = 15
TITLE_MAX = 60
STAFF_ONLY = "this command is for staff only."


def short_title(ticket: Ticket) -> str:
    title = ticket.title or ticket.description or f"Ticket #{ticket.id}"
    title = discord.utils.escape_markdown(title.replace("\n", " "))
    return title if len(title) <= TITLE_MAX else title[: TITLE_MAX - 1] + "…"


def format_minutes(minutes: float | None) -> str:
    if minutes is None:
        return "n/a"
    if minutes < 60:
        return f"{minutes:.0f} min"
    if minutes < 60 * 48:
        return f"{minutes / 60:.1f} h"
    return f"{minutes / 1440:.1f} days"


def waiting_lines(guild_id: int, waiting: list[Ticket]) -> list[str]:
    return [
        f"• [#{t.id} {short_title(t)}]({thread_link(guild_id, t.thread_id)}) — "
        f"last message {discord.utils.format_dt(t.last_msg_at, 'R')}"
        for t in waiting
    ]


def stats_embed(summary: StatsSummary, days: int) -> discord.Embed:
    embed = discord.Embed(
        title=f"#community-help — last {days} day{'s' if days != 1 else ''}",
        colour=discord.Colour.blurple(),
    )
    embed.add_field(name="Opened", value=str(summary.opened))
    embed.add_field(
        name="Resolved",
        value=f"{summary.resolved} ({summary.resolved_without_staff} without staff)",
    )
    embed.add_field(
        name="Median first staff response",
        value=format_minutes(summary.median_first_response_minutes),
    )
    embed.add_field(name="Open (unclaimed) now", value=str(summary.open_now))
    embed.add_field(name="In progress now", value=str(summary.in_progress_now))
    embed.add_field(
        name=f"Waiting > {WAITING_THRESHOLD.days} days",
        value=str(summary.waiting_over_threshold),
    )
    leaderboard = (
        "\n".join(
            f"{i}. <@{e.user.discord_id}> — {e.count}"
            for i, e in enumerate(summary.top_claimers, start=1)
        )
        or "nobody yet"
    )
    embed.add_field(
        name="Top helpers (resolved, by claimer)", value=leaderboard, inline=False
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
            await interaction.response.send_message(STAFF_ONLY, ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        waiting = await get_unanswered_tickets(limit=WAITING_LIMIT + 1)
        if not waiting:
            await interaction.followup.send(
                "nothing waiting — nice work, team!", ephemeral=True
            )
            return
        lines = waiting_lines(self.bot.settings.guild_id, waiting[:WAITING_LIMIT])
        if len(waiting) > WAITING_LIMIT:
            lines.append(f"_…and more. Showing the oldest {WAITING_LIMIT}._")
        embed = discord.Embed(
            title="Waiting for staff",
            description="\n".join(lines),
            colour=discord.Colour.orange(),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="stats", description="Support stats for #community-help")
    @app_commands.describe(days="How many days to look back (default 7)")
    @app_commands.guild_only()
    async def stats(
        self,
        interaction: discord.Interaction,
        days: app_commands.Range[int, 1, 365] = 7,
    ) -> None:
        if not self.is_staff(interaction):
            await interaction.response.send_message(STAFF_ONLY, ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        now = datetime.now(UTC)
        summary = await calculate_stats(since=now - timedelta(days=days), until=now)
        await interaction.followup.send(
            embed=stats_embed(summary, days), ephemeral=True
        )


async def setup(bot: SupportBot) -> None:
    await bot.add_cog(Staff(bot))
