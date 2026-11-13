"""Ticket flows: core state change first, then Discord side effects.

Used by the cog (messages), the buttons (interactions), macros and the stale
task, so every entry point behaves the same.
"""

import logging
from typing import Any
from typing import cast
from typing import TYPE_CHECKING

import discord

from nephthys.core import private_tickets
from nephthys.core import tickets
from nephthys.core.copytext import get_copy
from nephthys.core.permissions import Actor
from nephthys.database.enums import TicketSurface
from nephthys.database.tables import Ticket
from nephthys.discord_ops import staff_card
from nephthys.discord_ops import threads
from nephthys.discord_ops.heartbeat import send_heartbeat
from nephthys.discord_ops.send import followup_text
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
    # No staff card: STAFF_CHANNEL_ID is for #tickets. Community tickets still
    # count in /stats, /waiting and the daily summary (they're read from the DB).
    if bot.ai_client is not None:
        from nephthys.discord_ops.ai_flow import process_new_ticket

        bot.spawn(process_new_ticket(bot, ticket.id), name=f"ai-ticket-{ticket.id}")
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
        await staff_card.refresh_staff_card(bot, ticket.id)
    # Private tickets are locked when closed; only #community-help auto-reopens
    if result.should_reopen and ticket.surface == TicketSurface.COMMUNITY:
        try:
            reopened = await tickets.reopen(ticket.id, actor)
        except TicketNotClosedError:
            return  # someone else re-opened it first
        TICKETS_REOPENED.labels(source="auto").inc()
        await threads.post_reopened(bot, reopened, reopened_by=message.author)
        await staff_card.refresh_staff_card(bot, ticket.id)


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
    """Resolve and apply side effects. Raises the core errors on refusal.
    Private (#tickets) tickets get the close + CSAT + lock flow instead."""
    existing = await tickets.require_ticket(ticket_id)
    if existing.surface == TicketSurface.PRIVATE:
        from nephthys.discord_ops.private_flow import close_private

        return await close_private(bot, ticket_id, actor, user, source=source)
    result = await tickets.resolve(ticket_id, actor)
    TICKETS_RESOLVED.labels(source=source).inc()
    await threads.post_resolved(
        bot,
        result.ticket,
        resolved_by=user,
        stale_days=bot.settings.stale_ticket_days,
    )
    await staff_card.refresh_staff_card(bot, ticket_id)
    return result.ticket


async def reopen_ticket(
    bot: "SupportBot",
    ticket_id: int,
    actor: Actor,
    user: discord.abc.User,
    *,
    source: str,
) -> Ticket:
    existing = await tickets.require_ticket(ticket_id)
    if existing.surface == TicketSurface.PRIVATE:
        from nephthys.discord_ops.private_flow import reopen_private

        return await reopen_private(bot, ticket_id, actor, user, source=source)
    ticket = await tickets.reopen(ticket_id, actor)
    TICKETS_REOPENED.labels(source=source).inc()
    await threads.post_reopened(bot, ticket, reopened_by=user)
    await staff_card.refresh_staff_card(bot, ticket_id)
    return ticket


def refusal_message(error: Exception) -> str | None:
    """User-facing text for an expected refusal, or None if unexpected."""
    errors = get_copy().errors
    if isinstance(error, PermissionDenied):
        return errors.not_allowed
    if isinstance(error, TicketAlreadyClosedError):
        return errors.already_resolved
    if isinstance(error, TicketNotClosedError):
        return errors.not_resolved
    if isinstance(error, TicketNotFoundError):
        return errors.ticket_missing
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
        await followup_text(interaction, refusal_message(e) or str(e))
    except Exception as e:
        log.exception(f"Button failed ticket_id={ticket_id} reopen={reopen}")
        await send_heartbeat(
            bot,
            f"❌ {'Re-open' if reopen else 'Resolve'} button failed for ticket "
            f"#{ticket_id}: `{type(e).__name__}: {e}`",
            ping=True,
        )
        await followup_text(interaction, get_copy().community.something_went_wrong)


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
    if await private_tickets.escalation_of(ticket.id) is not None:
        return  # escalated (maybe deleted by /escalate): keep the record
    thread = await threads.get_thread(bot, ticket.thread_id)
    if thread is None:
        await staff_card.delete_staff_card(bot, ticket)
        await tickets.delete_ticket(ticket.id)
        return
    if await threads.thread_has_human_replies(thread):
        log.info(f"Question for ticket id={ticket.id} deleted; keeping thread")
        return
    await staff_card.delete_staff_card(bot, ticket)
    await tickets.delete_ticket(ticket.id)
    async with threads.best_effort("delete_thread"):
        await thread.delete(reason="Question deleted before anyone replied")


async def handle_thread_deleted(bot: "SupportBot", thread_id: int) -> None:
    ticket = await tickets.get_ticket_by_thread(thread_id)
    if ticket:
        await staff_card.delete_staff_card(bot, ticket)
        await tickets.delete_ticket(ticket.id)
