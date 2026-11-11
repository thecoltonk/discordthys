"""The only way the bot sends messages.

- Text messages are always sent with `suppress_embeds=True`, so links (FAQ,
  thread links, anything players write into copy) never unfurl into previews.
- Rich-embed messages (cards, sticky, summaries, AI answers) carry no message
  content, so there's nothing to unfurl. `suppress_embeds` can't be used on them
  because Discord would hide the bot's own embed too.
- Mentions never ping unless a caller explicitly allows it.
"""

from typing import Any

import discord

NO_MENTIONS = discord.AllowedMentions.none()


def _extras(view: discord.ui.View | None, **kwargs: Any) -> dict[str, Any]:
    extras = {k: v for k, v in kwargs.items() if v is not None}
    if view is not None:
        extras["view"] = view
    return extras


async def send_text(
    target: Any,
    content: str,
    *,
    view: discord.ui.View | None = None,
    allowed_mentions: discord.AllowedMentions = NO_MENTIONS,
    delete_after: float | None = None,
    reference: Any = None,
) -> Any:
    """Send a plain-text message to a channel, thread or partial messageable."""
    return await target.send(
        content,
        suppress_embeds=True,
        allowed_mentions=allowed_mentions,
        **_extras(view, delete_after=delete_after, reference=reference),
    )


async def send_embed(
    target: Any,
    embed: discord.Embed,
    *,
    view: discord.ui.View | None = None,
    reference: Any = None,
) -> Any:
    """Send an embed-only message (no content, so nothing can unfurl)."""
    return await target.send(
        embed=embed,
        allowed_mentions=NO_MENTIONS,
        **_extras(view, reference=reference),
    )


async def respond_text(
    interaction: discord.Interaction[Any], content: str, *, ephemeral: bool
) -> None:
    await interaction.response.send_message(
        content,
        ephemeral=ephemeral,
        suppress_embeds=True,
        allowed_mentions=NO_MENTIONS,
    )


async def followup_text(
    interaction: discord.Interaction[Any], content: str, *, ephemeral: bool = True
) -> None:
    await interaction.followup.send(
        content,
        ephemeral=ephemeral,
        suppress_embeds=True,
        allowed_mentions=NO_MENTIONS,
    )


async def followup_embed(
    interaction: discord.Interaction[Any],
    embed: discord.Embed,
    *,
    ephemeral: bool = True,
) -> None:
    await interaction.followup.send(
        embed=embed, ephemeral=ephemeral, allowed_mentions=NO_MENTIONS
    )
