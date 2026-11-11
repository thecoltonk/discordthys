"""Sticky "how it works" message kept at the bottom of #community-help.

After a new top-level question, the sticky is reposted once things have been
quiet for `debounce` seconds. The new sticky is sent *before* the old one is
deleted, so the channel is never without one. The sticky is a bot message, so
it never creates a ticket or thread.
"""

import asyncio
import logging
from typing import TYPE_CHECKING

import discord

from nephthys.database.tables import StickyMessage

if TYPE_CHECKING:
    from nephthys.bot import SupportBot

log = logging.getLogger(__name__)

STICKY_COLOUR = discord.Colour.blurple()


def build_sticky_embed(title: str, body: str, faq_url: str) -> discord.Embed:
    description = body.replace("{faq_url}", faq_url or "(ask staff)")
    return discord.Embed(title=title, description=description, colour=STICKY_COLOUR)


class StickyManager:
    def __init__(self, bot: "SupportBot", channel_id: int, debounce: float):
        self.bot = bot
        self.channel_id = channel_id
        self.debounce = debounce
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self._dirty_at = 0.0

    def embed(self) -> discord.Embed:
        s = self.bot.settings
        return build_sticky_embed(s.sticky_title, s.sticky_body, s.faq_url)

    def schedule(self) -> None:
        """Note new activity; repost once it's been quiet for `debounce` seconds."""
        loop = asyncio.get_running_loop()
        self._dirty_at = loop.time()
        if self._task is None or self._task.done():
            self._task = loop.create_task(self._run(), name="sticky-repost")

    async def wait_idle(self) -> None:
        """For tests and shutdown: wait for any pending repost to finish."""
        if self._task is not None:
            await self._task

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            while (wait := self._dirty_at + self.debounce - loop.time()) > 0:
                await asyncio.sleep(wait)
            started_for = self._dirty_at
            try:
                await self.repost()
            except Exception:
                log.exception("Failed to repost sticky message")
                return
            if self._dirty_at == started_for:
                return  # nothing new arrived while we were reposting

    async def repost(self) -> None:
        async with self._lock:
            channel = self.bot.get_partial_messageable(self.channel_id)
            previous = (
                await StickyMessage.objects()
                .where(StickyMessage.channel_id == self.channel_id)
                .first()
            )
            new = await channel.send(
                embed=self.embed(), allowed_mentions=discord.AllowedMentions.none()
            )
            await StickyMessage.insert(
                StickyMessage(channel_id=self.channel_id, message_id=new.id)
            ).on_conflict(
                target=StickyMessage.channel_id,
                action="DO UPDATE",
                values=[StickyMessage.message_id, StickyMessage.updated_at],
            )
            if previous and previous.message_id != new.id:
                try:
                    await channel.get_partial_message(previous.message_id).delete()
                except discord.NotFound:
                    pass
                except discord.HTTPException as e:
                    log.warning(f"Couldn't delete old sticky: {e}")
            log.debug(f"Sticky reposted message_id={new.id}")

    async def ensure(self) -> None:
        """On startup: repost unless the sticky is already the newest message."""
        current = (
            await StickyMessage.objects()
            .where(StickyMessage.channel_id == self.channel_id)
            .first()
        )
        channel = self.bot.get_channel(self.channel_id)
        if current and isinstance(channel, discord.TextChannel):
            try:
                newest = [m async for m in channel.history(limit=1)]
            except discord.HTTPException as e:
                log.warning(f"Couldn't read #community-help history: {e}")
                newest = []
            if newest and newest[0].id == current.message_id:
                return
        await self.repost()
