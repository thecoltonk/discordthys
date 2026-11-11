"""Low-level Discord side effects for #community-help tickets.

Everything here is best-effort where it's cosmetic (reactions), and raises
where the caller needs to know (creating the thread).
"""

import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import discord

from nephthys.core import tickets
from nephthys.core.permissions import Actor
from nephthys.core.permissions import is_staff
from nephthys.core.transcript import TRANSCRIPT
from nephthys.database.enums import BotMessageKind
from nephthys.database.tables import Ticket
from nephthys.ui.buttons import reopen_view
from nephthys.ui.buttons import resolve_view
from nephthys.utils.metrics import DISCORD_ERRORS

if TYPE_CHECKING:
    from nephthys.bot import SupportBot

log = logging.getLogger(__name__)

THREAD_NAME_MAX = 90
NO_MENTIONS = discord.AllowedMentions.none()
# Ping only the user being addressed, never roles/everyone
USER_MENTIONS = discord.AllowedMentions(everyone=False, roles=False, users=True)
QUESTION_MESSAGE_TYPES = (discord.MessageType.default, discord.MessageType.reply)


@asynccontextmanager
async def best_effort(action: str):
    """Swallow and log Discord API errors for cosmetic side effects."""
    try:
        yield
    except discord.HTTPException as e:
        DISCORD_ERRORS.labels(action=action).inc()
        log.warning(f"Discord side effect failed action={action}: {e}")


def actor_from_user(user: discord.User | discord.Member, staff_role_id: int) -> Actor:
    """Map a Discord user to an `Actor`. Only guild members can be staff."""
    roles = getattr(user, "roles", None) or []
    return Actor(
        discord_id=user.id,
        username=getattr(user, "display_name", None) or user.name,
        is_staff=is_staff((r.id for r in roles), staff_role_id),
    )


def thread_name_for(message: discord.Message) -> str:
    """First line of the question, or a fallback for attachment-only posts."""
    first_line = next(
        (line.strip() for line in message.clean_content.splitlines() if line.strip()),
        "",
    )
    if not first_line:
        return TRANSCRIPT.thread_name_fallback.format(name=message.author.display_name)[
            :THREAD_NAME_MAX
        ]
    if len(first_line) > THREAD_NAME_MAX:
        return first_line[: THREAD_NAME_MAX - 1].rstrip() + "…"
    return first_line


def thread_link(guild_id: int, thread_id: int) -> str:
    return f"https://discord.com/channels/{guild_id}/{thread_id}"


async def get_thread(bot: "SupportBot", thread_id: int) -> discord.Thread | None:
    """Get a thread from cache, or fetch it (archived threads aren't cached)."""
    channel = bot.get_channel(thread_id)
    if isinstance(channel, discord.Thread):
        return channel
    try:
        fetched = await bot.fetch_channel(thread_id)
    except (discord.NotFound, discord.Forbidden):
        return None
    return fetched if isinstance(fetched, discord.Thread) else None


def question_message(bot: "SupportBot", ticket: Ticket) -> discord.PartialMessage:
    return bot.get_partial_messageable(ticket.channel_id).get_partial_message(
        ticket.message_id
    )


async def add_reaction(bot: "SupportBot", ticket: Ticket, emoji: str | None) -> None:
    if not emoji:
        return
    async with best_effort("add_reaction"):
        await question_message(bot, ticket).add_reaction(emoji)


async def remove_own_reaction(
    bot: "SupportBot", ticket: Ticket, emoji: str | None
) -> None:
    if not emoji or bot.user is None:
        return
    async with best_effort("remove_reaction"):
        await question_message(bot, ticket).remove_reaction(emoji, bot.user)


async def set_archived(thread: discord.Thread, archived: bool) -> None:
    if thread.archived == archived:
        return
    async with best_effort("archive" if archived else "unarchive"):
        await thread.edit(archived=archived)


async def send_auto_reply(bot: "SupportBot", thread: discord.Thread, ticket: Ticket):
    text = TRANSCRIPT.auto_reply.format(faq=TRANSCRIPT.faq_text(bot.settings.faq_url))
    msg = await thread.send(
        text, view=resolve_view(ticket.id), allowed_mentions=NO_MENTIONS
    )
    await tickets.record_bot_message(
        ticket.id, thread.id, msg.id, BotMessageKind.AUTO_REPLY
    )
    return msg


async def send_staff_feed(
    bot: "SupportBot", ticket: Ticket, author: discord.abc.User
) -> None:
    channel_id = bot.settings.staff_channel_id
    if not channel_id:
        return
    title = discord.utils.escape_markdown(ticket.title or "(no title)")
    line = TRANSCRIPT.staff_feed.format(
        id=ticket.id,
        title=title,
        user=author.mention,
        link=thread_link(bot.settings.guild_id, ticket.thread_id),
    )
    async with best_effort("staff_feed"):
        await bot.get_partial_messageable(channel_id).send(
            line, allowed_mentions=NO_MENTIONS
        )


async def post_resolved(
    bot: "SupportBot",
    ticket: Ticket,
    *,
    resolved_by: discord.abc.User | None,
    stale_days: int | None = None,
) -> None:
    """Closing message with a Re-open button, ✅ on the question, archive."""
    thread = await get_thread(bot, ticket.thread_id)
    if resolved_by is not None:
        text = TRANSCRIPT.resolved.format(user=resolved_by.mention)
    else:
        text = TRANSCRIPT.resolved_stale.format(days=stale_days or "a few")

    if thread is not None:
        await set_archived(thread, False)
        async with best_effort("send_resolved"):
            msg = await thread.send(
                text, view=reopen_view(ticket.id), allowed_mentions=NO_MENTIONS
            )
            await tickets.record_bot_message(
                ticket.id, thread.id, msg.id, BotMessageKind.RESOLVED
            )

    await add_reaction(bot, ticket, bot.settings.resolved_reaction)
    await remove_own_reaction(bot, ticket, bot.settings.open_reaction)

    if thread is not None:
        await set_archived(thread, True)


async def post_reopened(
    bot: "SupportBot", ticket: Ticket, *, reopened_by: discord.abc.User
) -> None:
    """Unarchive, swap ✅ back to 🤔, and say who re-opened it."""
    thread = await get_thread(bot, ticket.thread_id)
    if thread is not None:
        await set_archived(thread, False)

    await remove_own_reaction(bot, ticket, bot.settings.resolved_reaction)
    await add_reaction(bot, ticket, bot.settings.open_reaction)

    if thread is not None:
        async with best_effort("send_reopened"):
            msg = await thread.send(
                TRANSCRIPT.reopened.format(user=reopened_by.mention),
                allowed_mentions=NO_MENTIONS,
            )
            await tickets.record_bot_message(
                ticket.id, thread.id, msg.id, BotMessageKind.REOPENED
            )


async def thread_has_human_replies(thread: discord.Thread) -> bool:
    async for msg in thread.history(limit=100):
        if not msg.author.bot and msg.type in QUESTION_MESSAGE_TYPES:
            return True
    return False
