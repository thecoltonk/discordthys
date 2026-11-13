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
    from nephthys.piccolo_migrations import cloudsail_003_private_tickets
    from nephthys.piccolo_migrations import cloudsail_004_ticket_archived_at
    from nephthys.piccolo_migrations import cloudsail_005_zammad

    await Ticket.raw("DROP SCHEMA IF EXISTS public CASCADE")
    await Ticket.raw("CREATE SCHEMA public")
    for migration in (
        cloudsail_001_init,
        cloudsail_002_cards_categories_ai,
        cloudsail_003_private_tickets,
        cloudsail_004_ticket_archived_at,
        cloudsail_005_zammad,
    ):
        await Ticket.raw(postgresql_code_block(migration.FORWARDS))
    yield


@pytest.fixture
async def db(migrated_db):
    """Give each test empty tables."""
    from nephthys.database.tables import Ticket

    await Ticket.raw(
        "TRUNCATE app_user, ticket, bot_message, sticky_message, ai_suggestion, "
        "zammad_outbox, zammad_message_map "
        "RESTART IDENTITY CASCADE"
    )
    await Ticket.raw("ALTER SEQUENCE ticket_number_seq RESTART WITH 1")
    yield


@pytest.fixture(autouse=True)
def default_copy():
    """Each test starts (and ends) with the built-in default text."""
    from nephthys.core.copytext import DEFAULT_COPY
    from nephthys.core.copytext import use_copy

    use_copy(DEFAULT_COPY)
    yield
    use_copy(DEFAULT_COPY)
