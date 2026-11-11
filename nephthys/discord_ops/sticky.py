"""Sticky messages kept at the bottom of a channel: the "how it works" note in
#community-help and the ticket panel in #tickets.

After a new top-level question, the sticky is reposted once things have been
quiet for `debounce` seconds. The new sticky is sent *before* the old one is
deleted, so the channel is never without one. The sticky is a bot message, so
it never creates a ticket or thread.
"""

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

import discord

from nephthys.core.copytext import get_copy
from nephthys.database.tables import StickyMessage
from nephthys.discord_ops.send import send_embed

if TYPE_CHECKING:
    from nephthys.bot import SupportBot

log = logging.getLogger(__name__)

STICKY_COLOUR = discord.Colour.blurple()


def build_sticky_embed(faq_url: str) -> discord.Embed:
    """The sticky, without the FAQ line when FAQ_URL is unset."""
    sticky = get_copy().sticky
    description = sticky.body.format(faq_url=faq_url) if faq_url else sticky.body_no_faq
    return discord.Embed(
        title=sticky.title, description=description, colour=STICKY_COLOUR
    )


type StickyRender = Callable[[], tuple[discord.Embed, discord.ui.View | None]]


class StickyManager:
    def __init__(
        self,
        bot: "SupportBot",
        channel_id: int,
        debounce: float,
        render: StickyRender | None = None,
    ):
        self.bot = bot
        self.channel_id = channel_id
        self.debounce = debounce
        # Builds the sticky's embed (and optional buttons); defaults to the
        # #community-help "how it works" message
        self.render: StickyRender = render or (
            lambda: (build_sticky_embed(bot.settings.faq_url), None)
        )
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self._dirty_at = 0.0

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
            embed, view = self.render()
            new = await send_embed(channel, embed, view=view)
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
