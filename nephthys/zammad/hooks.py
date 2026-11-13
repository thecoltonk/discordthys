"""What the Discord flows call when something happens in a #tickets thread.

Every function only writes a row to the outbox (and nudges the worker), so a
slow or unreachable Zammad never delays or breaks a Discord flow. All of them
are no-ops while the integration is off.
"""

import logging
from typing import Any
from typing import TYPE_CHECKING

from nephthys.database.enums import TicketSurface
from nephthys.database.tables import Ticket
from nephthys.zammad import outbox

if TYPE_CHECKING:
    from nephthys.bot import SupportBot

log = logging.getLogger(__name__)


def enabled(bot: "SupportBot", ticket: Ticket | None = None) -> bool:
    if not bot.settings.zammad_enabled:
        return False
    return ticket is None or ticket.surface == TicketSurface.PRIVATE


def _wake(bot: "SupportBot") -> None:
    sync = getattr(bot, "zammad", None)
    if sync is not None:
        sync.wake()


async def _enqueue(
    bot: "SupportBot",
    ticket: Ticket,
    kind: str,
    payload: dict[str, Any] | None = None,
    *,
    dedupe_key: str | None = None,
) -> None:
    try:
        await outbox.enqueue(
            ticket.id, outbox.TO_ZAMMAD, kind, payload, dedupe_key=dedupe_key
        )
    except Exception:
        log.exception(f"Couldn't queue Zammad {kind} for ticket id={ticket.id}")
        return
    _wake(bot)


async def ticket_ready(bot: "SupportBot", ticket: Ticket) -> None:
    """A leaf category was picked: create the Zammad ticket."""
    if enabled(bot, ticket):
        await _enqueue(
            bot, ticket, outbox.CREATE_TICKET, dedupe_key=f"dz:create:{ticket.id}"
        )


def attachment_payload(attachment: Any) -> dict[str, Any]:
    return {
        "id": getattr(attachment, "id", None),
        "url": getattr(attachment, "url", None),
        "filename": getattr(attachment, "filename", None) or "attachment",
        "size": getattr(attachment, "size", None),
        "content_type": getattr(attachment, "content_type", None),
    }


async def thread_message(
    bot: "SupportBot",
    message: Any,
    ticket: Ticket,
    *,
    is_staff: bool,
    is_opener: bool,
) -> None:
    """A person wrote in the ticket thread: mirror it as a Zammad article.

    Messages the bot posted on behalf of Zammad agents never get here: they are
    webhook or bot messages, which the cog ignores. The map check below is a
    second guard against echoing them back."""
    if not enabled(bot, ticket):
        return
    if await outbox.article_for_discord_message(message.id) is not None:
        return
    author = message.author
    attachments = getattr(message, "attachments", None)
    await _enqueue(
        bot,
        ticket,
        outbox.ARTICLE,
        {
            "message_id": message.id,
            "author_id": author.id,
            "author_name": getattr(author, "display_name", None) or author.name,
            "role": "Staff" if is_staff and not is_opener else "Player",
            "is_opener": is_opener,
            "content": getattr(message, "clean_content", None) or message.content or "",
            "attachments": [
                attachment_payload(a)
                for a in (attachments if isinstance(attachments, list) else [])
            ],
        },
        dedupe_key=f"dz:msg:{message.id}",
    )


async def ticket_closed(bot: "SupportBot", ticket: Ticket, by: str | None) -> None:
    if not enabled(bot, ticket):
        return
    await outbox.drop_unstarted(ticket.id)
    await _enqueue(bot, ticket, outbox.CLOSE, {"by": by})


async def ticket_reopened(bot: "SupportBot", ticket: Ticket, by: str | None) -> None:
    if enabled(bot, ticket):
        await _enqueue(bot, ticket, outbox.REOPEN, {"by": by})


async def internal_note(bot: "SupportBot", ticket: Ticket, text: str) -> None:
    """An internal (agents-only) note, e.g. the CSAT rating."""
    if enabled(bot, ticket):
        await _enqueue(bot, ticket, outbox.NOTE, {"text": text})
