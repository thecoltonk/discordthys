"""#community-help: every top-level message is a ticket with its own thread."""

import logging

import discord
from discord.ext import commands

from nephthys.bot import SupportBot
from nephthys.core import tickets
from nephthys.discord_ops import flows
from nephthys.discord_ops import threads
from nephthys.discord_ops.sticky import StickyManager

log = logging.getLogger(__name__)


class CommunityHelp(commands.Cog):
    def __init__(self, bot: SupportBot):
        self.bot = bot
        self.settings = bot.settings
        self.sticky: StickyManager | None = (
            StickyManager(
                bot,
                bot.settings.community_channel_id,
                bot.settings.sticky_debounce_seconds,
            )
            if bot.settings.sticky_enabled
            else None
        )

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if self.sticky is not None:
            try:
                await self.sticky.ensure()
            except discord.HTTPException as e:
                log.warning(f"Couldn't post the sticky message: {e}")

    def is_community_thread(self, channel: object) -> bool:
        return (
            isinstance(channel, discord.Thread)
            and channel.parent_id == self.settings.community_channel_id
        )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.guild.id != self.settings.guild_id:
            return
        if not flows.is_question(message):
            # Bots (including our own sticky), webhooks and system messages
            return

        if message.channel.id == self.settings.community_channel_id:
            await flows.open_ticket(self.bot, message)
            await self.after_new_question()
            return

        if self.is_community_thread(message.channel):
            await self.handle_thread_message(message)

    async def handle_thread_message(self, message: discord.Message) -> None:
        ticket = await tickets.get_ticket_by_thread(message.channel.id)
        if not ticket:
            return  # a thread we didn't create
        actor = threads.actor_from_user(message.author, self.settings.staff_role_id)
        await flows.record_reply(self.bot, message, ticket, actor)

    async def after_new_question(self) -> None:
        """Keep the sticky "how it works" message at the bottom."""
        if self.sticky is not None:
            self.sticky.schedule()

    @commands.Cog.listener()
    async def on_raw_message_delete(
        self, payload: discord.RawMessageDeleteEvent
    ) -> None:
        if payload.channel_id == self.settings.community_channel_id:
            await flows.handle_question_deleted(self.bot, payload.message_id)

    @commands.Cog.listener()
    async def on_raw_bulk_message_delete(
        self, payload: discord.RawBulkMessageDeleteEvent
    ) -> None:
        if payload.channel_id == self.settings.community_channel_id:
            for message_id in payload.message_ids:
                await flows.handle_question_deleted(self.bot, message_id)

    @commands.Cog.listener()
    async def on_raw_thread_delete(self, payload: discord.RawThreadDeleteEvent) -> None:
        if payload.parent_id == self.settings.community_channel_id:
            await flows.handle_thread_deleted(self.bot, payload.thread_id)


async def setup(bot: SupportBot) -> None:
    await bot.add_cog(CommunityHelp(bot))
