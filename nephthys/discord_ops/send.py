"""The only way the bot sends messages.

- Text messages are always sent with `suppress_embeds=True`, so links (FAQ,
  thread links, anything players write into copy) never unfurl into previews.
- Rich-embed messages (cards, sticky, summaries, AI answers) carry no message
  content, so there's nothing to unfurl. `suppress_embeds` can't be used on them
  because Discord would hide the bot's own embed too.
- Mentions never ping unless a caller explicitly allows it.
"""

import re
from typing import Any

import discord

NO_MENTIONS = discord.AllowedMentions.none()
URL_PATTERN = re.compile(r"https?://", re.IGNORECASE)


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
    ping_content: str | None = None,
    ping_roles: list[int] | None = None,
) -> Any:
    """Send an embed message. The only content allowed alongside an embed is a
    link-free ping line (e.g. role mentions on a staff alert), so there's still
    nothing to unfurl."""
    if ping_content is not None and URL_PATTERN.search(ping_content):
        raise ValueError("ping_content must not contain links (they would unfurl)")
    allowed = (
        discord.AllowedMentions(
            everyone=False, users=False, roles=[discord.Object(r) for r in ping_roles]
        )
        if ping_roles
        else NO_MENTIONS
    )
    return await target.send(
        ping_content,
        embed=embed,
        allowed_mentions=allowed,
        **_extras(view, reference=reference),
    )


async def edit_text_response(
    interaction: discord.Interaction[Any], content: str
) -> None:
    """Replace a message's text from a component interaction, removing its buttons."""
    await interaction.response.edit_message(
        content=content, view=None, suppress_embeds=True
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


async def send_embeds(target: Any, embeds: list[discord.Embed]) -> Any:
    """Several embeds in one message (no content)."""
    return await target.send(embeds=embeds, allowed_mentions=NO_MENTIONS)


async def followup_embeds(
    interaction: discord.Interaction[Any],
    embeds: list[discord.Embed],
    *,
    ephemeral: bool = True,
) -> None:
    await interaction.followup.send(
        embeds=embeds, ephemeral=ephemeral, allowed_mentions=NO_MENTIONS
    )
