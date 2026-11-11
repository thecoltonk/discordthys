import logging
import sys
from datetime import datetime
from datetime import timedelta
from datetime import UTC
from typing import Any

import discord
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from discord.ext import commands

from nephthys.config import Settings

log = logging.getLogger(__name__)

# Cogs loaded on startup, in order
EXTENSIONS: list[str] = [
    "nephthys.cogs.community_help",
    "nephthys.cogs.staff",
]


def build_intents() -> discord.Intents:
    """Only `message_content` is privileged; the members intent isn't needed
    because guild message and interaction payloads include the author's roles."""
    intents = discord.Intents.none()
    intents.guilds = True
    intents.guild_messages = True
    intents.guild_reactions = True
    intents.message_content = True
    return intents


class SupportBot(commands.Bot):
    def __init__(self, settings: Settings, *, connect_db: bool = True, **kwargs: Any):
        super().__init__(
            command_prefix=commands.when_mentioned,
            help_command=None,
            intents=build_intents(),
            allowed_mentions=discord.AllowedMentions(
                everyone=False, roles=False, users=True, replied_user=False
            ),
            **kwargs,
        )
        self.settings = settings
        self.connect_db = connect_db
        self.scheduler = AsyncIOScheduler(timezone=settings.timezone)
        self.db_ready = False
        self.announced_online = False

    @property
    def guild_object(self) -> discord.Object:
        return discord.Object(id=self.settings.guild_id)

    async def setup_hook(self) -> None:
        if self.connect_db:
            from piccolo_conf import DB

            await DB.start_connection_pool()
            self.db_ready = True
            log.info("Database connection pool started")

        from nephthys.ui.buttons import DYNAMIC_ITEMS

        self.add_dynamic_items(*DYNAMIC_ITEMS)

        for ext in EXTENSIONS:
            await self.load_extension(ext)
            log.info(f"Loaded extension {ext}")

        # Guild-scoped commands show up immediately (global ones can take an hour)
        self.tree.copy_global_to(guild=self.guild_object)
        synced = await self.tree.sync(guild=self.guild_object)
        log.info(f"Synced {len(synced)} application commands to guild")

        if self.settings.stale_ticket_days:
            self.scheduler.add_job(
                self.run_stale_close,
                "interval",
                hours=1,
                max_instances=1,
                coalesce=True,
                next_run_time=datetime.now(UTC) + timedelta(minutes=2),
            )
        self.scheduler.start()

    async def run_stale_close(self) -> None:
        from nephthys.tasks.close_stale import close_stale_tickets

        await self.wait_until_ready()
        await close_stale_tickets(self)

    async def on_ready(self) -> None:
        log.info(f"Logged in as {self.user} (id={self.user.id if self.user else '?'})")
        if not self.announced_online:
            self.announced_online = True
            from nephthys.discord_ops.heartbeat import send_heartbeat

            await send_heartbeat(self, "🟢 Cloudsail Support is online")

    async def on_error(self, event_method: str, /, *args: Any, **kwargs: Any) -> None:
        log.exception(f"Unhandled error in {event_method}")
        from nephthys.discord_ops.heartbeat import send_heartbeat

        exc = sys.exc_info()[1]
        await send_heartbeat(
            self,
            f"❌ Unhandled error in `{event_method}`: `{type(exc).__name__}: {exc}`",
            ping=True,
        )

    async def close(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
        await super().close()
        if self.db_ready:
            from piccolo_conf import DB

            await DB.close_connection_pool()
            self.db_ready = False
