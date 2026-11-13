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


def load_copy_or_exit(settings: Settings) -> None:
    """Load copy/copy.toml (all message text). Invalid copy is fatal at startup;
    later edits are validated on reload and ignored if invalid."""
    from nephthys.core.copytext import configure_copy
    from nephthys.core.copytext import CopyError

    for name in settings.deprecated_env_set:
        logging.warning(
            f"{name} is no longer used: message text now lives in "
            f"{settings.copy_file}. Move your text there and unset {name}."
        )
    from nephthys.core.ticket_categories import configure_ticket_categories
    from nephthys.core.ticket_categories import TicketCategoriesError

    try:
        configure_copy(
            settings.copy_file,
            required=settings.copy_file_explicit,
            override=settings.copy_override_file,
            override_required=settings.copy_override_file_explicit,
        )
        configure_ticket_categories(
            settings.ticket_categories_file,
            required=settings.ticket_categories_file_explicit,
        )
    except (CopyError, TicketCategoriesError) as e:
        print(e, file=sys.stderr)
        sys.exit(2)


def zammad_setup(settings: Settings) -> int:
    """`nephthys zammad-setup`: create/update what the sync needs in Zammad."""
    from nephthys.zammad.setup import format_report
    from nephthys.zammad.setup import redact
    from nephthys.zammad.setup import run_setup

    report = asyncio.run(run_setup(settings))
    print(redact(format_report(report), settings))
    return 0 if report.success else 1


def zammad_retry(settings: Settings) -> int:
    """`nephthys zammad-retry`: re-queue sync items the worker gave up on. The
    running bot picks them up within seconds."""

    async def run() -> int:
        from nephthys.zammad import outbox
        from piccolo_conf import DB

        await DB.start_connection_pool()
        try:
            return await outbox.retry_failed()
        finally:
            await DB.close_connection_pool()

    print(f"Re-queued {asyncio.run(run())} failed Zammad sync item(s).")
    return 0


COMMANDS = {"zammad-setup": zammad_setup, "zammad-retry": zammad_retry}


def start() -> None:
    try:
        settings = get_settings()
    except ConfigError as e:
        print(e, file=sys.stderr)
        sys.exit(2)
    configure_logging(settings)
    load_copy_or_exit(settings)

    if len(sys.argv) > 1:
        command = COMMANDS.get(sys.argv[1])
        if command is None:
            print(
                f"Unknown command {sys.argv[1]!r}. Commands: {', '.join(COMMANDS)} "
                "(no command starts the bot).",
                file=sys.stderr,
            )
            sys.exit(2)
        sys.exit(command(settings))

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
