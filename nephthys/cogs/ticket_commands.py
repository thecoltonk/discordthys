"""Staff slash commands used inside ticket threads (#community-help and #tickets).

/faq and /ip post on staff's behalf but do NOT count as a staff reply: they
don't claim the ticket and don't clear "waiting". /resolve and /reopen run the
same flows as the buttons.
"""

import logging
from dataclasses import dataclass

import discord
from discord import app_commands
from discord.ext import commands

from nephthys.bot import SupportBot
from nephthys.core import tickets
from nephthys.core.copytext import get_copy
from nephthys.core.permissions import Actor
from nephthys.database.enums import BotMessageKind
from nephthys.database.tables import Ticket
from nephthys.discord_ops import flows
from nephthys.discord_ops.send import followup_text
from nephthys.discord_ops.send import respond_text
from nephthys.discord_ops.threads import actor_from_user
from nephthys.errors.errors import PermissionDenied
from nephthys.errors.errors import TicketAlreadyClosedError
from nephthys.errors.errors import TicketNotClosedError
from nephthys.errors.errors import TicketNotFoundError

log = logging.getLogger(__name__)

REFUSALS = (
    PermissionDenied,
    TicketAlreadyClosedError,
    TicketNotClosedError,
    TicketNotFoundError,
)


@dataclass(frozen=True)
class TicketContext:
    ticket: Ticket
    actor: Actor


class TicketCommands(commands.Cog):
    def __init__(self, bot: SupportBot):
        self.bot = bot

    async def ticket_context(
        self, interaction: discord.Interaction
    ) -> TicketContext | None:
        """Staff + inside a ticket thread, or reply with why not (ephemeral)."""
        settings = self.bot.settings
        actor = actor_from_user(interaction.user, settings.staff_role_id)
        if not actor.is_staff:
            await respond_text(
                interaction, get_copy().errors.staff_only, ephemeral=True
            )
            return None
        channel = interaction.channel
        ticket = None
        ticket_parents = {settings.community_channel_id, settings.tickets_channel_id}
        if isinstance(channel, discord.Thread) and channel.parent_id in ticket_parents:
            ticket = await tickets.get_ticket_by_thread(channel.id)
        if ticket is None:
            await respond_text(
                interaction, get_copy().errors.not_a_ticket_thread, ephemeral=True
            )
            return None
        return TicketContext(ticket=ticket, actor=actor)

    async def post_reply(
        self, interaction: discord.Interaction, ticket: Ticket, text: str
    ) -> None:
        """Post publicly as the command response ("<staff> used /faq"). This
        deliberately doesn't call `record_message`, so it never claims."""
        await respond_text(interaction, text, ephemeral=False)
        try:
            msg = await interaction.original_response()
        except discord.HTTPException:
            return
        await tickets.record_bot_message(
            ticket.id, ticket.thread_id, msg.id, BotMessageKind.MACRO
        )

    @app_commands.command(name="faq", description="Staff: link the FAQ in this ticket")
    @app_commands.guild_only()
    async def faq(self, interaction: discord.Interaction) -> None:
        ctx = await self.ticket_context(interaction)
        if ctx is None:
            return
        url = self.bot.settings.faq_url
        if not url:
            await respond_text(
                interaction, get_copy().commands.faq_unconfigured, ephemeral=True
            )
            return
        await self.post_reply(
            interaction, ctx.ticket, get_copy().commands.faq_reply.format(faq_url=url)
        )

    @app_commands.command(
        name="ip", description="Staff: post the server address in this ticket"
    )
    @app_commands.guild_only()
    async def ip(self, interaction: discord.Interaction) -> None:
        ctx = await self.ticket_context(interaction)
        if ctx is None:
            return
        address = self.bot.settings.server_address
        if not address:
            await respond_text(
                interaction, get_copy().commands.ip_unconfigured, ephemeral=True
            )
            return
        await self.post_reply(
            interaction,
            ctx.ticket,
            get_copy().commands.ip_reply.format(server_address=address),
        )

    @app_commands.command(
        name="resolve", description="Staff: mark this ticket as resolved"
    )
    @app_commands.guild_only()
    async def resolve(self, interaction: discord.Interaction) -> None:
        ctx = await self.ticket_context(interaction)
        if ctx is None:
            return
        await interaction.response.defer(ephemeral=True)
        try:
            await flows.resolve_ticket(
                self.bot, ctx.ticket.id, ctx.actor, interaction.user, source="command"
            )
        except REFUSALS as e:
            await followup_text(interaction, flows.refusal_message(e) or str(e))
            return
        await followup_text(interaction, get_copy().commands.resolved)

    @app_commands.command(
        name="reopen", description="Staff: re-open this resolved ticket"
    )
    @app_commands.guild_only()
    async def reopen(self, interaction: discord.Interaction) -> None:
        ctx = await self.ticket_context(interaction)
        if ctx is None:
            return
        await interaction.response.defer(ephemeral=True)
        try:
            await flows.reopen_ticket(
                self.bot, ctx.ticket.id, ctx.actor, interaction.user, source="command"
            )
        except REFUSALS as e:
            await followup_text(interaction, flows.refusal_message(e) or str(e))
            return
        await followup_text(interaction, get_copy().commands.reopened)


async def setup(bot: SupportBot) -> None:
    await bot.add_cog(TicketCommands(bot))
