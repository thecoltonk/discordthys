from nephthys.database.raw_migration import raw_migration

ID = "2026-11-11T00:00:00:000000"
VERSION = "1.33.0"
DESCRIPTION = "Cloudsail Support initial schema (Discord)"

FORWARDS = """
CREATE TYPE ticket_status AS ENUM ('OPEN', 'IN_PROGRESS', 'CLOSED');
CREATE TYPE user_type AS ENUM ('AUTHOR', 'HELPER', 'OTHER');
CREATE TYPE ticket_surface AS ENUM ('COMMUNITY', 'PRIVATE');
CREATE TYPE bot_message_kind AS ENUM ('AUTO_REPLY', 'RESOLVED', 'REOPENED', 'MACRO');

CREATE TABLE app_user (
    id SERIAL PRIMARY KEY,
    discord_id BIGINT NOT NULL UNIQUE,
    username TEXT,
    helper BOOLEAN NOT NULL DEFAULT FALSE,
    admin BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE ticket (
    id SERIAL PRIMARY KEY,
    surface ticket_surface NOT NULL DEFAULT 'COMMUNITY',
    channel_id BIGINT NOT NULL,
    message_id BIGINT NOT NULL UNIQUE,
    thread_id BIGINT NOT NULL UNIQUE,
    title TEXT,
    description TEXT NOT NULL DEFAULT '',
    status ticket_status NOT NULL DEFAULT 'OPEN',
    last_msg_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_msg_by user_type NOT NULL DEFAULT 'AUTHOR',
    opened_by INTEGER NOT NULL REFERENCES app_user (id) ON DELETE RESTRICT ON UPDATE CASCADE,
    assigned_to INTEGER REFERENCES app_user (id) ON DELETE SET NULL ON UPDATE CASCADE,
    assigned_at TIMESTAMPTZ,
    closed_by INTEGER REFERENCES app_user (id) ON DELETE SET NULL ON UPDATE CASCADE,
    closed_at TIMESTAMPTZ,
    reopened_by INTEGER REFERENCES app_user (id) ON DELETE SET NULL ON UPDATE CASCADE,
    reopened_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ticket_status_idx ON ticket (status);
CREATE INDEX ticket_created_at_idx ON ticket (created_at);
CREATE INDEX ticket_assigned_to_idx ON ticket (assigned_to);

CREATE TABLE bot_message (
    id SERIAL PRIMARY KEY,
    ticket INTEGER NOT NULL REFERENCES ticket (id) ON DELETE CASCADE,
    channel_id BIGINT NOT NULL,
    message_id BIGINT NOT NULL,
    kind bot_message_kind NOT NULL DEFAULT 'AUTO_REPLY',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (channel_id, message_id)
);
CREATE INDEX bot_message_ticket_idx ON bot_message (ticket);

CREATE TABLE sticky_message (
    channel_id BIGINT PRIMARY KEY,
    message_id BIGINT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

BACKWARDS = """
DROP TABLE IF EXISTS sticky_message;
DROP TABLE IF EXISTS bot_message;
DROP TABLE IF EXISTS ticket;
DROP TABLE IF EXISTS app_user;
DROP TYPE IF EXISTS bot_message_kind;
DROP TYPE IF EXISTS ticket_surface;
DROP TYPE IF EXISTS user_type;
DROP TYPE IF EXISTS ticket_status;
"""


async def forwards():
    return raw_migration(
        migration_id=ID,
        app_name="nephthys",
        description=DESCRIPTION,
        forwards=FORWARDS,
        backwards=BACKWARDS,
    )
