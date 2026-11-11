"""Persistent buttons.

These are `DynamicItem`s: the ticket ID lives in the `custom_id`, and the
classes are registered once in `SupportBot.setup_hook`. Buttons on any old
message keep working after restarts without storing per-message view state.
"""

import logging
import re
from typing import Any

import discord

from nephthys.core.transcript import TRANSCRIPT

log = logging.getLogger(__name__)


class ResolveButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"cs:resolve:(?P<ticket_id>[0-9]+)",
):
    def __init__(self, ticket_id: int) -> None:
        self.ticket_id = ticket_id
        super().__init__(
            discord.ui.Button(
                label=TRANSCRIPT.resolve_button,
                style=discord.ButtonStyle.success,
                emoji="✅",
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
                label=TRANSCRIPT.reopen_button,
                style=discord.ButtonStyle.secondary,
                emoji="🔄",
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


DYNAMIC_ITEMS = (ResolveButton, ReopenButton)
