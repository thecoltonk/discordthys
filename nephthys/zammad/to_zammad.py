"""Discord -> Zammad: the handlers for `to_zammad` outbox rows.

Every article is `type: note`, `internal: false` (the notes about a close are
internal). Nothing here creates an email-type article, and the customers
only have an address under `.invalid`, so no email can reach a player.
"""

import logging
from datetime import datetime
from datetime import timedelta
from datetime import UTC
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
# A player's avatar is uploaded to Zammad at most this often
AVATAR_REFRESH = timedelta(days=1)
AVATAR_MAX_BYTES = 5 * 1024 * 1024  # Zammad's Avatar::MAX_IMAGE_SIZE


def is_closed_state(state: object) -> bool:
    return isinstance(state, str) and state.lower() in CLOSED_STATES


# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------


def customer_name(user: User) -> str:
    return (user.username or "").strip() or f"Discord user {user.discord_id}"


async def ensure_customer(sync: "ZammadSync", user: User) -> int:
    """Find or create the Zammad customer for a Discord player (one per
    player), and remember its id. Their name and avatar are then kept up to
    date by `sync_profile`."""
    if user.zammad_user_id is not None:
        return user.zammad_user_id
    login = customer_login(user.discord_id)
    email = customer_email(user.discord_id)
    name = customer_name(user)
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
    await User.update({User.zammad_user_id: customer_id, User.zammad_name: name}).where(
        User.id == user.id
    )
    return customer_id


async def sync_profile(sync: "ZammadSync", user_id: int) -> None:
    """Bring the player's Zammad customer in line with Discord: their display
    name (whenever it changed) and their avatar (when it changed, at most once
    a day). Best effort: a failure is logged and never holds up the ticket."""
    try:
        await _sync_profile(sync, user_id)
    except Exception as e:  # noqa: BLE001 - never fail the outbox row over a profile
        log.warning(f"Couldn't update the Zammad profile of user id={user_id}: {e}")


async def _sync_profile(sync: "ZammadSync", user_id: int) -> None:
    user = await User.objects().where(User.id == user_id).first()
    if user is None or user.zammad_user_id is None:
        return
    name = customer_name(user)
    if name != user.zammad_name:
        await sync.client.update_user(
            user.zammad_user_id, {"firstname": name, "lastname": ""}
        )
        await User.update({User.zammad_name: name}).where(User.id == user.id)
        log.info(f"Zammad customer id={user.zammad_user_id} renamed")
    await _sync_avatar(sync, user)


def avatar_due(user: User, now: datetime) -> bool:
    if not user.avatar_url or not user.avatar_key:
        return False
    if user.avatar_key == user.zammad_avatar_key:
        return False
    last = user.zammad_avatar_at
    return last is None or now - last >= AVATAR_REFRESH


async def _sync_avatar(sync: "ZammadSync", user: User) -> None:
    client = sync.avatar_client
    now = datetime.now(UTC)
    if client is None or sync.avatars_off_reason or not avatar_due(user, now):
        return
    assert user.zammad_user_id is not None and user.avatar_url
    # Count the attempt first: a failure waits for the next day too
    await User.update({User.zammad_avatar_at: now}).where(User.id == user.id)
    async with sync.http().get(user.avatar_url) as response:
        response.raise_for_status()
        image = await response.read()
        mime = (response.content_type or "image/png").split(";")[0]
    if not image or len(image) > AVATAR_MAX_BYTES or not mime.startswith("image/"):
        log.info(f"Skipped the Discord avatar of user id={user.id} ({mime})")
        return
    try:
        await client.set_avatar_of(user.zammad_user_id, image, mime)
    except ZammadError as e:
        if e.status in (401, 403):
            # The token can't act as customers (no admin.user): stop trying
            # until the next restart, and say why once
            sync.avatars_off_reason = str(e)
            await sync.alert(
                "avatars",
                "⚠️ Can't set Discord avatars on Zammad customers: "
                f"`{e}`. ZAMMAD_AVATAR_TOKEN must belong to a Zammad user whose "
                "role grants `admin.user` (DEPLOY.md §7). Names are still synced.",
            )
        raise
    await User.update({User.zammad_avatar_key: user.avatar_key}).where(
        User.id == user.id
    )
    log.info(f"Zammad customer id={user.zammad_user_id} got the Discord avatar")


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
    await sync_profile(sync, opener.id)
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
                # Their name or avatar may have changed since the last message
                await sync_profile(sync, opener.id)
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
# close
# ---------------------------------------------------------------------------

REOPEN_REFUSED_NOTE = (
    "This ticket is closed in Discord, and closed Discord tickets can't be "
    "re-opened, so it has been closed here again. Its Discord thread is locked "
    "and archived: the player can't see new replies there. If they need more "
    "help, ask them to open a new ticket in #tickets."
)


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


async def close(sync: "ZammadSync", row: Row, ticket: Ticket) -> None:
    """Close the Zammad ticket and say why in an internal note: either it was
    closed in Discord, or (`reopen_refused`) someone re-opened it in Zammad
    after that, which Discord tickets don't support."""
    assert ticket.zammad_ticket_id is not None
    if row.payload.get("reopen_refused"):
        text = REOPEN_REFUSED_NOTE
    else:
        text = f"Closed in Discord by {row.payload.get('by') or 'the bot'}."
    current = await sync.client.get_ticket(ticket.zammad_ticket_id)
    if not is_closed_state(current.get("state")):
        await sync.client.update_ticket(ticket.zammad_ticket_id, {"state": "closed"})
    await _internal_note(sync, row, ticket, text)


async def retired(sync: "ZammadSync", row: Row, ticket: Ticket) -> None:
    """A re-open or CSAT note queued before those features were removed:
    nothing to send, the row just completes."""
    log.info(f"Skipped retired Zammad outbox row {row.id} ({row.kind})")


HANDLERS = {
    outbox.CREATE_TICKET: create_ticket,
    outbox.ARTICLE: article,
    outbox.CLOSE: close,
    outbox.REOPEN: retired,
    outbox.NOTE: retired,
}
