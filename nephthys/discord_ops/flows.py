"""Ticket flows: core state change first, then Discord side effects.

Used by the cog (messages), the buttons (interactions), macros and the stale
task, so every entry point behaves the same.
"""

import logging
from typing import Any
from typing import cast
from typing import TYPE_CHECKING

import discord

from nephthys.core import tickets
from nephthys.core.permissions import Actor
from nephthys.core.transcript import TRANSCRIPT
from nephthys.database.tables import Ticket
from nephthys.discord_ops import threads
from nephthys.discord_ops.heartbeat import send_heartbeat
from nephthys.errors.errors import PermissionDenied
from nephthys.errors.errors import TicketAlreadyClosedError
from nephthys.errors.errors import TicketNotClosedError
from nephthys.errors.errors import TicketNotFoundError
from nephthys.utils.metrics import TICKETS_CLAIMED
from nephthys.utils.metrics import TICKETS_OPENED
from nephthys.utils.metrics import TICKETS_REOPENED
from nephthys.utils.metrics import TICKETS_RESOLVED

if TYPE_CHECKING:
    from nephthys.bot import SupportBot

log = logging.getLogger(__name__)


def is_question(message: discord.Message) -> bool:
    return (
        not message.author.bot
        and message.webhook_id is None
        and message.type in threads.QUESTION_MESSAGE_TYPES
    )


# ---------------------------------------------------------------------------
# New question
# ---------------------------------------------------------------------------


async def open_ticket(bot: "SupportBot", message: discord.Message) -> Ticket | None:
    """Create a thread + ticket for a new top-level question."""
    settings = bot.settings
    actor = threads.actor_from_user(message.author, settings.staff_role_id)
    name = threads.thread_name_for(message)
    try:
        thread = await message.create_thread(
            name=name,
            auto_archive_duration=settings.thread_auto_archive_minutes,  # type: ignore[arg-type]
            reason=f"Support ticket for {message.author}",
        )
    except discord.HTTPException as e:
        # e.g. the message was deleted already, or someone made a thread on it
        log.warning(f"Could not create thread for message_id={message.id}: {e}")
        await send_heartbeat(
            bot, f"⚠️ Couldn't open a thread for {message.jump_url}: `{e}`"
        )
        return None

    ticket = await tickets.create_ticket(
        author=actor,
        channel_id=message.channel.id,
        message_id=message.id,
        thread_id=thread.id,
        title=name,
        description=message.content,
        created_at=message.created_at,
    )
    TICKETS_OPENED.labels(surface=str(ticket.surface)).inc()

    async with threads.best_effort("send_auto_reply"):
        await threads.send_auto_reply(bot, thread, ticket)
    await threads.add_reaction(bot, ticket, settings.open_reaction)
    await threads.send_staff_feed(bot, ticket, message.author)
    return ticket


# ---------------------------------------------------------------------------
# Replies in a ticket thread
# ---------------------------------------------------------------------------


async def record_reply(
    bot: "SupportBot", message: discord.Message, ticket: Ticket, actor: Actor
) -> None:
    """Track the reply (claiming if it's the first staff reply), and auto-reopen
    when the original poster replies to a resolved ticket."""
    result = await tickets.record_message(ticket, actor, at=message.created_at)
    if result.claimed:
        TICKETS_CLAIMED.inc()
    if result.should_reopen:
        try:
            reopened = await tickets.reopen(ticket.id, actor)
        except TicketNotClosedError:
            return  # someone else re-opened it first
        TICKETS_REOPENED.labels(source="auto").inc()
        await threads.post_reopened(bot, reopened, reopened_by=message.author)


# ---------------------------------------------------------------------------
# Resolve / reopen
# ---------------------------------------------------------------------------


