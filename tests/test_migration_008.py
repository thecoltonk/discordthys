"""cloudsail_008: the generative assistant's table is dropped; tested both ways."""

import pytest

from nephthys.database.raw_migration import postgresql_code_block
from nephthys.database.tables import Ticket
from nephthys.piccolo_migrations import cloudsail_008_drop_ai_suggestion as m008
from tests.factories import new_ticket

pytestmark = pytest.mark.usefixtures("db")


async def tables() -> set[str]:
    rows = await Ticket.raw(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
    )
    return {r["table_name"] for r in rows}


async def run(sql: str) -> None:
    await Ticket.raw(postgresql_code_block(sql))


async def test_migration_008_round_trip():
    assert "ai_suggestion" not in await tables()
    ticket = await new_ticket()
    try:
        # Backwards: the table is back as cloudsail_002 made it, and usable
        await run(m008.BACKWARDS)
        assert "ai_suggestion" in await tables()
        await Ticket.raw(
            "INSERT INTO ai_suggestion (ticket, mode, model, answer, confidence, "
            "kb_sources, posted) VALUES ({}, 'shadow', 'm', 'old answer', 0.9, "
            "ARRAY['faq.md'], true)",
            ticket.id,
        )
        [row] = await Ticket.raw("SELECT outcome, rated_by FROM ai_suggestion")
        assert row == {"outcome": "pending", "rated_by": None}
        with pytest.raises(Exception, match="mode"):
            await Ticket.raw(
                "INSERT INTO ai_suggestion (ticket, mode, model) VALUES ({}, 'x', 'm')",
                ticket.id,
            )
        # Forwards, with rows in it
        await run(m008.FORWARDS)
        assert "ai_suggestion" not in await tables()
        await run(m008.FORWARDS)  # safe on a schema that's already there
    finally:
        await run(m008.FORWARDS)
    # Tickets are untouched
    rows = await Ticket.raw("SELECT id FROM ticket")
    assert [r["id"] for r in rows] == [ticket.id]
