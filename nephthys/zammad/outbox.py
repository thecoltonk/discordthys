"""The sync queue (table `zammad_outbox`) and the mirrored-message map.

No Discord or HTTP imports: everything here is plain database work, so Discord
flows can enqueue without ever waiting on Zammad.

Ordering: rows are worked per (ticket, direction), oldest first, one at a time.
A row that fails is retried with exponential backoff and holds back the rows
behind it; only a row that is given up on (`failed`) is stepped over. For
`to_zammad`, the `create_ticket` row always goes first, and nothing else for
that ticket runs until the Zammad ticket exists.
"""

import json
import logging
import random
from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from datetime import UTC
from typing import Any

from nephthys.database.tables import Ticket
from nephthys.database.tables import ZammadMessageMap
from nephthys.database.tables import ZammadOutbox

log = logging.getLogger(__name__)

TO_ZAMMAD = "to_zammad"
TO_DISCORD = "to_discord"

# to_zammad
CREATE_TICKET = "create_ticket"
ARTICLE = "article"
CLOSE = "close"
# No longer queued (private tickets can't be re-opened; CSAT notes are gone).
# Rows of these kinds left over from before the upgrade are skipped.
REOPEN = "reopen"
NOTE = "note"
# to_discord
POST_ARTICLE = "post_article"
SYNC_TICKET = "sync_ticket"

PENDING = "pending"
DONE = "done"
FAILED = "failed"

BACKOFF_BASE_SECONDS = 5.0
BACKOFF_MAX_SECONDS = 900.0
# Attempts allowed for errors that retrying can't fix (e.g. HTTP 422)
PERMANENT_ATTEMPTS = 3
ERROR_MAX = 500

ORIGIN_DISCORD = "discord"
ORIGIN_ZAMMAD = "zammad"


def utcnow() -> datetime:
    return datetime.now(UTC)


def backoff_seconds(attempts: int, *, jitter: bool = True) -> float:
    """5s, 10s, 20s ... capped at 15 minutes (with up to 20% jitter)."""
    delay = min(BACKOFF_BASE_SECONDS * (2 ** max(0, attempts - 1)), BACKOFF_MAX_SECONDS)
    return delay * (1 + random.uniform(0, 0.2)) if jitter else delay


