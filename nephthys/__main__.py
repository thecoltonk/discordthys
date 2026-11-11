import asyncio
import logging
import sys

from nephthys.config import ConfigError
from nephthys.config import get_settings
from nephthys.config import Settings
from nephthys.utils.logging import parse_level_name
from nephthys.utils.logging import setup_otel_logging


def configure_logging(settings: Settings) -> None:
    handler = logging.StreamHandler()
    handler.setLevel(parse_level_name(settings.log_level))
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.handlers = [handler]
    # discord.py is very chatty at DEBUG
    logging.getLogger("discord").setLevel(logging.INFO)
    if settings.otel_logs_url:
        setup_otel_logging(
            endpoint=settings.otel_logs_url,
            service_name=settings.otel_service_name,
            environment=settings.environment,
            basic_auth=settings.otel_logs_basic_auth,
            level=settings.log_level_otel,
        )


async def run(settings: Settings) -> None:
    from nephthys.bot import SupportBot
    from nephthys.web.health import HealthServer

    bot = SupportBot(settings)
    health = HealthServer(bot, settings.port)
    await health.start()
    try:
        async with bot:
            await bot.start(settings.discord_token)
    finally:
        await health.stop()


def start() -> None:
    try:
        settings = get_settings()
    except ConfigError as e:
        print(e, file=sys.stderr)
        sys.exit(2)
    configure_logging(settings)

    try:
        import uvloop

        runner = uvloop.run
    except ImportError:
        runner = asyncio.run

    import aiohttp
    import discord

    try:
        runner(run(settings))
    except KeyboardInterrupt:
        pass
    except discord.LoginFailure:
        logging.critical(
            "Discord rejected DISCORD_TOKEN. Reset the bot token and update .env."
        )
        sys.exit(1)
    except discord.PrivilegedIntentsRequired:
        logging.critical(
            "The Message Content intent is disabled. Enable it in the Discord developer "
            "portal (your app → Bot → Privileged Gateway Intents) and restart."
        )
        sys.exit(1)
    except (aiohttp.ClientConnectionError, OSError) as e:
        logging.critical(
            f"Can't reach Discord or the database: {e}. Will exit and let the container restart."
        )
        sys.exit(1)


if __name__ == "__main__":
    start()
