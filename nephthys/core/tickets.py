"""The ticket state machine. No Discord imports: callers do the side effects.

Lifecycle (community surface):

    create_ticket ──► OPEN ──(first staff reply)──► IN_PROGRESS
                       │                              │
                       └──────────── resolve ─────────┤
                                                      ▼
                                                    CLOSED ──reopen──► OPEN / IN_PROGRESS

Credit for a resolve always goes to the claimer (`assigned_to`); a ticket that
was never claimed by staff earns no credit.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from datetime import UTC

from nephthys.core.permissions import Actor
from nephthys.core.permissions import can_resolve
from nephthys.core.users import get_user
from nephthys.core.users import upsert_user
from nephthys.database.enums import BotMessageKind
from nephthys.database.enums import TicketStatus
from nephthys.database.enums import TicketSurface
from nephthys.database.enums import UserType
from nephthys.database.tables import BotMessage
from nephthys.database.tables import Ticket
from nephthys.database.tables import User
from nephthys.errors.errors import PermissionDenied
from nephthys.errors.errors import PrivateTicketReopenError
from nephthys.errors.errors import TicketAlreadyClosedError
from nephthys.errors.errors import TicketNotClosedError
from nephthys.errors.errors import TicketNotFoundError

log = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------


async def get_ticket(ticket_id: int) -> Ticket | None:
    return await Ticket.objects().where(Ticket.id == ticket_id).first()


async def require_ticket(ticket_id: int) -> Ticket:
    ticket = await get_ticket(ticket_id)
    if not ticket:
        raise TicketNotFoundError(ticket_id)
    return ticket


async def get_ticket_by_thread(thread_id: int) -> Ticket | None:
    return await Ticket.objects().where(Ticket.thread_id == thread_id).first()


async def get_ticket_by_message(message_id: int) -> Ticket | None:
    return await Ticket.objects().where(Ticket.message_id == message_id).first()


async def get_opener(ticket: Ticket) -> User:
    opener = await get_user(fk_id(ticket.opened_by))
    if not opener:
        raise ValueError(f"Ticket {ticket.id} has no recorded opener")
    return opener


def fk_id(value: object) -> int:
    """Piccolo returns a raw int for non-prefetched foreign keys; normalise it."""
    if isinstance(value, int):
        return value
    inner = getattr(value, "id", None)
    if isinstance(inner, int):
        return inner
    raise TypeError(f"Unexpected foreign key value: {value!r}")


def fk_id_or_none(value: object) -> int | None:
    return None if value is None else fk_id(value)


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------


async def create_ticket(
    *,
    author: Actor,
    channel_id: int,
    message_id: int | None,
    thread_id: int,
    title: str | None,
    description: str,
    created_at: datetime | None = None,
    surface: TicketSurface = TicketSurface.COMMUNITY,
) -> Ticket:
    user = await upsert_user(author)
    now = created_at or utcnow()
    ticket = Ticket(
        surface=surface,
        channel_id=channel_id,
        message_id=message_id,
        thread_id=thread_id,
        title=title,
        description=description,
        status=TicketStatus.OPEN,
        last_msg_at=now,
        last_msg_by=UserType.AUTHOR,
        opened_by=user.id,
        created_at=now,
    )
    await ticket.save()
    log.info(f"Created ticket id={ticket.id} thread_id={thread_id} by={user.id}")
    return ticket


async def count_tickets_opened_by(discord_id: int) -> int:
    user = await User.objects().where(User.discord_id == discord_id).first()
    if not user:
        return 0
    return await Ticket.count().where(Ticket.opened_by == user.id)


# ---------------------------------------------------------------------------
# Messages in a ticket thread
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MessageResult:
    ticket: Ticket
    # This message claimed the ticket (first staff reply)
    claimed: bool
    # The original poster replied to a resolved ticket; caller should reopen
    should_reopen: bool


async def record_message(
    ticket: Ticket, author: Actor, at: datetime | None = None
) -> MessageResult:
    """Record a human message in a ticket thread.

    - Updates `last_msg_at` / `last_msg_by`.
    - The first staff reply (from someone other than the poster) claims the
      ticket. The claim is atomic, so two simultaneous replies can't both claim.
    - Flags `should_reopen` when the poster replies to a resolved ticket.
    """
    at = at or utcnow()
    user = await upsert_user(author)
    is_author = user.id == fk_id(ticket.opened_by)
    last_msg_by = (
        UserType.AUTHOR
        if is_author
        else UserType.HELPER
        if author.is_staff
        else UserType.OTHER
    )
    await Ticket.update(
        {Ticket.last_msg_at: at, Ticket.last_msg_by: last_msg_by}
    ).where(Ticket.id == ticket.id)

    claimed = False
    if author.is_staff and not is_author and ticket.status != TicketStatus.CLOSED:
        claimed = await claim(ticket.id, user.id, at)

    refreshed = await require_ticket(ticket.id)
    return MessageResult(
        ticket=refreshed,
        claimed=claimed,
        should_reopen=is_author and refreshed.status == TicketStatus.CLOSED,
    )


async def claim(ticket_id: int, user_id: int, at: datetime | None = None) -> bool:
    """Claim an unclaimed, unresolved ticket. Returns True if this call claimed it."""
    rows = (
        await Ticket.update(
            {
                Ticket.assigned_to: user_id,
                Ticket.assigned_at: at or utcnow(),
                Ticket.status: TicketStatus.IN_PROGRESS,
            }
        )
        .where(
            (Ticket.id == ticket_id)
            & Ticket.assigned_to.is_null()
            & Ticket.assigned_name.is_null()
            & (Ticket.status != TicketStatus.CLOSED)
        )
        .returning(Ticket.id)
    )
    if rows:
        log.info(f"Ticket id={ticket_id} claimed by user_id={user_id}")
    return bool(rows)


async def claim_by_name(ticket_id: int, name: str, at: datetime | None = None) -> bool:
    """Claim for someone with no Discord account here: a Zammad agent whose
    first public reply counts as the staff claim. Same rules as `claim`."""
    rows = (
        await Ticket.update(
            {
                Ticket.assigned_name: name,
                Ticket.assigned_at: at or utcnow(),
                Ticket.status: TicketStatus.IN_PROGRESS,
            }
        )
        .where(
            (Ticket.id == ticket_id)
            & Ticket.assigned_to.is_null()
            & Ticket.assigned_name.is_null()
            & (Ticket.status != TicketStatus.CLOSED)
        )
        .returning(Ticket.id)
    )
    if rows:
        log.info(f"Ticket id={ticket_id} claimed from Zammad by {name!r}")
    return bool(rows)


async def record_external_staff_message(
    ticket_id: int, at: datetime | None = None
) -> None:
    """A staff reply that didn't come from a Discord user (a Zammad agent):
    the ticket is no longer waiting on staff."""
    await Ticket.update(
        {Ticket.last_msg_at: at or utcnow(), Ticket.last_msg_by: UserType.HELPER}
    ).where(Ticket.id == ticket_id)


# ---------------------------------------------------------------------------
# Resolve / reopen
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolveResult:
    ticket: Ticket
    # Who resolved it (None for automatic resolves, e.g. stale)
    resolved_by: User | None
    # Who gets credit: always the claimer, or nobody
    credited_to: User | None


async def resolve(
    ticket_id: int,
    resolver: Actor | None,
    at: datetime | None = None,
    *,
    by_name: str | None = None,
) -> ResolveResult:
    """Mark a ticket as resolved.

    `resolver=None` means the bot is resolving it (e.g. the stale task), which
    skips the permission check. `by_name` records who did it when that isn't a
    Discord user (a Zammad agent).

    Raises `PermissionDenied`, `TicketAlreadyClosedError` or `TicketNotFoundError`.
    """
    ticket = await require_ticket(ticket_id)
    resolver_user: User | None = None
    if resolver is not None:
        opener = await get_opener(ticket)
        if not can_resolve(resolver, opener.discord_id):
            raise PermissionDenied(
                "Only the person who asked or staff can resolve this ticket",
                user_id=resolver.discord_id,
            )
        resolver_user = await upsert_user(resolver)

    rows = (
        await Ticket.update(
            {
                Ticket.status: TicketStatus.CLOSED,
                Ticket.closed_by: resolver_user.id if resolver_user else None,
                Ticket.closed_by_name: by_name if resolver_user is None else None,
                Ticket.closed_at: at or utcnow(),
            }
        )
        .where((Ticket.id == ticket_id) & (Ticket.status != TicketStatus.CLOSED))
        .returning(Ticket.id)
    )
    if not rows:
        raise TicketAlreadyClosedError(ticket_id)

    ticket = await require_ticket(ticket_id)
    assigned = fk_id_or_none(ticket.assigned_to)
    credited = await get_user(assigned) if assigned is not None else None
    log.info(
        f"Resolved ticket id={ticket_id} by={resolver_user.id if resolver_user else 'bot'} "
        f"credit={credited.id if credited else None}"
    )
    return ResolveResult(ticket=ticket, resolved_by=resolver_user, credited_to=credited)


async def reopen(ticket_id: int, actor: Actor, at: datetime | None = None) -> Ticket:
    """Re-open a resolved #community-help ticket. The claim (if any) is kept.

    Private (#tickets) tickets can't be re-opened by anyone, from Discord or
    from Zammad: a closed one stays closed and the player opens a new ticket.

    Raises `PrivateTicketReopenError`, `PermissionDenied`,
    `TicketNotClosedError` or `TicketNotFoundError`.
    """
    ticket = await require_ticket(ticket_id)
    if ticket.surface == TicketSurface.PRIVATE:
        raise PrivateTicketReopenError(ticket_id)
    opener = await get_opener(ticket)
    if not can_resolve(actor, opener.discord_id):
        raise PermissionDenied(
            "Only the person who asked or staff can re-open this ticket",
            user_id=actor.discord_id,
        )
    user = await upsert_user(actor)
    new_status = (
        TicketStatus.IN_PROGRESS
        if ticket.assigned_to is not None or ticket.assigned_name is not None
        else TicketStatus.OPEN
    )
    rows = (
        await Ticket.update(
            {
                Ticket.status: new_status,
                Ticket.closed_by: None,
                Ticket.closed_by_name: None,
                Ticket.closed_at: None,
                Ticket.reopened_by: user.id,
                Ticket.reopened_at: at or utcnow(),
            }
        )
        .where((Ticket.id == ticket_id) & (Ticket.status == TicketStatus.CLOSED))
        .returning(Ticket.id)
    )
    if not rows:
        raise TicketNotClosedError(ticket_id)
    log.info(f"Reopened ticket id={ticket_id} by user_id={user.id}")
    return await require_ticket(ticket_id)


async def delete_ticket(ticket_id: int) -> None:
    await Ticket.delete().where(Ticket.id == ticket_id)
    log.info(f"Deleted ticket id={ticket_id}")


# ---------------------------------------------------------------------------
# Bot messages
# ---------------------------------------------------------------------------


async def record_bot_message(
    ticket_id: int, channel_id: int, message_id: int, kind: BotMessageKind
) -> None:
    await BotMessage.insert(
        BotMessage(
            ticket=ticket_id, channel_id=channel_id, message_id=message_id, kind=kind
        )
    ).on_conflict(action="DO NOTHING")


async def get_bot_messages(
    ticket_id: int, kind: BotMessageKind | None = None
) -> list[BotMessage]:
    query = BotMessage.objects().where(BotMessage.ticket == ticket_id)
    if kind is not None:
        query = query.where(BotMessage.kind == kind)
    return await query.order_by(BotMessage.id)


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------


class InvalidCategoryError(ValueError):
    pass


async def set_category(
    ticket_id: int, category: str, *, source: str, allowed: tuple[str, ...]
) -> Ticket:
    """Set a ticket's category (staff choice always wins)."""
    if category not in allowed:
        raise InvalidCategoryError(category)
    await Ticket.update(
        {Ticket.category: category, Ticket.category_source: source}
    ).where(Ticket.id == ticket_id)
    return await require_ticket(ticket_id)


async def set_ai_category(
    ticket_id: int, category: str, *, allowed: tuple[str, ...]
) -> bool:
    """Apply an AI-suggested category only if nobody has categorised it yet."""
    if category not in allowed:
        raise InvalidCategoryError(category)
    rows = (
        await Ticket.update({Ticket.category: category, Ticket.category_source: "ai"})
        .where((Ticket.id == ticket_id) & Ticket.category.is_null())
        .returning(Ticket.id)
    )
    return bool(rows)


# ---------------------------------------------------------------------------
# Staff cards
# ---------------------------------------------------------------------------


async def set_staff_card(ticket_id: int, channel_id: int, message_id: int) -> None:
    await Ticket.update(
        {
            Ticket.staff_card_channel_id: channel_id,
            Ticket.staff_card_message_id: message_id,
        }
    ).where(Ticket.id == ticket_id)


async def count_past_tickets(ticket: Ticket) -> int:
    """How many tickets the same person opened before this one."""
    return await Ticket.count().where(
        (Ticket.opened_by == fk_id(ticket.opened_by)) & (Ticket.id < ticket.id)
    )
