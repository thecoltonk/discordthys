"""Zammad -> Discord: the handlers for `to_discord` outbox rows."""

import io
import logging
from typing import Any
from typing import TYPE_CHECKING

import discord

from nephthys.core import tickets
from nephthys.core.copytext import get_copy
from nephthys.database.enums import TicketStatus
from nephthys.database.tables import Ticket
from nephthys.discord_ops import staff_card
from nephthys.discord_ops import threads
from nephthys.discord_ops.send import send_text
from nephthys.discord_ops.send import send_webhook_text
from nephthys.errors.errors import TicketAlreadyClosedError
from nephthys.errors.errors import TicketNotClosedError
from nephthys.utils.metrics import TICKETS_CLAIMED
from nephthys.zammad import outbox
from nephthys.zammad.client import ZammadError
from nephthys.zammad.outbox import Row
from nephthys.zammad.text import agent_username
from nephthys.zammad.text import article_text
from nephthys.zammad.text import DISCORD_MESSAGE_MAX
from nephthys.zammad.text import split_message
from nephthys.zammad.to_zammad import is_closed_state

if TYPE_CHECKING:
    from nephthys.zammad.sync import ZammadSync

log = logging.getLogger(__name__)

WEBHOOK_NAME = "Cloudsail Support (Zammad replies)"


class GiveUp(Exception):
    """This row can never succeed (e.g. the thread was deleted)."""


def avatar_url(sync: "ZammadSync", image: object) -> str | None:
    """Zammad serves avatars at /api/v1/users/image/<hash> without a login.
    Discord's servers fetch the picture, so it needs the public address."""
    settings = sync.bot.settings
    if isinstance(image, str) and image and settings.zammad_public_url:
        return f"{settings.zammad_public_url}/api/v1/users/image/{image}"
    return settings.zammad_default_avatar_url


