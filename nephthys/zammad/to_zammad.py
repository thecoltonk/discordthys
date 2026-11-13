"""Discord -> Zammad: the handlers for `to_zammad` outbox rows.

Every article is `type: note`, `internal: false` (CSAT and close/reopen notes
are internal). Nothing here creates an email-type article, and the customers
only have an address under `.invalid`, so no email can reach a player.
"""

import logging
from typing import Any
from typing import TYPE_CHECKING

import aiohttp
import discord

from nephthys.core import tickets
from nephthys.core.ticket_categories import get_ticket_categories
from nephthys.database.tables import Ticket
from nephthys.database.tables import User
from nephthys.discord_ops import staff_card
from nephthys.discord_ops import threads
from nephthys.zammad import outbox
from nephthys.zammad.client import encode_attachment
from nephthys.zammad.client import ZammadError
from nephthys.zammad.outbox import Row
from nephthys.zammad.text import customer_email
from nephthys.zammad.text import customer_login
from nephthys.zammad.text import marker
from nephthys.zammad.text import mirrored_body

if TYPE_CHECKING:
    from nephthys.zammad.sync import ZammadSync

log = logging.getLogger(__name__)

# The custom User attribute created by `zammad-setup`. Zammad rejects attribute
# names ending in "_id", so it can't literally be called "discord_id".
DISCORD_ATTRIBUTE = "discord_uid"
CLOSED_STATES = ("closed", "merged")


def is_closed_state(state: object) -> bool:
    return isinstance(state, str) and state.lower() in CLOSED_STATES


# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------


async def ensure_customer(sync: "ZammadSync", user: User) -> int:
    """Find or create the Zammad customer for a Discord player (one per
    player), and remember its id."""
    if user.zammad_user_id is not None:
        return user.zammad_user_id
    login = customer_login(user.discord_id)
    email = customer_email(user.discord_id)
    name = (user.username or "").strip() or f"Discord user {user.discord_id}"
    data = {
        "login": login,
        "firstname": name,
        "lastname": "",
        "email": email,
        DISCORD_ATTRIBUTE: str(user.discord_id),
        "roles": ["Customer"],
        "note": "Discord player (created by the Cloudsail Support bot).",
    }
    try:
        customer = await sync.client.create_user(data)
    except ZammadError as e:
        if not e.permanent:
            raise
        # Most likely it exists already (login and email are unique)
        customer = await sync.client.find_user(login=login, email=email)
        if customer is None:
            raise
        if customer.get("firstname") != name:
            await sync.client.update_user(
                customer["id"], {"firstname": name, "lastname": ""}
            )
    customer_id = int(customer["id"])
    await User.update({User.zammad_user_id: customer_id}).where(User.id == user.id)
    return customer_id


# ---------------------------------------------------------------------------
# create_ticket
# ---------------------------------------------------------------------------


def category_text(ticket: Ticket) -> tuple[str, str]:
    """(title part, group) for the ticket's category."""
    cats = get_ticket_categories()
    category = cats.get(ticket.category)
    if category is None:
        return ticket.category or "Ticket", ticket.category or "Other"
    sub = category.sub(ticket.subcategory)
    label = f"{category.label} ({sub.label})" if sub is not None else category.label
    return label, category.group


async def opening_body(sync: "ZammadSync", ticket: Ticket, opener: User) -> str:
    settings = sync.bot.settings
    name = opener.username or str(opener.discord_id)
    label, _ = category_text(ticket)
    lines = [
        f"{name} (Player via Discord)",
        "",
        f"Opened ticket T-{ticket.number} in #tickets.",
        f"Category: {label} · Priority: {ticket.priority or 'medium'}",
        f"Discord thread: {threads.thread_link(settings.guild_id, ticket.thread_id)}",
        f"Discord user: {name} ({opener.discord_id})",
    ]
    if ticket.escalated_from is not None:
        origin = await tickets.get_ticket(ticket.escalated_from)
        lines.append("")
        if origin is not None:
            lines.append(
                "Escalated from a public #community-help post: "
                + threads.thread_link(settings.guild_id, origin.thread_id)
            )
        question = (
            (origin.description if origin else "") or ticket.description or ""
        ).strip()
        lines.append("Original public question:")
        lines.append(question or "(no text)")
    return "\n".join(lines)


async def create_ticket(sync: "ZammadSync", row: Row, ticket: Ticket) -> None:
    if ticket.zammad_ticket_id is not None:
        return
    opener = await tickets.get_opener(ticket)
    customer_id = await ensure_customer(sync, opener)
    label, group = category_text(ticket)
    title = f"T-{ticket.number} {label}"

    created: dict[str, Any] | None = None
    if row.attempts > 0:
        # An earlier attempt may have created it and died before saving the id
        created = await sync.client.find_ticket(title=title, customer_id=customer_id)
    if created is None:
        created = await sync.client.create_ticket(
            {
                "title": title,
                "group": group,
                "customer_id": customer_id,
                "priority": get_ticket_categories().zammad.priority(ticket.priority),
                "article": {
                    "body": await opening_body(sync, ticket, opener),
                    "content_type": "text/plain",
                    "type": "note",
                    "internal": False,
                    "sender": "Customer",
                    "origin_by_id": customer_id,
                    "message_id": marker("ticket", ticket.id),
                },
            }
        )
    await Ticket.update(
        {
            Ticket.zammad_ticket_id: int(created["id"]),
            Ticket.zammad_ticket_number: str(created.get("number") or ""),
            Ticket.zammad_group: group,
        }
    ).where(Ticket.id == ticket.id)
    log.info(f"T-{ticket.number} is Zammad ticket id={created['id']}")
    # The staff card now gets the Zammad link
    await staff_card.refresh_staff_card(sync.bot, ticket.id)


