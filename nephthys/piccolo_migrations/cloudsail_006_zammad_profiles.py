from nephthys.database.raw_migration import raw_migration

ID = "2026-11-27T00:00:00:000000"
VERSION = "1.33.0"
DESCRIPTION = "Discord avatar and name on Zammad customers"

FORWARDS = """
-- The player's Discord avatar as last seen on one of their messages or clicks
-- (display_avatar: the server avatar if set). URLs are content-addressed CDN
-- links, so they don't expire like attachment links.
ALTER TABLE app_user
    ADD COLUMN avatar_key TEXT,
    ADD COLUMN avatar_url TEXT,
    -- What the Zammad customer shows now: the name the bot last set, and the
    -- avatar it last uploaded (and when it last tried, for the once-a-day limit)
    ADD COLUMN zammad_name TEXT,
    ADD COLUMN zammad_avatar_key TEXT,
    ADD COLUMN zammad_avatar_at TIMESTAMPTZ;
"""

BACKWARDS = """
ALTER TABLE app_user
    DROP COLUMN IF EXISTS avatar_key,
    DROP COLUMN IF EXISTS avatar_url,
    DROP COLUMN IF EXISTS zammad_name,
    DROP COLUMN IF EXISTS zammad_avatar_key,
    DROP COLUMN IF EXISTS zammad_avatar_at;
"""


async def forwards():
    return raw_migration(
        migration_id=ID,
        app_name="nephthys",
        description=DESCRIPTION,
        forwards=FORWARDS,
        backwards=BACKWARDS,
    )
