from nephthys.database.raw_migration import raw_migration

ID = "2026-11-12T00:00:00:000000"
VERSION = "1.33.0"
DESCRIPTION = "Staff ticket cards, ticket categories and AI suggestions"

FORWARDS = """
ALTER TABLE ticket ADD COLUMN category TEXT;
-- 'ai' or 'staff': who set the category
ALTER TABLE ticket ADD COLUMN category_source TEXT
    CHECK (category_source IN ('ai', 'staff'));
ALTER TABLE ticket ADD COLUMN staff_card_channel_id BIGINT;
ALTER TABLE ticket ADD COLUMN staff_card_message_id BIGINT;
CREATE INDEX ticket_category_idx ON ticket (category);

CREATE TABLE ai_suggestion (
    id SERIAL PRIMARY KEY,
    ticket INTEGER NOT NULL REFERENCES ticket (id) ON DELETE CASCADE,
    mode TEXT NOT NULL CHECK (mode IN ('shadow', 'public')),
    model TEXT NOT NULL,
    answer TEXT,
    confidence DOUBLE PRECISION,
    kb_sources TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    -- Whether it passed the gates and was posted
    posted BOOLEAN NOT NULL DEFAULT FALSE,
    -- Why it wasn't posted: no_answer | low_confidence | no_sources |
    -- unknown_sources | invalid_json | error
    skip_reason TEXT,
    -- shadow: pending | helpful | not_helpful
    -- public: pending | fixed | needs_human
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

BACKWARDS = """
DROP TABLE IF EXISTS ai_suggestion;
DROP INDEX IF EXISTS ticket_category_idx;
ALTER TABLE ticket DROP COLUMN IF EXISTS staff_card_message_id;
ALTER TABLE ticket DROP COLUMN IF EXISTS staff_card_channel_id;
ALTER TABLE ticket DROP COLUMN IF EXISTS category_source;
ALTER TABLE ticket DROP COLUMN IF EXISTS category;
"""


async def forwards():
    return raw_migration(
        migration_id=ID,
        app_name="nephthys",
        description=DESCRIPTION,
        forwards=FORWARDS,
        backwards=BACKWARDS,
    )
