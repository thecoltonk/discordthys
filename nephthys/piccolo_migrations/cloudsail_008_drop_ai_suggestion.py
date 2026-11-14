from nephthys.database.raw_migration import raw_migration

ID = "2026-11-29T00:00:00:000000"
VERSION = "1.33.0"
DESCRIPTION = "Remove the generative assistant's table (ai_suggestion)"

FORWARDS = """
-- The generative answer assistant is gone (replaced by the Jev FAQ matcher,
-- which only picks pre-written answers and stores nothing). This table held
-- model-written answer text and staff ratings of it.
DROP TABLE IF EXISTS ai_suggestion;
"""

# The table comes back empty, as cloudsail_002 created it.
BACKWARDS = """
CREATE TABLE ai_suggestion (
    id SERIAL PRIMARY KEY,
    ticket INTEGER NOT NULL REFERENCES ticket (id) ON DELETE CASCADE,
    mode TEXT NOT NULL CHECK (mode IN ('shadow', 'public')),
    model TEXT NOT NULL,
    answer TEXT,
    confidence DOUBLE PRECISION,
    kb_sources TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    posted BOOLEAN NOT NULL DEFAULT FALSE,
    skip_reason TEXT,
    outcome TEXT NOT NULL DEFAULT 'pending',
    channel_id BIGINT,
    message_id BIGINT,
    rated_by INTEGER REFERENCES app_user (id) ON DELETE SET NULL,
    rated_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ai_suggestion_ticket_idx ON ai_suggestion (ticket);
CREATE INDEX ai_suggestion_created_at_idx ON ai_suggestion (created_at);
"""


async def forwards():
    return raw_migration(
        migration_id=ID,
        app_name="nephthys",
        description=DESCRIPTION,
        forwards=FORWARDS,
        backwards=BACKWARDS,
    )
