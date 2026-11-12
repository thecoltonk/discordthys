"""#tickets buttons (persistent DynamicItems, registered at startup)."""

import re
from typing import Any

import discord

from nephthys.core.copytext import get_copy
from nephthys.core.ticket_categories import Category
from nephthys.core.ticket_categories import SubCategory

CLOSE_ROW = 4  # Close Ticket always sits on the last row


class CreateTicketButton(
    discord.ui.DynamicItem[discord.ui.Button], template=r"cs:tcreate"
):
    def __init__(self) -> None:
        super().__init__(
            discord.ui.Button(
                label=get_copy().tickets.create_button,
                emoji="🎫",
                style=discord.ButtonStyle.primary,
                custom_id="cs:tcreate",
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction[Any],
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> "CreateTicketButton":
        return cls()

    async def callback(self, interaction: discord.Interaction[Any]) -> None:
        from nephthys.discord_ops.private_flow import create_from_interaction

        await create_from_interaction(interaction)


class CategoryButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"cs:tcat:(?P<ticket_id>[0-9]+):(?P<category>[a-z0-9_]+)",
):
    def __init__(
        self, ticket_id: int, category: str, label: str = "?", emoji: str | None = None
    ):
        self.ticket_id = ticket_id
        self.category = category
        super().__init__(
            discord.ui.Button(
                label=label,
                emoji=emoji,
                style=discord.ButtonStyle.secondary,
                custom_id=f"cs:tcat:{ticket_id}:{category}",
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction[Any],
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> "CategoryButton":
        return cls(int(match["ticket_id"]), match["category"])

    async def callback(self, interaction: discord.Interaction[Any]) -> None:
        from nephthys.discord_ops.private_flow import pick_from_interaction

        await pick_from_interaction(interaction, self.ticket_id, self.category, None)


class SubcategoryButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=(
        r"cs:tsub:(?P<ticket_id>[0-9]+):(?P<category>[a-z0-9_]+):(?P<sub>[a-z0-9_]+)"
    ),
):
    def __init__(
        self,
        ticket_id: int,
        category: str,
        sub: str,
        label: str = "?",
        emoji: str | None = None,
    ):
        self.ticket_id = ticket_id
        self.category = category
        self.sub = sub
        super().__init__(
            discord.ui.Button(
                label=label,
                emoji=emoji,
                style=discord.ButtonStyle.secondary,
                custom_id=f"cs:tsub:{ticket_id}:{category}:{sub}",
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction[Any],
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> "SubcategoryButton":
        return cls(int(match["ticket_id"]), match["category"], match["sub"])

    async def callback(self, interaction: discord.Interaction[Any]) -> None:
        from nephthys.discord_ops.private_flow import pick_from_interaction

        await pick_from_interaction(
            interaction, self.ticket_id, self.category, self.sub
        )


class CloseTicketButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"cs:tclose:(?P<ticket_id>[0-9]+)",
):
    def __init__(self, ticket_id: int) -> None:
        self.ticket_id = ticket_id
        super().__init__(
            discord.ui.Button(
                label=get_copy().tickets.close_button,
                emoji="🔒",
                style=discord.ButtonStyle.danger,
                custom_id=f"cs:tclose:{ticket_id}",
                row=CLOSE_ROW,
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction[Any],
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> "CloseTicketButton":
        return cls(int(match["ticket_id"]))

    async def callback(self, interaction: discord.Interaction[Any]) -> None:
        from nephthys.discord_ops.private_flow import close_from_interaction

        await close_from_interaction(interaction, self.ticket_id)


class CSATButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"cs:csat:(?P<ticket_id>[0-9]+):(?P<rating>[1-5])",
):
    def __init__(self, ticket_id: int, rating: int) -> None:
        self.ticket_id = ticket_id
        self.rating = rating
        super().__init__(
            discord.ui.Button(
                label=str(rating),
                emoji="⭐" if rating == 5 else None,
                style=discord.ButtonStyle.primary,
                custom_id=f"cs:csat:{ticket_id}:{rating}",
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction[Any],
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> "CSATButton":
        return cls(int(match["ticket_id"]), int(match["rating"]))

    async def callback(self, interaction: discord.Interaction[Any]) -> None:
        from nephthys.discord_ops.private_flow import csat_from_interaction

        await csat_from_interaction(interaction, self.ticket_id, self.rating)


def panel_view() -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(CreateTicketButton())
    return view


def opener_view(
    ticket_id: int,
    *,
    categories: tuple[Category, ...] = (),
    category_key: str | None = None,
    subcategories: tuple[SubCategory, ...] = (),
) -> discord.ui.View:
    """Category buttons, or subcategory buttons, or none (leaf) + Close Ticket."""
    view = discord.ui.View(timeout=None)
    for c in categories:
        view.add_item(CategoryButton(ticket_id, c.key, c.label, c.emoji))
    for sub in subcategories:
        assert category_key is not None
        view.add_item(
            SubcategoryButton(ticket_id, category_key, sub.key, sub.label, sub.emoji)
        )
    view.add_item(CloseTicketButton(ticket_id))
    return view


def csat_view(ticket_id: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for rating in range(1, 6):
        view.add_item(CSATButton(ticket_id, rating))
    return view


TICKET_DYNAMIC_ITEMS = (
    CreateTicketButton,
    CategoryButton,
    SubcategoryButton,
    CloseTicketButton,
    CSATButton,
)
