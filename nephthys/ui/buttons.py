"""Persistent buttons.

These are `DynamicItem`s: the ticket ID lives in the `custom_id`, and the
classes are registered once in `SupportBot.setup_hook`. Buttons on any old
message keep working after restarts without storing per-message view state.
"""

import logging
import re
from typing import Any

import discord

from nephthys.core.copytext import get_copy

log = logging.getLogger(__name__)


class ResolveButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"cs:resolve:(?P<ticket_id>[0-9]+)",
):
    def __init__(self, ticket_id: int) -> None:
        self.ticket_id = ticket_id
        super().__init__(
            discord.ui.Button(
                label=get_copy().community.resolve_button,
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
                label=get_copy().community.reopen_button,
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


# ---------------------------------------------------------------------------
# AI answers
# ---------------------------------------------------------------------------


class AIFeedbackButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"cs:aifb:(?P<suggestion_id>[0-9]+):(?P<verdict>up|down)",
):
    """Shadow mode: staff rate a suggestion 👍 / 👎."""

    def __init__(self, suggestion_id: int, verdict: str) -> None:
        self.suggestion_id = suggestion_id
        self.verdict = verdict
        up = verdict == "up"
        ai_copy = get_copy().ai
        super().__init__(
            discord.ui.Button(
                label=ai_copy.helpful_button if up else ai_copy.not_helpful_button,
                emoji="👍" if up else "👎",
                style=discord.ButtonStyle.success if up else discord.ButtonStyle.danger,
                custom_id=f"cs:aifb:{suggestion_id}:{verdict}",
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction[Any],
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> "AIFeedbackButton":
        return cls(int(match["suggestion_id"]), match["verdict"])

    async def callback(self, interaction: discord.Interaction[Any]) -> None:
        from nephthys.discord_ops.ai_flow import shadow_feedback_from_interaction

        await shadow_feedback_from_interaction(
            interaction, self.suggestion_id, helpful=self.verdict == "up"
        )


class AIFixedButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"cs:aifix:(?P<suggestion_id>[0-9]+)",
):
    """Public mode: "This fixed it" (resolves the ticket)."""

    def __init__(self, suggestion_id: int) -> None:
        self.suggestion_id = suggestion_id
        super().__init__(
            discord.ui.Button(
                label=get_copy().ai.fixed_button,
                emoji="✅",
                style=discord.ButtonStyle.success,
                custom_id=f"cs:aifix:{suggestion_id}",
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction[Any],
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> "AIFixedButton":
        return cls(int(match["suggestion_id"]))

    async def callback(self, interaction: discord.Interaction[Any]) -> None:
        from nephthys.discord_ops.ai_flow import public_outcome_from_interaction

        await public_outcome_from_interaction(
            interaction, self.suggestion_id, fixed=True
        )


class AINeedHumanButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"cs:aihuman:(?P<suggestion_id>[0-9]+)",
):
    """Public mode: "I still need a human"."""

    def __init__(self, suggestion_id: int) -> None:
        self.suggestion_id = suggestion_id
        super().__init__(
            discord.ui.Button(
                label=get_copy().ai.need_human_button,
                emoji="🙋",
                style=discord.ButtonStyle.secondary,
                custom_id=f"cs:aihuman:{suggestion_id}",
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction[Any],
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> "AINeedHumanButton":
        return cls(int(match["suggestion_id"]))

    async def callback(self, interaction: discord.Interaction[Any]) -> None:
        from nephthys.discord_ops.ai_flow import public_outcome_from_interaction

        await public_outcome_from_interaction(
            interaction, self.suggestion_id, fixed=False
        )


def card_links_view(
    thread_url: str, *, zammad_url: str | None = None
) -> discord.ui.View:
    """Staff card buttons: "Open in Zammad" (when there's a Zammad ticket to
    link to) first, then "Open in Discord"."""
    card = get_copy().card
    view = discord.ui.View(timeout=None)
    if zammad_url:
        view.add_item(
            discord.ui.Button(label=card.open_zammad_button, url=zammad_url, emoji="🎫")
        )
    view.add_item(
        discord.ui.Button(label=card.open_thread_button, url=thread_url, emoji="🧵")
    )
    return view


def shadow_feedback_view(suggestion_id: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(AIFeedbackButton(suggestion_id, "up"))
    view.add_item(AIFeedbackButton(suggestion_id, "down"))
    return view


def public_answer_view(suggestion_id: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(AIFixedButton(suggestion_id))
    view.add_item(AINeedHumanButton(suggestion_id))
    return view


DYNAMIC_ITEMS = (
    ResolveButton,
    ReopenButton,
    AIFeedbackButton,
    AIFixedButton,
    AINeedHumanButton,
)
