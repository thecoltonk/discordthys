"""Staff `?macros`, typed as the first word of a message in a ticket thread.

`?ticket` (escalate to a private #tickets thread) arrives with #tickets in v2.
"""

import logging
import re
from typing import TYPE_CHECKING

import discord

from nephthys.core import tickets
from nephthys.core.permissions import Actor
from nephthys.core.transcript import TRANSCRIPT
from nephthys.database.enums import TicketStatus
from nephthys.database.tables import Ticket
from nephthys.macros.faq import FAQ
from nephthys.macros.ip import IP
from nephthys.macros.reopen import Reopen
from nephthys.macros.resolve import Resolve
from nephthys.macros.types import Macro
from nephthys.macros.types import MacroContext
from nephthys.utils.metrics import MACROS_RUN
from nephthys.utils.metrics import TICKETS_CLAIMED

if TYPE_CHECKING:
    from nephthys.bot import SupportBot

log = logging.getLogger(__name__)

MACROS: list[Macro] = [FAQ(), IP(), Resolve(), Reopen()]

MACRO_PATTERN = re.compile(
    r"^\?([a-z][a-z0-9_]*)(?:\s+(.*))?$", re.IGNORECASE | re.DOTALL
)


def find_macro(name: str) -> Macro | None:
    name = name.lower()
    return next((m for m in MACROS if name in m.all_names()), None)


def parse_macro(text: str) -> tuple[str, str] | None:
    """`"?faq  extra"` -> `("faq", "extra")`; non-macros -> None."""
    match = MACRO_PATTERN.match(text.strip())
    if not match:
        return None
    return match.group(1).lower(), (match.group(2) or "").strip()


async def run_macro(
    bot: "SupportBot", message: discord.Message, ticket: Ticket, actor: Actor
) -> bool:
    """Run a staff macro. Returns True if the message was a macro invocation
    (handled, even if it failed), False if it's an ordinary message."""
    if not actor.is_staff:
        return False
    parsed = parse_macro(message.content)
    if parsed is None:
        return False
    name, args = parsed
    ctx = MacroContext(bot=bot, message=message, ticket=ticket, actor=actor, args=args)

    macro = find_macro(name)
    if macro is None:
        available = ", ".join(f"`?{m.name}`" for m in MACROS)
        await ctx.error(TRANSCRIPT.unknown_macro.format(name=name, available=available))
        return True
    if ticket.status == TicketStatus.CLOSED and not macro.can_run_on_closed:
        await ctx.error(TRANSCRIPT.macro_wrong_state.format(name=name))
        return True

    # Remove the "?macro" message first (resolving archives the thread)
    try:
        await message.delete()
    except discord.HTTPException as e:
        log.warning(f"Couldn't delete macro invocation (Manage Messages?): {e}")

    await macro.run(ctx)
    MACROS_RUN.labels(name=macro.name).inc()

    if macro.counts_as_reply:
        result = await tickets.record_message(ticket, actor, at=message.created_at)
        if result.claimed:
            TICKETS_CLAIMED.inc()
    return True
