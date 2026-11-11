"""#tickets: the sticky ticket panel, private ticket threads, and /escalate."""

import logging

import discord
from discord import app_commands
from discord.ext import commands

from nephthys.bot import SupportBot
from nephthys.core import private_tickets
from nephthys.core import tickets
from nephthys.core.copytext import get_copy
from nephthys.core.users import get_user
from nephthys.database.enums import TicketStatus
from nephthys.database.enums import TicketSurface
from nephthys.discord_ops import private_flow
from nephthys.discord_ops import staff_card
from nephthys.discord_ops import threads
from nephthys.discord_ops.flows import is_question
from nephthys.discord_ops.private_flow import Player
from nephthys.discord_ops.send import followup_text
from nephthys.discord_ops.send import respond_text
from nephthys.discord_ops.send import send_text
from nephthys.discord_ops.sticky import StickyManager
from nephthys.errors.errors import TicketAlreadyClosedError
from nephthys.ui.ticket_buttons import panel_view

log = logging.getLogger(__name__)

PANEL_COLOUR = discord.Colour.blurple()


def panel_embed(community_channel_id: int) -> discord.Embed:
    copy = get_copy().tickets
    return discord.Embed(
        title=copy.panel_title,
        description=copy.panel_body.format(
            community_channel=f"<#{community_channel_id}>"
        ),
        colour=PANEL_COLOUR,
    )


class Tickets(commands.Cog):
    def __init__(self, bot: SupportBot):
        self.bot = bot
        self.settings = bot.settings
        self.sticky: StickyManager | None = None
        if bot.settings.tickets_channel_id:
            community = bot.settings.community_channel_id
            self.sticky = StickyManager(
                bot,
                bot.settings.tickets_channel_id,
                bot.settings.sticky_debounce_seconds,
                render=lambda: (panel_embed(community), panel_view()),
            )

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if self.sticky is not None:
            try:
                await self.sticky.ensure()
            except discord.HTTPException as e:
                log.warning(f"Couldn't post the ticket panel: {e}")

    def is_private_ticket_thread(self, channel: object) -> bool:
        return (
            self.settings.tickets_channel_id is not None
            and isinstance(channel, discord.Thread)
            and channel.parent_id == self.settings.tickets_channel_id
        )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.guild.id != self.settings.guild_id:
            return
        if self.settings.tickets_channel_id is None:
            return
        if message.channel.id == self.settings.tickets_channel_id:
            # Keep the panel at the bottom: people posting, and the "X started a
            # thread" notices Discord may add. Never our own messages (the panel).
            if self.sticky is not None and (
                is_question(message)
                or message.type == discord.MessageType.thread_created
            ):
                self.sticky.schedule()
            return
        if self.is_private_ticket_thread(message.channel) and is_question(message):
            ticket = await tickets.get_ticket_by_thread(message.channel.id)
            if ticket is None:
                return
            actor = threads.actor_from_user(message.author, self.settings.staff_role_id)
            await private_flow.record_private_message(self.bot, message, ticket, actor)

    @app_commands.command(
        name="escalate",
        description="Staff: move this #community-help ticket to a private #tickets thread",
    )
    @app_commands.describe(
        delete_original="Also delete the public question (it's copied into the private ticket)"
    )
    @app_commands.guild_only()
    async def escalate(
        self, interaction: discord.Interaction, delete_original: bool = False
    ) -> None:
        copy = get_copy()
        settings = self.settings
        actor = threads.actor_from_user(interaction.user, settings.staff_role_id)
        if not actor.is_staff:
            await respond_text(interaction, copy.errors.staff_only, ephemeral=True)
            return
        if not settings.tickets_enabled:
            await respond_text(
                interaction, copy.tickets.tickets_disabled, ephemeral=True
            )
            return
        channel = interaction.channel
        ticket = None
        if (
            isinstance(channel, discord.Thread)
            and channel.parent_id == settings.community_channel_id
        ):
            ticket = await tickets.get_ticket_by_thread(channel.id)
        if ticket is None or ticket.surface != TicketSurface.COMMUNITY:
            await respond_text(
                interaction, copy.tickets.not_community_thread, ephemeral=True
            )
            return
        existing = await private_tickets.escalation_of(ticket.id)
        if existing is not None:
            await respond_text(
                interaction,
                copy.tickets.already_escalated.format(
                    link=private_flow.ticket_link(self.bot, existing)
                ),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        opener = await get_user(tickets.fk_id(ticket.opened_by))
        if opener is None:
            await followup_text(interaction, copy.errors.ticket_missing)
            return
        player = Player(
            discord_id=opener.discord_id,
            display_name=opener.username or str(opener.discord_id),
        )
        try:
            private = await private_flow.open_private_ticket(
                self.bot,
                player,
                escalated_from=ticket,
                quote=ticket.description if delete_original else None,
            )
        except private_flow.TicketCreateError:
            await followup_text(interaction, copy.tickets.create_failed)
            return

        await self.close_public_side(ticket.id, interaction, private, delete_original)
        await followup_text(
            interaction,
            copy.tickets.escalate_done.format(
                link=private_flow.ticket_link(self.bot, private)
            ),
        )

    async def close_public_side(
        self,
        ticket_id: int,
        interaction: discord.Interaction,
        private: tickets.Ticket,
        delete_original: bool,
    ) -> None:
        """Note + link in the public thread, resolve, ✅, lock + archive."""
        copy = get_copy()
        bot = self.bot
        ticket = await tickets.require_ticket(ticket_id)
        actor = threads.actor_from_user(interaction.user, self.settings.staff_role_id)
        if ticket.status != TicketStatus.CLOSED:
            try:
                await tickets.resolve(ticket.id, actor)
            except TicketAlreadyClosedError:
                pass
        thread = await threads.get_thread(bot, ticket.thread_id)
        opener = await get_user(tickets.fk_id(ticket.opened_by))
        if thread is not None:
            await threads.set_archived(thread, False)
            async with threads.best_effort("send_escalated_public"):
                await send_text(
                    thread,
                    copy.tickets.escalated_public.format(
                        user=f"<@{opener.discord_id}>" if opener else copy.card.someone,
                        link=private_flow.ticket_link(bot, private),
                    ),
                )
        await threads.add_reaction(bot, ticket, self.settings.resolved_reaction)
        await threads.remove_own_reaction(bot, ticket, self.settings.open_reaction)
        if thread is not None:
            async with threads.best_effort("lock_escalated"):
                await thread.edit(locked=True, archived=True)
        if delete_original and ticket.message_id:
            async with threads.best_effort("delete_escalated_question"):
                await threads.question_message(bot, ticket).delete()
        await staff_card.refresh_staff_card(bot, ticket.id)


async def setup(bot: SupportBot) -> None:
    await bot.add_cog(Tickets(bot))
