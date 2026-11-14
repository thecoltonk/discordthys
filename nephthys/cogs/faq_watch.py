"""Watched channels (FAQ_WATCH_CHANNEL_IDS, e.g. #general): reply to questions
the FAQ answers, and nothing else. No tickets are ever created from here."""

import logging

import discord
from discord.ext import commands

from nephthys.bot import SupportBot
from nephthys.discord_ops import faq_flow

log = logging.getLogger(__name__)


class FAQWatch(commands.Cog):
    def __init__(self, bot: SupportBot):
        self.bot = bot
        self.settings = bot.settings
        self.channel_ids = frozenset(bot.settings.faq_watch_channel_ids)
        self.cooldowns = faq_flow.Cooldowns(
            bot.settings.faq_user_cooldown_seconds,
            bot.settings.faq_channel_cooldown_seconds,
        )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.guild.id != self.settings.guild_id:
            return
        # Only the watched channels themselves: not their threads, and never
        # #community-help or #tickets (config refuses those IDs)
        if message.channel.id not in self.channel_ids:
            return
        await faq_flow.handle_watched_message(self.bot, message, self.cooldowns)


async def setup(bot: SupportBot) -> None:
    await bot.add_cog(FAQWatch(bot))
