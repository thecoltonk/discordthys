"""Persistent buttons.

These are `DynamicItem`s: the ticket ID lives in the `custom_id`, and the
classes are registered once in `SupportBot.setup_hook`. Buttons on any old
message keep working after restarts without storing per-message view state.
"""

import logging
import re
from typing import Any
from typing import TypedDict

import discord

from nephthys.core.copytext import get_copy

log = logging.getLogger(__name__)


class ButtonFace(TypedDict):
    label: str | None
    emoji: str | None


def face(label: str, emoji: str) -> ButtonFace:
    """A button's label and emoji from copy; empty strings mean "none"."""
    return {"label": label or None, "emoji": emoji or None}


class ResolveButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"cs:resolve:(?P<ticket_id>[0-9]+)",
):
    def __init__(self, ticket_id: int) -> None:
        self.ticket_id = ticket_id
        super().__init__(
            discord.ui.Button(
                **face(
                    get_copy().community.resolve_button,
                    get_copy().community.resolve_button_emoji,
                ),
                style=discord.ButtonStyle.success,
                custom_id=f"cs:resolve:{ticket_id}",
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction[Any],
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> "ResolveButton":
        return cls(int(match["ticket_id"]))

    async def callback(self, interaction: discord.Interaction[Any]) -> None:
        from nephthys.discord_ops.flows import resolve_from_interaction

        await resolve_from_interaction(interaction, self.ticket_id)


class ReopenButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"cs:reopen:(?P<ticket_id>[0-9]+)",
):
    def __init__(self, ticket_id: int) -> None:
        self.ticket_id = ticket_id
        super().__init__(
            discord.ui.Button(
                **face(
                    get_copy().community.reopen_button,
                    get_copy().community.reopen_button_emoji,
                ),
                style=discord.ButtonStyle.secondary,
                custom_id=f"cs:reopen:{ticket_id}",
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction[Any],
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> "ReopenButton":
        return cls(int(match["ticket_id"]))

    async def callback(self, interaction: discord.Interaction[Any]) -> None:
        from nephthys.discord_ops.flows import reopen_from_interaction

        await reopen_from_interaction(interaction, self.ticket_id)


def resolve_view(ticket_id: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(ResolveButton(ticket_id))
    return view


def reopen_view(ticket_id: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(ReopenButton(ticket_id))
    return view


# ---------------------------------------------------------------------------
# Buttons of the removed generative assistant
# ---------------------------------------------------------------------------


class RetiredAIButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"cs:(?P<kind>aifb|aifix|aihuman):(?P<rest>[0-9a-z:]+)",
):
    """The generative assistant is gone, and the bot never posts these any
    more. The custom IDs of its buttons (👍/👎 on staff suggestions, "This fixed
    it" / "I still need a human" on public answers) stay registered only so
    that a click on an old message gets a private answer, not "This
    interaction failed". Nothing is recorded and no ticket is touched."""

    def __init__(self, custom_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label="-",
                style=discord.ButtonStyle.secondary,
                custom_id=custom_id,
                disabled=True,
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction[Any],
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> "RetiredAIButton":
        return cls(match.group(0))

    async def callback(self, interaction: discord.Interaction[Any]) -> None:
        from nephthys.discord_ops.send import respond_text

        try:
            await respond_text(
                interaction, get_copy().faq.retired_button, ephemeral=True
            )
        except discord.HTTPException as e:
            log.info(f"Couldn't answer a click on a retired AI button: {e}")


def card_links_view(
    thread_url: str, *, zammad_url: str | None = None
) -> discord.ui.View:
    """Staff card buttons: "Open in Zammad" (when there's a Zammad ticket to
    link to) first, then "Open in Discord"."""
    card = get_copy().card
    view = discord.ui.View(timeout=None)
    if zammad_url:
        view.add_item(
            discord.ui.Button(
                **face(card.open_zammad_button, card.open_zammad_button_emoji),
                url=zammad_url,
            )
        )
    view.add_item(
        discord.ui.Button(
            **face(card.open_thread_button, card.open_thread_button_emoji),
            url=thread_url,
        )
    )
    return view


DYNAMIC_ITEMS = (
    ResolveButton,
    ReopenButton,
    RetiredAIButton,
)