async def fetch_files(
    sync: "ZammadSync", ticket: Ticket, payload: dict[str, Any]
) -> tuple[list[tuple[str, bytes]], list[str]]:
    """Download the article's attachments from Zammad. Too-large or missing
    files become a one-line note; a Zammad outage raises (and is retried)."""
    copy = get_copy().zammad
    limit = int(sync.bot.settings.zammad_attachment_max_mb * 1024 * 1024)
    files: list[tuple[str, bytes]] = []
    notes: list[str] = []
    for item in (payload.get("attachments") or [])[:10]:  # Discord: 10 per message
        name = str(item.get("filename") or "attachment")
        try:
            size = int(item.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        data: bytes | None = None
        if size <= limit:
            try:
                data = await sync.client.download_attachment(
                    int(payload["zammad_ticket_id"]),
                    int(payload["article_id"]),
                    int(item["id"]),
                )
            except ZammadError as e:
                if not e.permanent:
                    raise
        if data is None or len(data) > limit:
            notes.append(copy.attachment_skipped.format(filename=name))
        else:
            files.append((name, data))
    return files, notes


def as_discord_files(files: list[tuple[str, bytes]]) -> list[discord.File]:
    # discord.File objects can only be sent once, so build them per attempt
    return [discord.File(io.BytesIO(data), filename=name) for name, data in files]


async def post_reply(
    sync: "ZammadSync",
    thread: Any,
    ticket: Ticket,
    *,
    name: str,
    image: object,
    content: str,
    files: list[tuple[str, bytes]],
) -> Any:
    """Post one message under the agent's Zammad name and avatar through the
    #tickets webhook. If that fails for any reason, fall back to a plain bot
    message ("**Name (Support):** text") and say so in the log channel."""
    copy = get_copy().zammad
    username = agent_username(copy.agent_name, name, copy.unknown_agent)
    name = name or copy.unknown_agent  # for the fallback message
    reason: str
    webhook = await sync.get_webhook()
    if webhook is None:
        reason = sync.webhook_error or "no webhook available"
    else:
        try:
            return await send_webhook_text(
                webhook,
                content,
                thread_id=thread.id,
                username=username,
                avatar_url=avatar_url(sync, image),
                files=as_discord_files(files),
            )
        except discord.HTTPException as e:
            reason = f"{type(e).__name__}: {e}"
            sync.forget_webhook()
    text = copy.fallback_reply.format(
        name=discord.utils.escape_markdown(name), text=content
    )[:DISCORD_MESSAGE_MAX]
    message = await send_text(thread, text, files=as_discord_files(files))
    log.warning(f"Zammad reply for T-{ticket.number} sent as a bot message: {reason}")
    await sync.alert(
        "webhook-fallback",
        f"⚠️ A Zammad reply in T-{ticket.number} was posted as a plain bot message "
        f"because the webhook failed: `{reason}`",
    )
    return message


async def post_article(sync: "ZammadSync", row: Row, ticket: Ticket) -> None:
    payload = row.payload
    article_id = int(payload["article_id"])
    progress = int(payload.get("progress") or 0)
    if progress == 0 and await outbox.is_known_article(article_id):
        return  # already in Discord (or written there in the first place)
    bot = sync.bot
    thread = await threads.get_thread(bot, ticket.thread_id)
    if thread is None:
        raise GiveUp(f"the Discord thread of T-{ticket.number} is gone")

    name = str(payload.get("author_name") or "").strip()
    text = article_text(payload.get("body"), payload.get("content_type"))
    files, notes = await fetch_files(sync, ticket, payload)
    if notes:
        text = "\n\n".join(part for part in (text, "\n".join(notes)) if part)
    chunks = split_message(text) or ([""] if files else [])

    if chunks and thread.archived:
        # Webhooks unarchive threads themselves, but not locked ones
        async with threads.best_effort("unarchive_for_reply"):
            await thread.edit(archived=False)
    for index in range(progress, len(chunks)):
        last = index == len(chunks) - 1
        message = await post_reply(
            sync,
            thread,
            ticket,
            name=name,
            image=payload.get("author_image"),
            content=chunks[index],
            files=files if last else [],
        )
        await outbox.record_mirror(
            ticket.id,
            origin=outbox.ORIGIN_ZAMMAD,
            discord_message_id=getattr(message, "id", None),
            zammad_article_id=article_id,
        )
        # Saved after every part, so a crash never re-posts what was sent
        payload["progress"] = index + 1
        await outbox.save_payload(row.id, payload)
    if not chunks:
        await outbox.record_mirror(
            ticket.id,
            origin=outbox.ORIGIN_ZAMMAD,
            discord_message_id=None,
            zammad_article_id=article_id,
        )
        return

    # An agent's first public reply is the staff claim, as in Discord
    await tickets.record_external_staff_message(ticket.id)
    if await tickets.claim_by_name(ticket.id, name or get_copy().zammad.unknown_agent):
        TICKETS_CLAIMED.inc()
        await staff_card.refresh_staff_card(bot, ticket.id)
    if ticket.archived_at is not None and ticket.status == TicketStatus.CLOSED:
        # A reply on a finished ticket: put the thread away again
        async with threads.best_effort("rearchive_after_reply"):
            await thread.edit(archived=True)


async def sync_ticket(sync: "ZammadSync", row: Row, ticket: Ticket) -> None:
    """Bring the Discord ticket in line with Zammad's current state and group.

    It reads the ticket fresh instead of trusting the event that queued it,
    so late, repeated or out-of-order webhooks all end in the same place."""
    from nephthys.discord_ops import private_flow

    if ticket.zammad_ticket_id is None:
        return
    bot = sync.bot
    current = await sync.client.get_ticket(ticket.zammad_ticket_id)
    by_name = row.payload.get("by_name") or None

    group = current.get("group")
    if isinstance(group, str) and group and group != ticket.zammad_group:
        await Ticket.update({Ticket.zammad_group: group}).where(Ticket.id == ticket.id)
        await staff_card.refresh_staff_card(bot, ticket.id)

    # A close/reopen made in Discord that Zammad hasn't received yet wins;
    # delivering it triggers another sync
    if await outbox.has_pending(
        ticket.id, outbox.TO_ZAMMAD, (outbox.CLOSE, outbox.REOPEN)
    ):
        return
    closed_in_zammad = is_closed_state(current.get("state"))
    closed_in_discord = ticket.status == TicketStatus.CLOSED
    if closed_in_zammad and not closed_in_discord:
        try:
            await private_flow.close_private(
                bot,
                ticket.id,
                None,
                None,
                source="zammad",
                by_name=by_name,
                mirror=False,
            )
        except TicketAlreadyClosedError:
            pass
    elif closed_in_discord and not closed_in_zammad:
        try:
            await private_flow.reopen_private(
                bot,
                ticket.id,
                None,
                None,
                source="zammad",
                by_name=by_name,
                mirror=False,
            )
        except TicketNotClosedError:
            pass


HANDLERS = {
    outbox.POST_ARTICLE: post_article,
    outbox.SYNC_TICKET: sync_ticket,
}