async def resolve_ticket(
    bot: "SupportBot",
    ticket_id: int,
    actor: Actor | None,
    user: discord.abc.User | None,
    *,
    source: str,
) -> Ticket:
    """Resolve and apply side effects. Raises the core errors on refusal."""
    result = await tickets.resolve(ticket_id, actor)
    TICKETS_RESOLVED.labels(source=source).inc()
    await threads.post_resolved(
        bot,
        result.ticket,
        resolved_by=user,
        stale_days=bot.settings.stale_ticket_days,
    )
    return result.ticket


async def reopen_ticket(
    bot: "SupportBot",
    ticket_id: int,
    actor: Actor,
    user: discord.abc.User,
    *,
    source: str,
) -> Ticket:
    ticket = await tickets.reopen(ticket_id, actor)
    TICKETS_REOPENED.labels(source=source).inc()
    await threads.post_reopened(bot, ticket, reopened_by=user)
    return ticket


def refusal_message(error: Exception) -> str | None:
    """User-facing text for an expected refusal, or None if unexpected."""
    if isinstance(error, PermissionDenied):
        return TRANSCRIPT.not_allowed
    if isinstance(error, TicketAlreadyClosedError):
        return TRANSCRIPT.already_resolved
    if isinstance(error, TicketNotClosedError):
        return TRANSCRIPT.not_resolved
    if isinstance(error, TicketNotFoundError):
        return TRANSCRIPT.ticket_missing
    return None


async def _run_from_interaction(
    interaction: discord.Interaction[Any], ticket_id: int, *, reopen: bool
) -> None:
    bot = cast("SupportBot", interaction.client)
    # Acknowledge within 3s; side effects can take longer
    await interaction.response.defer()
    actor = threads.actor_from_user(interaction.user, bot.settings.staff_role_id)
    try:
        if reopen:
            await reopen_ticket(
                bot, ticket_id, actor, interaction.user, source="button"
            )
        else:
            await resolve_ticket(
                bot, ticket_id, actor, interaction.user, source="button"
            )
    except (
        PermissionDenied,
        TicketAlreadyClosedError,
        TicketNotClosedError,
        TicketNotFoundError,
    ) as e:
        text = refusal_message(e) or str(e)
        await interaction.followup.send(text, ephemeral=True)
    except Exception as e:
        log.exception(f"Button failed ticket_id={ticket_id} reopen={reopen}")
        await send_heartbeat(
            bot,
            f"❌ {'Re-open' if reopen else 'Resolve'} button failed for ticket "
            f"#{ticket_id}: `{type(e).__name__}: {e}`",
            ping=True,
        )
        await interaction.followup.send(
            "something went wrong there — staff have been told!", ephemeral=True
        )


async def resolve_from_interaction(
    interaction: discord.Interaction[Any], ticket_id: int
) -> None:
    await _run_from_interaction(interaction, ticket_id, reopen=False)


async def reopen_from_interaction(
    interaction: discord.Interaction[Any], ticket_id: int
) -> None:
    await _run_from_interaction(interaction, ticket_id, reopen=True)


# ---------------------------------------------------------------------------
# Deletions
# ---------------------------------------------------------------------------


async def handle_question_deleted(bot: "SupportBot", message_id: int) -> None:
    """The question message was deleted. If nobody replied, clean up entirely."""
    ticket = await tickets.get_ticket_by_message(message_id)
    if not ticket:
        return
    thread = await threads.get_thread(bot, ticket.thread_id)
    if thread is None:
        await tickets.delete_ticket(ticket.id)
        return
    if await threads.thread_has_human_replies(thread):
        log.info(f"Question for ticket id={ticket.id} deleted; keeping thread")
        return
    await tickets.delete_ticket(ticket.id)
    async with threads.best_effort("delete_thread"):
        await thread.delete(reason="Question deleted before anyone replied")


async def handle_thread_deleted(thread_id: int) -> None:
    ticket = await tickets.get_ticket_by_thread(thread_id)
    if ticket:
        await tickets.delete_ticket(ticket.id)
