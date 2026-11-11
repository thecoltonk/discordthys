from nephthys.database.raw_migration import raw_migration

ID = "2026-11-13T00:00:00:000000"
VERSION = "1.33.0"
DESCRIPTION = (
    "#tickets: private ticket numbers, priority, subcategory, CSAT, escalation"
)

FORWARDS = """
-- Private tickets have no top-level question message
ALTER TABLE ticket ALTER COLUMN message_id DROP NOT NULL;

ALTER TABLE ticket ADD COLUMN number INTEGER UNIQUE;
ALTER TABLE ticket ADD COLUMN subcategory TEXT;
ALTER TABLE ticket ADD COLUMN priority TEXT
    CHECK (priority IN ('low', 'medium', 'high'));
-- The current opener message (deleted and reposted on each category pick)
ALTER TABLE ticket ADD COLUMN opener_message_id BIGINT;
-- A private ticket escalated from a #community-help ticket
ALTER TABLE ticket ADD COLUMN escalated_from INTEGER
    REFERENCES ticket (id) ON DELETE SET NULL;
ALTER TABLE ticket ADD COLUMN csat_rating SMALLINT
    CHECK (csat_rating BETWEEN 1 AND 5);
ALTER TABLE ticket ADD COLUMN csat_at TIMESTAMPTZ;

-- Players pick #tickets categories themselves
ALTER TABLE ticket DROP CONSTRAINT IF EXISTS ticket_category_source_check;
ALTER TABLE ticket ADD CONSTRAINT ticket_category_source_check
    CHECK (category_source IN ('ai', 'staff', 'player'));

CREATE INDEX ticket_surface_idx ON ticket (surface);
CREATE INDEX ticket_escalated_from_idx ON ticket (escalated_from);

-- T-<n> numbers. The bot aligns this with TICKET_NUMBER_START on startup.
CREATE SEQUENCE ticket_number_seq;
"""

BACKWARDS = """
DROP SEQUENCE IF EXISTS ticket_number_seq;
DROP INDEX IF EXISTS ticket_escalated_from_idx;
DROP INDEX IF EXISTS ticket_surface_idx;
ALTER TABLE ticket DROP CONSTRAINT IF EXISTS ticket_category_source_check;
ALTER TABLE ticket ADD CONSTRAINT ticket_category_source_check
    CHECK (category_source IN ('ai', 'staff'));
ALTER TABLE ticket DROP COLUMN IF EXISTS csat_at;
ALTER TABLE ticket DROP COLUMN IF EXISTS csat_rating;
ALTER TABLE ticket DROP COLUMN IF EXISTS escalated_from;
ALTER TABLE ticket DROP COLUMN IF EXISTS opener_message_id;
ALTER TABLE ticket DROP COLUMN IF EXISTS priority;
ALTER TABLE ticket DROP COLUMN IF EXISTS subcategory;
ALTER TABLE ticket DROP COLUMN IF EXISTS number;
ALTER TABLE ticket ALTER COLUMN message_id SET NOT NULL;
"""


async def forwards():
    return raw_migration(
        migration_id=ID,
        app_name="nephthys",
        description=DESCRIPTION,
        forwards=FORWARDS,
        backwards=BACKWARDS,
    )
