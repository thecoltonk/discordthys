"""Post notable events and errors to the staff log channel (if configured)."""

import logging
from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from nephthys.bot import SupportBot

log = logging.getLogger(__name__)

MAX_LEN = 1900


async def send_heartbeat(bot: "SupportBot", text: str, *, ping: bool = False) -> None:
    channel_id = bot.settings.log_channel_id
    if not channel_id:
        return
    maintainer = bot.settings.maintainer_id
    content = text if len(text) <= MAX_LEN else text[: MAX_LEN - 1] + "…"
    if ping and maintainer:
        content = f"<@{maintainer}> {content}"
    try:
        await bot.get_partial_messageable(channel_id).send(
            content,
            allowed_mentions=discord.AllowedMentions(
                everyone=False,
                roles=False,
                users=[discord.Object(maintainer)] if ping and maintainer else False,
            ),
        )
    except discord.HTTPException as e:
        log.error(f"Failed to send heartbeat: {e}")
