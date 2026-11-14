from nephthys.database.raw_migration import raw_migration

ID = "2026-11-28T00:00:00:000000"
VERSION = "1.33.0"
DESCRIPTION = "Remove CSAT (ticket.csat_rating, csat_at) and the archive wait"

FORWARDS = """
-- CSAT is gone: ratings are no longer asked for, stored or shown.
ALTER TABLE ticket DROP COLUMN IF EXISTS csat_rating;
ALTER TABLE ticket DROP COLUMN IF EXISTS csat_at;

-- Closing a #tickets thread locks and archives it straight away again, so
-- "closed" already means "archived": the column that tracked the wait for a
-- rating, and the index its sweep used, have no reader left.
DROP INDEX IF EXISTS ticket_pending_archive_idx;
ALTER TABLE ticket DROP COLUMN IF EXISTS archived_at;
"""

# The columns come back empty (dropped ratings can't be restored); archived_at
# is backfilled the way cloudsail_004 did it, so its sweep leaves old tickets
# alone.
BACKWARDS = """
ALTER TABLE ticket ADD COLUMN csat_rating SMALLINT
    CHECK (csat_rating BETWEEN 1 AND 5);
ALTER TABLE ticket ADD COLUMN csat_at TIMESTAMPTZ;
ALTER TABLE ticket ADD COLUMN archived_at TIMESTAMPTZ;

UPDATE ticket SET archived_at = closed_at
WHERE surface = 'PRIVATE' AND status = 'CLOSED';

CREATE INDEX ticket_pending_archive_idx ON ticket (closed_at)
    WHERE surface = 'PRIVATE' AND status = 'CLOSED' AND archived_at IS NULL;
"""


async def forwards():
    return raw_migration(
        migration_id=ID,
        app_name="nephthys",
        description=DESCRIPTION,
        forwards=FORWARDS,
        backwards=BACKWARDS,
    )
