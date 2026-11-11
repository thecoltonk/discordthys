from dataclasses import dataclass
from typing import TYPE_CHECKING

import discord

from nephthys.core import tickets
from nephthys.core.permissions import Actor
from nephthys.database.enums import BotMessageKind
from nephthys.database.tables import Ticket

if TYPE_CHECKING:
    from nephthys.bot import SupportBot

ERROR_DELETE_AFTER = 10


@dataclass
class MacroContext:
    bot: "SupportBot"
    message: discord.Message
    ticket: Ticket
    actor: Actor
    # Everything after the macro name
    args: str

    @property
    def thread_id(self) -> int:
        return self.ticket.thread_id

    async def say(self, text: str) -> None:
        """Post in the ticket thread on behalf of staff, and record it."""
        msg = await self.message.channel.send(
            text, allowed_mentions=discord.AllowedMentions.none()
        )
        await tickets.record_bot_message(
            self.ticket.id, self.thread_id, msg.id, BotMessageKind.MACRO
        )

    async def error(self, text: str) -> None:
        """A short-lived reply (plain messages can't be ephemeral)."""
        try:
            await self.message.channel.send(
                text,
                delete_after=ERROR_DELETE_AFTER,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            pass


class Macro:
    name: str
    aliases: tuple[str, ...] = ()
    description: str = ""
    # Allowed on resolved tickets?
    can_run_on_closed: bool = False
    # Does running it count as a staff reply (claims, updates last message)?
    counts_as_reply: bool = False

    def all_names(self) -> set[str]:
        return {self.name, *self.aliases}

    async def run(self, ctx: MacroContext) -> None:
        raise NotImplementedError
