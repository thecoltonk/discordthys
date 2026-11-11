import itertools
from datetime import datetime

from nephthys.core import tickets
from nephthys.core.permissions import Actor
from nephthys.database.tables import Ticket

_ids = itertools.count(1_000_000_000_000_000)


def snowflake() -> int:
    return next(_ids)


def player(discord_id: int | None = None, name: str = "player") -> Actor:
    return Actor(discord_id=discord_id or snowflake(), username=name, is_staff=False)


def staff(discord_id: int | None = None, name: str = "staff") -> Actor:
    return Actor(discord_id=discord_id or snowflake(), username=name, is_staff=True)


async def new_ticket(
    author: Actor | None = None, created_at: datetime | None = None
) -> Ticket:
    msg = snowflake()
    return await tickets.create_ticket(
        author=author or player(),
        channel_id=200,
        message_id=msg,
        thread_id=msg,
        title="How do I claim land?",
        description="How do I claim land?",
        created_at=created_at,
    )
