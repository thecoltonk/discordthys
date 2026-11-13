"""Auto-resolve tickets with no activity for STALE_TICKET_DAYS (off by default)."""

import asyncio
import logging
from datetime import datetime
from datetime import timedelta
from datetime import UTC
from typing import TYPE_CHECKING

from nephthys.core.queries import get_stale_tickets
from nephthys.database.enums import TicketSurface
from nephthys.discord_ops import flows
from nephthys.discord_ops.heartbeat import send_heartbeat
from nephthys.errors.errors import TicketAlreadyClosedError
from nephthys.errors.errors import TicketNotFoundError

if TYPE_CHECKING:
    from nephthys.bot import SupportBot

log = logging.getLogger(__name__)

# Space out Discord calls when closing a backlog
DELAY_BETWEEN_TICKETS = 1.0


async def close_stale_tickets(
    bot: "SupportBot", now: datetime | None = None, delay: float = DELAY_BETWEEN_TICKETS
) -> int:
    days = bot.settings.stale_ticket_days
    if not days:
        return 0
    cutoff = (now or datetime.now(UTC)) - timedelta(days=days)
    # #community-help only: private tickets are closed by people (with CSAT)
    stale = [
        t
        for t in await get_stale_tickets(cutoff)
        if t.surface == TicketSurface.COMMUNITY
    ]
    closed = 0
    for ticket in stale:
        try:
            await flows.resolve_ticket(bot, ticket.id, None, None, source="stale")
            closed += 1
        except (TicketAlreadyClosedError, TicketNotFoundError):
            continue
        except Exception as e:
            log.exception(f"Failed to close stale ticket id={ticket.id}")
            await send_heartbeat(
                bot,
                f"❌ Failed to auto-close stale ticket #{ticket.id}: `{e}`",
                error=True,
            )
        if delay:
            await asyncio.sleep(delay)
    if closed:
        log.info(f"Closed {closed} stale tickets (inactive > {days} days)")
    return closed