def _payload(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        return json.loads(value)
    return dict(value or {})


@dataclass
class Row:
    id: int
    ticket_id: int
    direction: str
    kind: str
    payload: dict[str, Any]
    attempts: int
    created_at: datetime


async def enqueue(
    ticket_id: int,
    direction: str,
    kind: str,
    payload: dict[str, Any] | None = None,
    *,
    dedupe_key: str | None = None,
) -> bool:
    """Queue work. Returns False when `dedupe_key` was already queued (at any
    time, in any state), which is what makes re-deliveries harmless."""
    rows = await ZammadOutbox.raw(
        "INSERT INTO zammad_outbox (ticket, direction, kind, payload, dedupe_key) "
        "VALUES ({}, {}, {}, {}::jsonb, {}) "
        "ON CONFLICT (dedupe_key) DO NOTHING RETURNING id",
        ticket_id,
        direction,
        kind,
        json.dumps(payload or {}),
        dedupe_key,
    )
    return bool(rows)


async def has_pending(ticket_id: int, direction: str, kinds: tuple[str, ...]) -> bool:
    return await ZammadOutbox.exists().where(
        (ZammadOutbox.ticket == ticket_id)
        & (ZammadOutbox.direction == direction)
        & (ZammadOutbox.status == PENDING)
        & ZammadOutbox.kind.is_in(list(kinds))
    )


async def due_rows(now: datetime | None = None) -> list[Row]:
    """The next row of every (ticket, direction) queue that is ready to run."""
    rows = await ZammadOutbox.raw(
        """
        SELECT * FROM (
            SELECT DISTINCT ON (o.ticket, o.direction)
                   o.id, o.ticket, o.direction, o.kind, o.payload, o.attempts,
                   o.created_at, o.next_attempt_at, t.zammad_ticket_id
            FROM zammad_outbox o JOIN ticket t ON t.id = o.ticket
            WHERE o.status = 'pending'
            ORDER BY o.ticket, o.direction, (o.kind = 'create_ticket') DESC, o.id
        ) head
        WHERE head.next_attempt_at <= {}
          AND (head.direction = 'to_discord' OR head.kind = 'create_ticket'
               OR head.zammad_ticket_id IS NOT NULL)
        ORDER BY head.id
        """,
        now or utcnow(),
    )
    return [
        Row(
            id=r["id"],
            ticket_id=r["ticket"],
            direction=r["direction"],
            kind=r["kind"],
            payload=_payload(r["payload"]),
            attempts=r["attempts"],
            created_at=r["created_at"],
        )
        for r in rows
    ]


async def mark_done(row_id: int) -> None:
    await ZammadOutbox.update(
        {
            ZammadOutbox.status: DONE,
            ZammadOutbox.done_at: utcnow(),
            ZammadOutbox.last_error: None,
        }
    ).where(ZammadOutbox.id == row_id)


async def save_payload(row_id: int, payload: dict[str, Any]) -> None:
    """Persist progress inside a row (e.g. how many chunks were posted)."""
    await ZammadOutbox.raw(
        "UPDATE zammad_outbox SET payload = {}::jsonb WHERE id = {}",
        json.dumps(payload),
        row_id,
    )


async def mark_error(
    row: Row, error: str, *, permanent: bool, max_attempts: int
) -> bool:
    """Record a failed attempt. Returns True when the row was given up on."""
    attempts = row.attempts + 1
    limit = min(max_attempts, PERMANENT_ATTEMPTS) if permanent else max_attempts
    gave_up = attempts >= limit
    await ZammadOutbox.update(
        {
            ZammadOutbox.attempts: attempts,
            ZammadOutbox.status: FAILED if gave_up else PENDING,
            ZammadOutbox.last_error: error[:ERROR_MAX],
            ZammadOutbox.next_attempt_at: utcnow()
            + timedelta(seconds=backoff_seconds(attempts)),
        }
    ).where(ZammadOutbox.id == row.id)
    return gave_up


async def retry_failed() -> int:
    """Put every given-up row back in the queue (`nephthys zammad-retry`)."""
    rows = (
        await ZammadOutbox.update(
            {
                ZammadOutbox.status: PENDING,
                ZammadOutbox.attempts: 0,
                ZammadOutbox.next_attempt_at: utcnow(),
            }
        )
        .where(ZammadOutbox.status == FAILED)
        .returning(ZammadOutbox.id)
    )
    return len(rows)


async def drop_unstarted(ticket_id: int) -> None:
    """Forget queued messages of a ticket that never reached Zammad (closed
    before a category was picked)."""
    await ZammadOutbox.raw(
        "DELETE FROM zammad_outbox o USING ticket t WHERE t.id = o.ticket "
        "AND o.ticket = {} AND o.direction = 'to_zammad' AND o.status = 'pending' "
        "AND t.zammad_ticket_id IS NULL AND NOT EXISTS (SELECT 1 FROM zammad_outbox c "
        "WHERE c.ticket = o.ticket AND c.kind = 'create_ticket')",
        ticket_id,
    )


@dataclass(frozen=True)
class OutboxStats:
    pending: int = 0
    failed: int = 0
    # Age of the oldest row that should already have been delivered
    oldest_pending_seconds: float | None = None
    last_error: str | None = None

    @property
    def healthy(self) -> bool:
        return self.pending == 0 and self.failed == 0


async def stats(now: datetime | None = None) -> OutboxStats:
    """Backlog and failures. Messages typed before a category was picked wait
    for the Zammad ticket by design, so they aren't counted as backlog."""
    now = now or utcnow()
    rows = await ZammadOutbox.raw(
        """
        SELECT
            count(*) FILTER (WHERE o.status = 'pending') AS pending,
            count(*) FILTER (WHERE o.status = 'failed') AS failed,
            min(o.created_at) FILTER (WHERE o.status = 'pending') AS oldest,
            (SELECT e.last_error FROM zammad_outbox e
              WHERE e.status <> 'done' AND e.last_error IS NOT NULL
              ORDER BY e.id DESC LIMIT 1) AS last_error
        FROM zammad_outbox o JOIN ticket t ON t.id = o.ticket
        WHERE o.status <> 'done'
          AND (o.direction = 'to_discord' OR t.zammad_ticket_id IS NOT NULL
               OR EXISTS (SELECT 1 FROM zammad_outbox c
                           WHERE c.ticket = o.ticket AND c.kind = 'create_ticket'))
        """
    )
    r = rows[0]
    oldest = r["oldest"]
    return OutboxStats(
        pending=int(r["pending"] or 0),
        failed=int(r["failed"] or 0),
        oldest_pending_seconds=(now - oldest).total_seconds() if oldest else None,
        last_error=r["last_error"],
    )


# ---------------------------------------------------------------------------
# Mirrored messages
# ---------------------------------------------------------------------------


async def record_mirror(
    ticket_id: int,
    *,
    origin: str,
    discord_message_id: int | None,
    zammad_article_id: int | None,
) -> None:
    await ZammadMessageMap.raw(
        "INSERT INTO zammad_message_map (ticket, origin, discord_message_id, "
        "zammad_article_id) VALUES ({}, {}, {}, {}) ON CONFLICT DO NOTHING",
        ticket_id,
        origin,
        discord_message_id,
        zammad_article_id,
    )


async def article_for_discord_message(message_id: int) -> ZammadMessageMap | None:
    return (
        await ZammadMessageMap.objects()
        .where(ZammadMessageMap.discord_message_id == message_id)
        .first()
    )


async def is_known_article(article_id: int) -> bool:
    """True for articles the bot created (origin discord) or already posted
    to Discord (origin zammad)."""
    return await ZammadMessageMap.exists().where(
        ZammadMessageMap.zammad_article_id == article_id
    )


async def synced_tickets(closed_after: datetime, limit: int = 300) -> list[Ticket]:
    """Tickets worth reconciling: mirrored, and open or recently closed."""
    from nephthys.database.enums import TicketStatus

    return (
        await Ticket.objects()
        .where(
            Ticket.zammad_ticket_id.is_not_null()
            & (
                (Ticket.status != TicketStatus.CLOSED)
                | (Ticket.closed_at > closed_after)
            )
        )
        .order_by(Ticket.id, ascending=False)
        .limit(limit)
    )
