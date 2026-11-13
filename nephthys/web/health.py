"""Tiny HTTP server for the Docker healthcheck and Prometheus scraping."""

import asyncio
import json
import logging

from aiohttp import web
from prometheus_client import CONTENT_TYPE_LATEST
from prometheus_client import generate_latest

from nephthys.bot import SupportBot
from nephthys.utils.metrics import ZAMMAD_WEBHOOKS

ZAMMAD_WEBHOOK_PATH = "/zammad/webhook"

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

    async def zammad_webhook(request: web.Request) -> web.Response:
        """Zammad's trigger calls this. The body must carry a valid
        `X-Hub-Signature` (HMAC-SHA1 with ZAMMAD_WEBHOOK_SECRET); the handler
        only queues work, so Zammad gets its answer immediately."""
        from nephthys.zammad.text import verify_signature

        sync = getattr(bot, "zammad", None)
        secret = bot.settings.zammad_webhook_secret
        if sync is None or not secret:
            return web.json_response({"error": "Zammad sync is off"}, status=404)
        body = await request.read()
        if not verify_signature(secret, body, request.headers.get("X-Hub-Signature")):
            ZAMMAD_WEBHOOKS.labels(result="bad_signature").inc()
            log.warning(
                f"Rejected a Zammad webhook call with a bad signature from {request.remote}"
            )
            return web.json_response({"error": "invalid signature"}, status=401)
        try:
            payload = json.loads(body)
        except ValueError:
            payload = None
        if not isinstance(payload, dict):
            ZAMMAD_WEBHOOKS.labels(result="bad_payload").inc()
            return web.json_response({"error": "invalid JSON"}, status=400)
        try:
            result = await sync.handle_webhook(payload)
        except Exception as e:
            ZAMMAD_WEBHOOKS.labels(result="error").inc()
            log.exception("Zammad webhook handling failed")
            return web.json_response({"error": type(e).__name__}, status=503)
        ZAMMAD_WEBHOOKS.labels(result="ok").inc()
        return web.json_response({"ok": True, "result": result})

    app = web.Application(client_max_size=8 * 1024 * 1024)
    app.router.add_post(ZAMMAD_WEBHOOK_PATH, zammad_webhook)
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
