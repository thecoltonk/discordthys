"""Build embeds from their [embeds.*] style in copy.toml."""

from datetime import datetime

import discord

from nephthys.core.copytext import colour_value
from nephthys.core.copytext import EmbedStyle

TITLE_MAX = 256
DESCRIPTION_MAX = 4096
FOOTER_MAX = 2048


def make_embed(
    style: EmbedStyle,
    *,
    title: str | None,
    description: str | None,
    footer: str | None = None,
    color: str | None = None,
    url: str | None = None,
    timestamp: datetime | None = None,
) -> discord.Embed:
    """Callers pass already-formatted title/description (and footer when the
    style's footer has placeholders). Empty strings are left out."""
    colour = colour_value(style.color if color is None else color)
    embed = discord.Embed(
        title=title[:TITLE_MAX] if title else None,
        description=description[:DESCRIPTION_MAX] if description else None,
        colour=discord.Colour(colour) if colour is not None else None,
        url=url if title else None,
        timestamp=timestamp,
    )
    footer_text = style.footer if footer is None else footer
    if footer_text:
        embed.set_footer(text=footer_text[:FOOTER_MAX])
    if style.thumbnail_url:
        embed.set_thumbnail(url=style.thumbnail_url)
    return embed
