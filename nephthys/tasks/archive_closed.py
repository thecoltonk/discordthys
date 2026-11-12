"""Archive closed #tickets threads whose opener never rated (CSAT_TIMEOUT_HOURS)."""

import logging
from datetime import datetime
from datetime import timedelta
from datetime import UTC
from typing import TYPE_CHECKING

from nephthys.core.private_tickets import awaiting_archive
from nephthys.discord_ops.private_flow import archive_private

if TYPE_CHECKING:
    from nephthys.bot import SupportBot

log = logging.getLogger(__name__)

# How often the sweep runs (the timeout is in hours, so this is plenty)
SWEEP_MINUTES = 10


async def archive_expired_tickets(
    bot: "SupportBot", now: datetime | None = None
) -> int:
    cutoff = (now or datetime.now(UTC)) - timedelta(
        hours=bot.settings.csat_timeout_hours
    )
    expired = await awaiting_archive(closed_before=cutoff)
    for ticket in expired:
        await archive_private(bot, ticket)
    if expired:
        log.info(f"Archived {len(expired)} closed tickets after the CSAT timeout")
    return len(expired)
