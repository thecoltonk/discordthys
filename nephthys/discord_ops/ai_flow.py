"""AI on new tickets: categorise, then (optionally) suggest a KB-grounded answer.

Modes (AI_ANSWER_MODE):
- shadow (default): suggestions go only to the staff channel, as a reply to
  the ticket card, with 👍/👎 buttons. Ratings are logged for accuracy stats.
- public: a clearly labelled bot answer in the thread with [This fixed it]
  (resolves) and [I still need a human].

AI messages never claim a ticket and never change "waiting": they're bot
messages, which the reply tracker ignores.
"""

import logging
from typing import Any
from typing import cast
from typing import TYPE_CHECKING

import discord

from nephthys.ai.answer import suggest_answer
from nephthys.ai.categorize import suggest_category
from nephthys.ai.kb import load_kb
from nephthys.core import ai_store
from nephthys.core import tickets
from nephthys.core.copytext import get_copy
from nephthys.core.permissions import can_resolve
from nephthys.core.users import upsert_user
from nephthys.database.enums import TicketStatus
from nephthys.database.tables import AISuggestion
from nephthys.database.tables import Ticket
from nephthys.discord_ops import flows
from nephthys.discord_ops import staff_card
from nephthys.discord_ops import threads
from nephthys.discord_ops.embeds import make_embed
from nephthys.discord_ops.send import respond_text
from nephthys.discord_ops.send import send_embed
from nephthys.discord_ops.send import send_text
from nephthys.errors.errors import TicketAlreadyClosedError
from nephthys.ui.buttons import public_answer_view
from nephthys.ui.buttons import shadow_feedback_view
from nephthys.utils.metrics import AI_ANSWERS
from nephthys.utils.metrics import AI_CATEGORIES

if TYPE_CHECKING:
    from nephthys.bot import SupportBot

log = logging.getLogger(__name__)


def sources_text(sources: list[str]) -> str:
    return ", ".join(f"`{s}`" for s in sources) or get_copy().ai.no_sources


# ---------------------------------------------------------------------------
# New ticket pipeline
# ---------------------------------------------------------------------------


async def process_new_ticket(bot: "SupportBot", ticket_id: int) -> None:
    client = bot.ai_client
    settings = bot.settings
    if client is None:
        return
    ticket = await tickets.get_ticket(ticket_id)
    if ticket is None:
        return
    title = ticket.title or ""
    question = ticket.description or ""

    if settings.ai_categorize_enabled:
        category = await suggest_category(
            client, settings.ticket_categories, title=title, question=question
        )
        AI_CATEGORIES.labels(result="set" if category else "none").inc()
        if category and await tickets.set_ai_category(
            ticket_id, category, allowed=settings.ticket_categories
        ):
            await staff_card.refresh_staff_card(bot, ticket_id)

    if settings.ai_answers_enabled:
        await answer_ticket(bot, ticket)


async def answer_ticket(bot: "SupportBot", ticket: Ticket) -> AISuggestion | None:
    client = bot.ai_client
    settings = bot.settings
    if client is None:
        return None
    mode = settings.ai_answer_mode
    decision = await suggest_answer(
        client,
        load_kb(settings.kb_dir),
        title=ticket.title or "",
        question=ticket.description or "",
        min_confidence=settings.ai_answer_min_confidence,
    )

    post = decision.post
    skip_reason = decision.skip_reason
    # Don't answer a ticket that got resolved while we were thinking
    current = await tickets.get_ticket(ticket.id)
    if current is None:
        return None
    if post and mode == ai_store.PUBLIC and current.status == TicketStatus.CLOSED:
        post, skip_reason = False, "ticket_closed"
    if post and mode == ai_store.SHADOW and not settings.staff_channel_id:
        post, skip_reason = False, "no_staff_channel"

    suggestion = await ai_store.record_attempt(
        ticket_id=ticket.id,
        mode=mode,
        model=client.model,
        answer=decision.answer,
        confidence=decision.confidence,
        kb_sources=decision.kb_sources,
        posted=post,
        skip_reason=skip_reason,
    )
    AI_ANSWERS.labels(
        mode=mode, result="posted" if post else (skip_reason or "skipped")
    ).inc()
    if not post:
        log.info(f"AI answer for ticket id={ticket.id} not posted: {skip_reason}")
        return suggestion

    try:
        if mode == ai_store.SHADOW:
            msg = await post_shadow(bot, current, suggestion)
        else:
            msg = await post_public(bot, current, suggestion)
    except discord.HTTPException as e:
        log.warning(f"Couldn't post AI answer for ticket id={ticket.id}: {e}")
        msg = None
    if msg is None:
        await AISuggestion.update(
            {AISuggestion.posted: False, AISuggestion.skip_reason: "send_failed"}
        ).where(AISuggestion.id == suggestion.id)
        return suggestion

    await ai_store.set_message(suggestion.id, msg.channel.id, msg.id)
    await staff_card.refresh_staff_card(bot, ticket.id)
    return suggestion


def shadow_embed(
    ticket: Ticket, suggestion: AISuggestion, guild_id: int
) -> discord.Embed:
    ai_copy = get_copy().ai
    style = get_copy().embeds.ai_shadow
    embed = make_embed(
        style,
        title=style.title.format(id=ticket.id, title=ticket.title or ""),
        description=style.description.format(answer=suggestion.answer or ""),
        url=threads.thread_link(guild_id, ticket.thread_id),
    )
    embed.add_field(
        name=ai_copy.shadow_field_confidence,
        value=f"{(suggestion.confidence or 0):.0%}",
    )
    embed.add_field(
        name=ai_copy.shadow_field_sources,
        value=sources_text(list(suggestion.kb_sources)),
    )
    return embed


