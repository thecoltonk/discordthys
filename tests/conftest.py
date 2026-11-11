import os

# piccolo_conf reads DATABASE_URL at import time, so point it at the test DB
# before anything imports it.
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://cs:cs@localhost:5432/cs_test"
)
os.environ["DATABASE_URL"] = TEST_DATABASE_URL

import pytest


@pytest.fixture(scope="session")
async def migrated_db():
    """Rebuild the test schema from the init migration once per session."""
    from nephthys.database.raw_migration import postgresql_code_block
    from nephthys.database.tables import Ticket
    from nephthys.piccolo_migrations import cloudsail_001_init
    from nephthys.piccolo_migrations import cloudsail_002_cards_categories_ai

    await Ticket.raw("DROP SCHEMA IF EXISTS public CASCADE")
    await Ticket.raw("CREATE SCHEMA public")
    for migration in (cloudsail_001_init, cloudsail_002_cards_categories_ai):
        await Ticket.raw(postgresql_code_block(migration.FORWARDS))
    yield


@pytest.fixture
async def db(migrated_db):
    """Give each test empty tables."""
    from nephthys.database.tables import Ticket

    await Ticket.raw(
        "TRUNCATE app_user, ticket, bot_message, sticky_message, ai_suggestion "
        "RESTART IDENTITY CASCADE"
    )
    yield
