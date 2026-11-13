"""Post notable events and errors to the log channel (if configured).

Only real errors (`error=True`) notify anyone. Everything else (notices,
warnings about handled problems, "caught up") is sent with Discord's @silent
flag: it's in the channel to read, but doesn't buzz phones. Callers that can
repeat themselves rate-limit before calling (see `ZammadSync.alert`).
"""

import logging
from typing import TYPE_CHECKING

import discord

from nephthys.discord_ops.send import send_text

if TYPE_CHECKING:
    from nephthys.bot import SupportBot

log = logging.getLogger(__name__)

MAX_LEN = 1900


async def send_heartbeat(
    bot: "SupportBot", text: str, *, error: bool = False, ping: bool = False
) -> None:
    """`error`: something failed and needs a look, so it notifies. `ping` also
    mentions MAINTAINER_ID (and implies `error`)."""
    channel_id = bot.settings.log_channel_id
    if not channel_id:
        return
    error = error or ping
    maintainer = bot.settings.maintainer_id
    content = text if len(text) <= MAX_LEN else text[: MAX_LEN - 1] + "…"
    if ping and maintainer:
        content = f"<@{maintainer}> {content}"
    try:
        await send_text(
            bot.get_partial_messageable(channel_id),
            content,
            allowed_mentions=discord.AllowedMentions(
                everyone=False,
                roles=False,
                users=[discord.Object(maintainer)] if ping and maintainer else False,
            ),
            silent=not error,
        )
    except discord.HTTPException as e:
        log.error(f"Failed to send heartbeat: {e}")
