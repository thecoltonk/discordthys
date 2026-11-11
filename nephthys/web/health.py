"""Tiny HTTP server for the Docker healthcheck and Prometheus scraping."""

import asyncio
import logging

from aiohttp import web
from prometheus_client import CONTENT_TYPE_LATEST
from prometheus_client import generate_latest

from nephthys.bot import SupportBot

log = logging.getLogger(__name__)


async def check_database() -> bool:
    from nephthys.database.tables import Ticket

    try:
        await asyncio.wait_for(Ticket.raw("SELECT 1"), timeout=3)
        return True
    except Exception as e:  # noqa: BLE001 - any failure means unhealthy
        log.warning(f"Database health check failed: {e}")
        return False


def build_app(bot: SupportBot) -> web.Application:
    async def health(_request: web.Request) -> web.Response:
        discord_ready = bot.is_ready() and not bot.is_closed()
        database = bot.db_ready and await check_database()
        healthy = discord_ready and database
        return web.json_response(
            {
                "healthy": healthy,
                "discord_ready": discord_ready,
                "database": database,
                "latency_ms": round(bot.latency * 1000)
                if discord_ready and bot.latency == bot.latency  # NaN check
                else None,
            },
            status=200 if healthy else 503,
        )

    async def metrics(_request: web.Request) -> web.Response:
        return web.Response(
            body=generate_latest(),
            headers={"Content-Type": CONTENT_TYPE_LATEST},
        )

    async def root(_request: web.Request) -> web.Response:
        return web.Response(text="Cloudsail Support bot. See /health.")

    app = web.Application()
    app.router.add_get("/", root)
    app.router.add_get("/health", health)
    app.router.add_get("/metrics", metrics)
    return app


class HealthServer:
    def __init__(self, bot: SupportBot, port: int, host: str = "0.0.0.0"):
        self.runner = web.AppRunner(build_app(bot), access_log=None)
        self.port = port
        self.host = host

    async def start(self) -> None:
        await self.runner.setup()
        site = web.TCPSite(self.runner, self.host, self.port)
        await site.start()
        log.info(f"Health server listening on {self.host}:{self.port}")

    async def stop(self) -> None:
        await self.runner.cleanup()
