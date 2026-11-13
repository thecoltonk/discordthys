from nephthys.database.raw_migration import raw_migration

ID = "2026-11-20T00:00:00:000000"
VERSION = "1.33.0"
DESCRIPTION = "Zammad two-way sync: ids, outbox, mirrored message map"

FORWARDS = """
-- The Zammad customer that mirrors this Discord user ("discord:<id>")
ALTER TABLE app_user ADD COLUMN zammad_user_id INTEGER;

ALTER TABLE ticket
    ADD COLUMN zammad_ticket_id INTEGER UNIQUE,
    ADD COLUMN zammad_ticket_number TEXT,
    -- The Zammad group the ticket is in now (agents can move it)
    ADD COLUMN zammad_group TEXT,
    -- Claimed / closed from Zammad: there is no Discord user to point at, so
    -- the agent's Zammad display name is kept instead
    ADD COLUMN assigned_name TEXT,
    ADD COLUMN closed_by_name TEXT;

-- Work queue for both directions. Rows are processed per (ticket, direction)
-- in id order; a row that fails is retried with backoff and blocks the rows
-- behind it, so messages never overtake each other.
CREATE TABLE zammad_outbox (
    id              SERIAL PRIMARY KEY,
    ticket          INTEGER NOT NULL REFERENCES ticket(id) ON DELETE CASCADE,
    direction       TEXT NOT NULL CHECK (direction IN ('to_zammad', 'to_discord')),
    kind            TEXT NOT NULL,
    payload         JSONB NOT NULL DEFAULT jsonb_build_object(),
    -- Makes enqueueing idempotent (a Discord message id, a Zammad article id...)
    dedupe_key      TEXT UNIQUE,
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'done', 'failed')),
    attempts        INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_error      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    done_at         TIMESTAMPTZ
);
CREATE INDEX zammad_outbox_pending_idx ON zammad_outbox (ticket, direction, id)
    WHERE status <> 'done';

-- Which Discord message is which Zammad article. `origin` is where the message
-- was written; rows with origin = 'zammad' are the bot's own posts in the
-- thread, which must never be mirrored back.
CREATE TABLE zammad_message_map (
    id                 SERIAL PRIMARY KEY,
    ticket             INTEGER NOT NULL REFERENCES ticket(id) ON DELETE CASCADE,
    origin             TEXT NOT NULL CHECK (origin IN ('discord', 'zammad')),
    discord_message_id BIGINT UNIQUE,
    zammad_article_id  INTEGER,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- One Discord message per article written in Discord; an article written in
-- Zammad may span several Discord messages (2000 character limit).
CREATE UNIQUE INDEX zammad_message_map_article_idx
    ON zammad_message_map (zammad_article_id) WHERE origin = 'discord';
CREATE INDEX zammad_message_map_zammad_article_idx
    ON zammad_message_map (zammad_article_id) WHERE origin = 'zammad';
"""

BACKWARDS = """
DROP TABLE IF EXISTS zammad_message_map;
DROP TABLE IF EXISTS zammad_outbox;
ALTER TABLE ticket
    DROP COLUMN IF EXISTS zammad_ticket_id,
    DROP COLUMN IF EXISTS zammad_ticket_number,
    DROP COLUMN IF EXISTS zammad_group,
    DROP COLUMN IF EXISTS assigned_name,
    DROP COLUMN IF EXISTS closed_by_name;
ALTER TABLE app_user DROP COLUMN IF EXISTS zammad_user_id;
"""


async def forwards():
    return raw_migration(
        migration_id=ID,
        app_name="nephthys",
        description=DESCRIPTION,
        forwards=FORWARDS,
        backwards=BACKWARDS,
    )
