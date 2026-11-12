from nephthys.database.raw_migration import raw_migration

ID = "2026-11-14T00:00:00:000000"
VERSION = "1.33.0"
DESCRIPTION = "#tickets: archive after CSAT or timeout (ticket.archived_at)"

FORWARDS = """
-- When the bot archived a closed #tickets thread. NULL while a closed ticket
-- is still waiting for its CSAT rating (or the CSAT timeout).
ALTER TABLE ticket ADD COLUMN archived_at TIMESTAMPTZ;

-- Closed private tickets were archived immediately before this migration
UPDATE ticket SET archived_at = closed_at
WHERE surface = 'PRIVATE' AND status = 'CLOSED';

CREATE INDEX ticket_pending_archive_idx ON ticket (closed_at)
    WHERE surface = 'PRIVATE' AND status = 'CLOSED' AND archived_at IS NULL;
"""

BACKWARDS = """
DROP INDEX IF EXISTS ticket_pending_archive_idx;
ALTER TABLE ticket DROP COLUMN IF EXISTS archived_at;
"""


async def forwards():
    return raw_migration(
        migration_id=ID,
        app_name="nephthys",
        description=DESCRIPTION,
        forwards=FORWARDS,
        backwards=BACKWARDS,
    )
