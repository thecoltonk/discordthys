from datetime import datetime

from nephthys.database.enums import TicketStatus
from nephthys.database.enums import UserType
from nephthys.database.tables import Ticket


async def get_unanswered_tickets(
    last_message_before: datetime | None = None, limit: int | None = None
) -> list[Ticket]:
    """Unresolved tickets where the last message wasn't from staff, oldest first.

    This is the "waiting for staff" list: anyone can help, but a ticket only
    stops waiting once staff have the last word.
    """
    query = (
        Ticket.objects()
        .where(
            (Ticket.status != TicketStatus.CLOSED)
            & (Ticket.last_msg_by != UserType.HELPER)
        )
        .order_by(Ticket.last_msg_at)
    )
    if last_message_before is not None:
        query = query.where(Ticket.last_msg_at < last_message_before)
    if limit is not None:
        query = query.limit(limit)
    return await query


async def get_stale_tickets(inactive_since: datetime) -> list[Ticket]:
    """Unresolved tickets with no messages since `inactive_since`."""
    return await (
        Ticket.objects()
        .where(
            (Ticket.status != TicketStatus.CLOSED)
            & (Ticket.last_msg_at < inactive_since)
        )
        .order_by(Ticket.last_msg_at)
    )
