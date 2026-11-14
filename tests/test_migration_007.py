"""cloudsail_007: CSAT and the archive wait are dropped; tested both ways."""

from datetime import datetime
from datetime import timedelta
from datetime import UTC

import pytest

from nephthys.core import private_tickets as pt
from nephthys.core import tickets
from nephthys.database.raw_migration import postgresql_code_block
from nephthys.database.tables import Ticket
from nephthys.piccolo_migrations import cloudsail_007_drop_csat as m007
from tests.factories import new_ticket
from tests.factories import player
from tests.factories import snowflake

pytestmark = pytest.mark.usefixtures("db")

DROPPED = {"csat_rating", "csat_at", "archived_at"}
INDEX = "ticket_pending_archive_idx"


async def columns(table: str) -> set[str]:
    rows = await Ticket.raw(
        "SELECT column_name FROM information_schema.columns WHERE table_name = {}",
        table,
    )
    return {r["column_name"] for r in rows}


async def has_index(name: str) -> bool:
    rows = await Ticket.raw("SELECT 1 FROM pg_indexes WHERE indexname = {}", name)
    return bool(rows)


async def run(sql: str) -> None:
    await Ticket.raw(postgresql_code_block(sql))


async def test_the_migrated_schema_has_no_csat_or_archive_columns():
    assert not DROPPED & await columns("ticket")
    assert not await has_index(INDEX)
    # The table class matches: nothing left that would select a dropped column
    assert not DROPPED & {c._meta.name for c in Ticket._meta.columns}


async def test_migration_007_round_trip_keeps_tickets():
    op = player()
    closed = await pt.create_private_ticket(
        author=op, channel_id=600, thread_id=snowflake(), number=26
    )
    await tickets.resolve(closed.id, op)
    still_open = await pt.create_private_ticket(
        author=player(), channel_id=600, thread_id=snowflake(), number=27
    )
    community = await new_ticket()
    await tickets.resolve(community.id, None)
    before = await Ticket.raw("SELECT * FROM ticket ORDER BY id")
    try:
        # Backwards: the columns and the sweep's index come back
        await run(m007.BACKWARDS)
        assert DROPPED <= await columns("ticket")
        assert await has_index(INDEX)
        rows = {
            r["id"]: r
            for r in await Ticket.raw(
                "SELECT id, closed_at, csat_rating, csat_at, archived_at FROM ticket"
            )
        }
        assert set(rows) == {closed.id, still_open.id, community.id}
        # Ratings can't be restored; a closed private ticket counts as archived
        # (so the old sweep wouldn't touch it), nothing else does
        assert all(
            r["csat_rating"] is None and r["csat_at"] is None for r in rows.values()
        )
        assert rows[closed.id]["archived_at"] == rows[closed.id]["closed_at"]
        assert rows[still_open.id]["archived_at"] is None
        assert rows[community.id]["archived_at"] is None
        # The old CHECK is back too
        with pytest.raises(Exception, match="csat_rating"):
            await Ticket.raw(
                "UPDATE ticket SET csat_rating = 6 WHERE id = {}", closed.id
            )

        # Forwards, with data in the columns it drops
        await Ticket.raw(
            "UPDATE ticket SET csat_rating = 4, csat_at = {} WHERE id = {}",
            datetime.now(UTC) - timedelta(hours=1),
            closed.id,
        )
        await run(m007.FORWARDS)
        assert not DROPPED & await columns("ticket")
        assert not await has_index(INDEX)
        await run(m007.FORWARDS)  # safe to run on a schema that's already there
    finally:
        await run(m007.FORWARDS)
    assert await Ticket.raw("SELECT * FROM ticket ORDER BY id") == before
    # ...and the bot still reads and writes tickets on the result
    refreshed = await tickets.require_ticket(closed.id)
    assert refreshed.number == 26 and refreshed.closed_at is not None
