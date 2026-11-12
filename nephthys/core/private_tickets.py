"""#tickets (private tickets): numbers, category picks, first message, CSAT.

No Discord imports. Status changes (claim/resolve/reopen) reuse core.tickets.
"""

import logging
from datetime import datetime

from nephthys.core.permissions import Actor
from nephthys.core.tickets import create_ticket
from nephthys.core.tickets import fk_id
from nephthys.core.tickets import require_ticket
from nephthys.core.tickets import utcnow
from nephthys.core.users import upsert_user
from nephthys.database.enums import TicketStatus
from nephthys.database.enums import TicketSurface
from nephthys.database.tables import Ticket
from nephthys.database.tables import User

log = logging.getLogger(__name__)

SEQUENCE = "ticket_number_seq"


async def align_number_sequence(start: int) -> None:
    """Make the next T-<n> at least `start` and above every existing number.

    Safe to run on every startup; it never moves the sequence backwards.
    """
    rows = await Ticket.raw(
        "SELECT GREATEST({}::bigint, COALESCE(MAX(number), 0) + 1, "
        "(SELECT CASE WHEN is_called THEN last_value + 1 ELSE last_value END "
        "FROM " + SEQUENCE + ")) AS next FROM ticket",
        start,
    )
    target = int(rows[0]["next"])
    await Ticket.raw("SELECT setval('" + SEQUENCE + "', {}, false)", target)


async def allocate_number(start: int) -> int:
    rows = await Ticket.raw(f"SELECT nextval('{SEQUENCE}') AS n")
    number = int(rows[0]["n"])
    if number < start:
        await align_number_sequence(start)
        rows = await Ticket.raw(f"SELECT nextval('{SEQUENCE}') AS n")
        number = int(rows[0]["n"])
    return number


async def create_private_ticket(
    *,
    author: Actor,
    channel_id: int,
    thread_id: int,
    number: int,
    escalated_from: int | None = None,
    description: str = "",
    created_at: datetime | None = None,
) -> Ticket:
    ticket = await create_ticket(
        author=author,
        channel_id=channel_id,
        message_id=None,
        thread_id=thread_id,
        title=None,
        description=description,
        created_at=created_at,
        surface=TicketSurface.PRIVATE,
    )
    await Ticket.update(
        {Ticket.number: number, Ticket.escalated_from: escalated_from}
    ).where(Ticket.id == ticket.id)
    return await require_ticket(ticket.id)


async def count_open_private(discord_id: int) -> int:
    user = await User.objects().where(User.discord_id == discord_id).first()
    if not user:
        return 0
    return await Ticket.count().where(
        (Ticket.opened_by == user.id)
        & (Ticket.surface == TicketSurface.PRIVATE)
        & (Ticket.status != TicketStatus.CLOSED)
    )


async def open_private_tickets(discord_id: int) -> list[Ticket]:
    user = await User.objects().where(User.discord_id == discord_id).first()
    if not user:
        return []
    return await Ticket.objects().where(
        (Ticket.opened_by == user.id)
        & (Ticket.surface == TicketSurface.PRIVATE)
        & (Ticket.status != TicketStatus.CLOSED)
    )


async def set_opener_message(ticket_id: int, message_id: int) -> None:
    await Ticket.update({Ticket.opener_message_id: message_id}).where(
        Ticket.id == ticket_id
    )


async def pick_category(
    ticket_id: int, category: str, *, leaf_priority: str | None
) -> bool:
    """First category pick only (double clicks are ignored). For a leaf
    category, `leaf_priority` is stored at the same time."""
    values: dict = {Ticket.category: category, Ticket.category_source: "player"}
    if leaf_priority is not None:
        values[Ticket.priority] = leaf_priority
    rows = (
        await Ticket.update(values)
        .where(
            (Ticket.id == ticket_id)
            & Ticket.category.is_null()
            & (Ticket.status != TicketStatus.CLOSED)
        )
        .returning(Ticket.id)
    )
    return bool(rows)


async def pick_subcategory(
    ticket_id: int, category: str, subcategory: str, *, priority: str
) -> bool:
    rows = (
        await Ticket.update(
            {Ticket.subcategory: subcategory, Ticket.priority: priority}
        )
        .where(
            (Ticket.id == ticket_id)
            & (Ticket.category == category)
            & Ticket.subcategory.is_null()
            & (Ticket.status != TicketStatus.CLOSED)
        )
        .returning(Ticket.id)
    )
    return bool(rows)


async def set_first_message(ticket_id: int, text: str) -> bool:
    """Record the player's first message as the ticket's description/title."""
    text = text.strip()
    if not text:
        return False
    title = text.splitlines()[0][:90]
    rows = (
        await Ticket.update({Ticket.description: text, Ticket.title: title})
        .where((Ticket.id == ticket_id) & (Ticket.description == ""))
        .returning(Ticket.id)
    )
    return bool(rows)


class CSATAlreadyRatedError(Exception):
    pass


class NotTicketOpenerError(Exception):
    pass


async def rate_csat(ticket_id: int, rating: int, rater: Actor) -> Ticket:
    """Only the ticket opener may rate, and only the first rating counts."""
    if not 1 <= rating <= 5:
        raise ValueError("CSAT rating must be 1-5")
    ticket = await require_ticket(ticket_id)
    user = await upsert_user(rater)
    if user.id != fk_id(ticket.opened_by):
        raise NotTicketOpenerError(ticket_id)
    rows = (
        await Ticket.update({Ticket.csat_rating: rating, Ticket.csat_at: utcnow()})
        .where((Ticket.id == ticket_id) & Ticket.csat_rating.is_null())
        .returning(Ticket.id)
    )
    if not rows:
        raise CSATAlreadyRatedError(ticket_id)
    log.info(f"CSAT {rating}/5 for ticket id={ticket_id}")
    return await require_ticket(ticket_id)


async def escalation_of(community_ticket_id: int) -> Ticket | None:
    """The private ticket created from a community ticket, if any."""
    return (
        await Ticket.objects()
        .where(Ticket.escalated_from == community_ticket_id)
        .first()
    )


async def mark_archived(ticket_id: int) -> None:
    await Ticket.update({Ticket.archived_at: utcnow()}).where(Ticket.id == ticket_id)


async def clear_archived(ticket_id: int) -> None:
    await Ticket.update({Ticket.archived_at: None}).where(Ticket.id == ticket_id)


async def awaiting_archive(closed_before: datetime) -> list[Ticket]:
    """Closed #tickets tickets whose CSAT window has passed but that haven't
    been archived yet (the opener never rated)."""
    return await Ticket.objects().where(
        (Ticket.surface == TicketSurface.PRIVATE)
        & (Ticket.status == TicketStatus.CLOSED)
        & Ticket.archived_at.is_null()
        & (Ticket.closed_at < closed_before)
    )