async def post_shadow(bot: "SupportBot", ticket: Ticket, suggestion: AISuggestion):
    channel_id = bot.settings.staff_channel_id
    assert channel_id
    channel = bot.get_partial_messageable(channel_id)
    reference = (
        channel.get_partial_message(ticket.staff_card_message_id)
        if ticket.staff_card_message_id and ticket.staff_card_channel_id == channel_id
        else None
    )
    return await send_embed(
        channel,
        shadow_embed(ticket, suggestion, bot.settings.guild_id),
        view=shadow_feedback_view(suggestion.id),
        reference=(
            reference.to_reference(fail_if_not_exists=False)
            if reference is not None
            else None
        ),
    )


def public_embed(suggestion: AISuggestion) -> discord.Embed:
    ai_copy = get_copy().ai
    style = get_copy().embeds.ai_public
    return make_embed(
        style,
        title=style.title,
        description=style.description.format(answer=suggestion.answer or ""),
        footer=style.footer.format(
            sources=", ".join(suggestion.kb_sources) or ai_copy.no_sources
        ),
    )


async def post_public(bot: "SupportBot", ticket: Ticket, suggestion: AISuggestion):
    thread = await threads.get_thread(bot, ticket.thread_id)
    if thread is None:
        return None
    return await send_embed(
        thread, public_embed(suggestion), view=public_answer_view(suggestion.id)
    )


# ---------------------------------------------------------------------------
# Feedback buttons
# ---------------------------------------------------------------------------


def with_outcome_note(
    message: discord.Message | None, note: str
) -> discord.Embed | None:
    if message is None or not message.embeds:
        return None
    embed = message.embeds[0].copy()
    embed.add_field(name=get_copy().ai.field_outcome, value=note, inline=False)
    return embed


async def shadow_feedback_from_interaction(
    interaction: discord.Interaction[Any], suggestion_id: int, *, helpful: bool
) -> None:
    bot = cast("SupportBot", interaction.client)
    actor = threads.actor_from_user(interaction.user, bot.settings.staff_role_id)
    copy = get_copy()
    if not actor.is_staff:
        await respond_text(interaction, copy.errors.staff_only, ephemeral=True)
        return
    user = await upsert_user(actor)
    try:
        suggestion = await ai_store.set_outcome(
            suggestion_id,
            ai_store.HELPFUL if helpful else ai_store.NOT_HELPFUL,
            user.id,
        )
    except ai_store.OutcomeAlreadySetError:
        await respond_text(interaction, copy.errors.already_rated, ephemeral=True)
        return
    except LookupError:
        await respond_text(interaction, copy.errors.ticket_missing, ephemeral=True)
        return
    template = copy.ai.rated_helpful if helpful else copy.ai.rated_not_helpful
    note = template.format(user=interaction.user.mention)
    embed = with_outcome_note(interaction.message, note)
    if embed is not None:
        await interaction.response.edit_message(embed=embed, view=None)
    else:
        await interaction.response.edit_message(view=None)
    await staff_card.refresh_staff_card(bot, tickets.fk_id(suggestion.ticket))


async def public_outcome_from_interaction(
    interaction: discord.Interaction[Any], suggestion_id: int, *, fixed: bool
) -> None:
    bot = cast("SupportBot", interaction.client)
    actor = threads.actor_from_user(interaction.user, bot.settings.staff_role_id)
    copy = get_copy()
    suggestion = await ai_store.get_suggestion(suggestion_id)
    if suggestion is None:
        await respond_text(interaction, copy.errors.ticket_missing, ephemeral=True)
        return
    ticket_id = tickets.fk_id(suggestion.ticket)
    ticket = await tickets.get_ticket(ticket_id)
    if ticket is None:
        await respond_text(interaction, copy.errors.ticket_missing, ephemeral=True)
        return
    opener = await tickets.get_opener(ticket)
    if not can_resolve(actor, opener.discord_id):
        await respond_text(interaction, copy.errors.not_allowed, ephemeral=True)
        return

    user = await upsert_user(actor)
    try:
        await ai_store.set_outcome(
            suggestion_id, ai_store.FIXED if fixed else ai_store.NEEDS_HUMAN, user.id
        )
    except ai_store.OutcomeAlreadySetError:
        await respond_text(interaction, copy.errors.ai_already_answered, ephemeral=True)
        return

    template = copy.ai.outcome_fixed if fixed else copy.ai.outcome_needs_human
    note = template.format(user=interaction.user.mention)
    embed = with_outcome_note(interaction.message, note)
    if embed is not None:
        await interaction.response.edit_message(embed=embed, view=None)
    else:
        await interaction.response.edit_message(view=None)

    if fixed:
        try:
            await flows.resolve_ticket(
                bot, ticket_id, actor, interaction.user, source="ai"
            )
        except TicketAlreadyClosedError:
            await staff_card.refresh_staff_card(bot, ticket_id)
    else:
        thread = await threads.get_thread(bot, ticket.thread_id)
        if thread is not None:
            async with threads.best_effort("ai_need_human"):
                await send_text(thread, copy.ai.need_human_reply)
        await staff_card.refresh_staff_card(bot, ticket_id)