# ---------------------------------------------------------------------------
# article
# ---------------------------------------------------------------------------


async def _download(sync: "ZammadSync", url: str) -> bytes:
    async with sync.http().get(url) as response:
        response.raise_for_status()
        return await response.read()


async def _fresh_urls(
    sync: "ZammadSync", ticket: Ticket, message_id: int
) -> dict[Any, str]:
    """Discord attachment links expire; ask Discord for current ones."""
    thread = await threads.get_thread(sync.bot, ticket.thread_id)
    if thread is None:
        return {}
    try:
        message = await thread.fetch_message(message_id)
    except discord.HTTPException:
        return {}
    return {a.id: a.url for a in message.attachments}


async def collect_attachments(
    sync: "ZammadSync", ticket: Ticket, payload: dict[str, Any]
) -> tuple[list[dict[str, str]], list[str]]:
    """Download the message's files for upload to Zammad. Files that are too
    large or gone are named in a note instead of failing the whole message."""
    limit = int(sync.bot.settings.zammad_attachment_max_mb * 1024 * 1024)
    uploads: list[dict[str, str]] = []
    notes: list[str] = []
    fresh: dict[Any, str] | None = None
    for item in payload.get("attachments") or []:
        name = item.get("filename") or "attachment"
        size = item.get("size") or 0
        if size > limit:
            notes.append(f"[Attachment not mirrored (too large): {name}]")
            continue
        url = item.get("url")
        data: bytes | None = None
        try:
            data = await _download(sync, url) if url else None
        except (aiohttp.ClientError, TimeoutError):
            if fresh is None:
                fresh = await _fresh_urls(sync, ticket, payload["message_id"])
            new_url = fresh.get(item.get("id"))
            if new_url:
                try:
                    data = await _download(sync, new_url)
                except (aiohttp.ClientError, TimeoutError):
                    data = None
        if data is None:
            notes.append(f"[Attachment not mirrored (download failed): {name}]")
        elif len(data) > limit:
            notes.append(f"[Attachment not mirrored (too large): {name}]")
        else:
            uploads.append(encode_attachment(name, data, item.get("content_type")))
    return uploads, notes


async def article(sync: "ZammadSync", row: Row, ticket: Ticket) -> None:
    assert ticket.zammad_ticket_id is not None
    payload = row.payload
    message_id = int(payload["message_id"])
    if await outbox.article_for_discord_message(message_id) is not None:
        return  # already mirrored
    mark = marker("msg", message_id)
    created: dict[str, Any] | None = None
    if row.attempts > 0:
        created = await sync.client.find_article(ticket.zammad_ticket_id, mark)
    if created is None:
        uploads, notes = await collect_attachments(sync, ticket, payload)
        is_staff = payload.get("role") == "Staff"
        data: dict[str, Any] = {
            "ticket_id": ticket.zammad_ticket_id,
            "body": mirrored_body(
                payload.get("author_name") or "Someone",
                "Staff" if is_staff else "Player",
                payload.get("content") or "",
                notes,
            ),
            "content_type": "text/plain",
            "type": "note",
            "internal": False,
            "sender": "Agent" if is_staff else "Customer",
            "message_id": mark,
        }
        if payload.get("is_opener"):
            opener = await tickets.get_opener(ticket)
            if opener.zammad_user_id is not None:
                data["origin_by_id"] = opener.zammad_user_id
        if uploads:
            data["attachments"] = uploads
        created = await sync.client.create_article(data)
    await outbox.record_mirror(
        ticket.id,
        origin=outbox.ORIGIN_DISCORD,
        discord_message_id=message_id,
        zammad_article_id=int(created["id"]),
    )


# ---------------------------------------------------------------------------
# close / reopen / note
# ---------------------------------------------------------------------------


async def _internal_note(
    sync: "ZammadSync", row: Row, ticket: Ticket, text: str
) -> None:
    assert ticket.zammad_ticket_id is not None
    mark = marker("note", row.id)
    if row.attempts > 0 and await sync.client.find_article(
        ticket.zammad_ticket_id, mark
    ):
        return
    await sync.client.create_article(
        {
            "ticket_id": ticket.zammad_ticket_id,
            "body": text,
            "content_type": "text/plain",
            "type": "note",
            "internal": True,
            "sender": "Agent",
            "message_id": mark,
        }
    )


async def _set_state(
    sync: "ZammadSync", row: Row, ticket: Ticket, *, closed: bool, text: str
) -> None:
    assert ticket.zammad_ticket_id is not None
    current = await sync.client.get_ticket(ticket.zammad_ticket_id)
    if is_closed_state(current.get("state")) != closed:
        await sync.client.update_ticket(
            ticket.zammad_ticket_id, {"state": "closed" if closed else "open"}
        )
    await _internal_note(sync, row, ticket, text)


async def close(sync: "ZammadSync", row: Row, ticket: Ticket) -> None:
    who = row.payload.get("by") or "the bot"
    await _set_state(
        sync, row, ticket, closed=True, text=f"Closed in Discord by {who}."
    )


async def reopen(sync: "ZammadSync", row: Row, ticket: Ticket) -> None:
    who = row.payload.get("by") or "staff"
    await _set_state(
        sync, row, ticket, closed=False, text=f"Re-opened in Discord by {who}."
    )


async def note(sync: "ZammadSync", row: Row, ticket: Ticket) -> None:
    await _internal_note(sync, row, ticket, str(row.payload.get("text") or ""))


HANDLERS = {
    outbox.CREATE_TICKET: create_ticket,
    outbox.ARTICLE: article,
    outbox.CLOSE: close,
    outbox.REOPEN: reopen,
    outbox.NOTE: note,
}
