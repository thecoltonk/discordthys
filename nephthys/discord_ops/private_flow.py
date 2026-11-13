"""#tickets (private, Mava-style) flows.

Create Ticket -> private thread named after the player -> opener message
("Ticket T-<n>", welcome, category buttons, Close Ticket). Each pick deletes and
reposts the opener so the buttons stay at the bottom; the "You have selected"
lines stack up; at a leaf the category buttons go away (Close stays) and the
staff alert card is posted with role pings. Close -> closing message + CSAT
1-5 (opener only, first click) -> lock + archive. Staff can /reopen.
"""

import logging
from dataclasses import dataclass
from typing import Any
from typing import cast
from typing import TYPE_CHECKING

import discord

from nephthys.core import private_tickets as pt
from nephthys.core import tickets
from nephthys.core.copytext import get_copy
from nephthys.core.permissions import Actor
from nephthys.core.permissions import can_resolve
from nephthys.core.ticket_categories import get_ticket_categories
from nephthys.database.enums import TicketStatus
from nephthys.database.tables import Ticket
from nephthys.discord_ops import staff_card
from nephthys.discord_ops import threads
from nephthys.discord_ops.embeds import make_embed
from nephthys.discord_ops.heartbeat import send_heartbeat
from nephthys.discord_ops.send import edit_text_response
from nephthys.discord_ops.send import followup_text
from nephthys.discord_ops.send import NO_MENTIONS
from nephthys.discord_ops.send import respond_text
from nephthys.discord_ops.send import send_embed
from nephthys.discord_ops.send import send_text
from nephthys.errors.errors import PermissionDenied
from nephthys.errors.errors import TicketAlreadyClosedError
from nephthys.errors.errors import TicketNotClosedError
from nephthys.errors.errors import TicketNotFoundError
from nephthys.ui.ticket_buttons import csat_face
from nephthys.ui.ticket_buttons import csat_view
from nephthys.ui.ticket_buttons import opener_view
from nephthys.utils.metrics import TICKETS_OPENED
from nephthys.utils.metrics import TICKETS_REOPENED
from nephthys.utils.metrics import TICKETS_RESOLVED
from nephthys.zammad import hooks as zammad_hooks

if TYPE_CHECKING:
    from nephthys.bot import SupportBot

log = logging.getLogger(__name__)

THREAD_NAME_MAX = 100
MESSAGE_MAX = 2000


class TicketCreateError(Exception):
    pass


@dataclass(frozen=True)
class Player:
    """Who a private ticket is for (the clicker, or the escalated poster)."""

    discord_id: int
    display_name: str


def player_from(user: discord.abc.User) -> Player:
    return Player(
        discord_id=user.id, display_name=getattr(user, "display_name", user.name)
    )


def ticket_link(bot: "SupportBot", ticket: Ticket) -> str:
    return threads.thread_link(bot.settings.guild_id, ticket.thread_id)


async def tickets_channel(bot: "SupportBot") -> discord.TextChannel | None:
    channel_id = bot.settings.tickets_channel_id
    if not channel_id:
        return None
    channel = bot.get_channel(channel_id)
    if isinstance(channel, discord.TextChannel):
        return channel
    try:
        fetched = await bot.fetch_channel(channel_id)
    except discord.HTTPException:
        return None
    return fetched if isinstance(fetched, discord.TextChannel) else None


# ---------------------------------------------------------------------------
# The opener message
# ---------------------------------------------------------------------------


def opener_content(
    ticket: Ticket,
    opener_discord_id: int,
    *,
    community_link: str | None,
    header: bool = True,
) -> str:
    """The opener text. `header=False` leaves out "Ticket T-<n>" (embed mode,
    where it's the embed title)."""
    copy = get_copy().tickets
    cats = get_ticket_categories()
    lines = [copy.opener_header.format(number=ticket.number)] if header else []
    lines.append(copy.welcome.format(user=f"<@{opener_discord_id}>"))
    if community_link:
        lines.append(copy.escalated_note.format(link=community_link))
    if ticket.category:
        category = cats.get(ticket.category)
        lines.append("")
        lines.append(
            copy.selected.format(
                choice=category.display if category else ticket.category
            )
        )
        sub = category.sub(ticket.subcategory) if category else None
        if sub is not None:
            lines.append(copy.selected.format(choice=sub.display))
        elif ticket.subcategory:
            lines.append(copy.selected.format(choice=ticket.subcategory))
        blurb = sub.blurb if sub else (category.blurb if category else "")
        if blurb:
            lines.append("")
            lines.append(blurb)
    return "\n".join(lines)[:MESSAGE_MAX]


def opener_buttons(ticket: Ticket) -> discord.ui.View:
    cats = get_ticket_categories()
    if not ticket.category:
        return opener_view(ticket.id, categories=cats.categories)
    category = cats.get(ticket.category)
    if category is not None and not category.is_leaf and not ticket.subcategory:
        return opener_view(
            ticket.id, category_key=category.key, subcategories=category.subcategories
        )
    return opener_view(ticket.id)  # leaf reached: only Close Ticket


async def community_link_for(bot: "SupportBot", ticket: Ticket) -> str | None:
    if ticket.escalated_from is None:
        return None
    origin = await tickets.get_ticket(ticket.escalated_from)
    return ticket_link(bot, origin) if origin else None


async def post_opener(
    bot: "SupportBot", ticket: Ticket, thread: Any, *, first: bool
) -> Any:
    opener = await tickets.get_opener(ticket)
    community_link = await community_link_for(bot, ticket)
    style = get_copy().embeds.ticket_opener
    if style.enabled:
        body = opener_content(
            ticket, opener.discord_id, community_link=community_link, header=False
        )
        embed = make_embed(
            style,
            title=style.title.format(number=ticket.number),
            description=style.description.format(body=body),
            footer=style.footer.format(number=ticket.number),
        )
        msg = await send_embed(
            thread,
            embed,
            view=opener_buttons(ticket),
            # Mentions inside embeds don't ping, so ping the player in the content
            ping_content=f"<@{opener.discord_id}>" if first else None,
            ping_users=[opener.discord_id] if first else None,
        )
        await pt.set_opener_message(ticket.id, msg.id)
        return msg
    content = opener_content(ticket, opener.discord_id, community_link=community_link)
    msg = await send_text(
        thread,
        content,
        view=opener_buttons(ticket),
        # Ping the player on the very first opener so they get a notification
        allowed_mentions=(
            discord.AllowedMentions(
                everyone=False, roles=False, users=[discord.Object(opener.discord_id)]
            )
            if first
            else NO_MENTIONS
        ),
    )
    await pt.set_opener_message(ticket.id, msg.id)
    return msg


async def repost_opener(bot: "SupportBot", ticket: Ticket) -> None:
    """Send the new opener first, then delete the old one (never zero)."""
    thread = await threads.get_thread(bot, ticket.thread_id)
    if thread is None:
        return
    old = ticket.opener_message_id
    await post_opener(bot, ticket, thread, first=False)
    if old:
        async with threads.best_effort("delete_old_opener"):
            await thread.get_partial_message(old).delete()


# ---------------------------------------------------------------------------
# Creating tickets
# ---------------------------------------------------------------------------


async def open_private_ticket(
    bot: "SupportBot",
    player: Player,
    *,
    escalated_from: Ticket | None = None,
    quote: str | None = None,
) -> Ticket:
    """Create the private thread, add the player, create the ticket, post the
    opener. Raises `TicketCreateError` if Discord refuses."""
    settings = bot.settings
    copy = get_copy().tickets
    channel = await tickets_channel(bot)
    if channel is None:
        raise TicketCreateError("TICKETS_CHANNEL_ID is unset or not a text channel")

    number = await pt.allocate_number(settings.ticket_number_start)
    name = copy.thread_name.format(name=player.display_name, number=number)
    try:
        thread = await channel.create_thread(
            name=name[:THREAD_NAME_MAX],
            type=discord.ChannelType.private_thread,
            invitable=False,
            auto_archive_duration=settings.thread_auto_archive_minutes,  # type: ignore[arg-type]
            reason=f"T-{number} for {player.display_name}",
        )
    except discord.HTTPException as e:
        await send_heartbeat(
            bot,
            f"❌ Couldn't create a private thread for T-{number}: `{e}`",
            error=True,
        )
        raise TicketCreateError(str(e)) from e
    try:
        await thread.add_user(discord.Object(player.discord_id))
    except discord.HTTPException as e:
        await send_heartbeat(
            bot, f"❌ Couldn't add the player to T-{number}: `{e}`", error=True
        )
        async with threads.best_effort("delete_failed_ticket_thread"):
            await thread.delete()
        raise TicketCreateError(str(e)) from e

    actor = Actor(
        discord_id=player.discord_id, username=player.display_name, is_staff=False
    )
    ticket = await pt.create_private_ticket(
        author=actor,
        channel_id=channel.id,
        thread_id=thread.id,
        number=number,
        escalated_from=escalated_from.id if escalated_from else None,
        description=(escalated_from.description if escalated_from else "") or "",
    )
    if escalated_from is not None and escalated_from.title:
        await Ticket.update({Ticket.title: escalated_from.title}).where(
            Ticket.id == ticket.id
        )
        ticket = await tickets.require_ticket(ticket.id)
    TICKETS_OPENED.labels(surface=str(ticket.surface)).inc()

    if quote:
        async with threads.best_effort("send_escalated_quote"):
            await send_text(
                thread, copy.escalated_quote.format(quote=quote)[:MESSAGE_MAX]
            )
    await post_opener(bot, ticket, thread, first=True)
    log.info(f"Opened private ticket T-{number} id={ticket.id} for {player.discord_id}")
    return ticket


async def create_from_interaction(interaction: discord.Interaction[Any]) -> None:
    bot = cast("SupportBot", interaction.client)
    copy = get_copy().tickets
    if not bot.settings.tickets_enabled:
        await respond_text(interaction, copy.tickets_disabled, ephemeral=True)
        return
    limit = bot.settings.tickets_max_open_per_user
    if limit:
        open_tickets = await pt.open_private_tickets(interaction.user.id)
        if len(open_tickets) >= limit:
            links = " ".join(ticket_link(bot, t) for t in open_tickets)
            await respond_text(
                interaction,
                copy.too_many_open.format(count=len(open_tickets), links=links),
                ephemeral=True,
            )
            return
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        ticket = await open_private_ticket(bot, player_from(interaction.user))
    except TicketCreateError:
        await followup_text(interaction, copy.create_failed)
        return
    await followup_text(interaction, copy.created.format(link=ticket_link(bot, ticket)))


# ---------------------------------------------------------------------------
# Category picks
# ---------------------------------------------------------------------------


async def is_opener_or_staff(ticket: Ticket, actor: Actor) -> bool:
    opener = await tickets.get_opener(ticket)
    return can_resolve(actor, opener.discord_id)


async def pick_from_interaction(
    interaction: discord.Interaction[Any],
    ticket_id: int,
    category_key: str,
    sub_key: str | None,
) -> None:
    bot = cast("SupportBot", interaction.client)
    copy = get_copy()
    ticket = await tickets.get_ticket(ticket_id)
    if ticket is None:
        await respond_text(interaction, copy.errors.ticket_missing, ephemeral=True)
        return
    actor = threads.actor_from_user(interaction.user, bot.settings.staff_role_id)
    if not await is_opener_or_staff(ticket, actor):
        await respond_text(interaction, copy.tickets.not_your_ticket, ephemeral=True)
        return
    category = get_ticket_categories().get(category_key)
    sub = category.sub(sub_key) if (category and sub_key) else None
    if category is None or (sub_key is not None and sub is None):
        await respond_text(interaction, copy.tickets.option_gone, ephemeral=True)
        return

    if sub is None:
        ok = await pt.pick_category(
            ticket.id,
            category.key,
            leaf_priority=category.priority if category.is_leaf else None,
        )
    else:
        ok = await pt.pick_subcategory(
            ticket.id, category.key, sub.key, priority=sub.priority
        )
    if not ok:
        await respond_text(interaction, copy.tickets.already_picked, ephemeral=True)
        return

    await interaction.response.defer()
    ticket = await tickets.require_ticket(ticket.id)
    await repost_opener(bot, ticket)
    if ticket.priority is not None:  # a leaf was reached
        await on_leaf(bot, ticket)


def ping_role_ids(bot: "SupportBot", ticket: Ticket) -> list[int]:
    category = get_ticket_categories().get(ticket.category)
    roles = [bot.settings.role_id_for(category.role if category else "staff")]
    if ticket.priority == "high" and bot.settings.staff_role_id not in roles:
        roles.append(bot.settings.staff_role_id)
    return roles


async def on_leaf(bot: "SupportBot", ticket: Ticket) -> None:
    """Post the staff alert card, pinging the owning role (+ Staff if High),
    and start the Zammad mirror."""
    await zammad_hooks.ticket_ready(bot, ticket)
    if ticket.staff_card_message_id:
        return
    roles = ping_role_ids(bot, ticket)
    content = get_copy().tickets.alert_ping.format(
        roles=" ".join(f"<@&{r}>" for r in roles),
        priority=staff_card.priority_label(ticket.priority),
    )
    await staff_card.post_staff_card(
        bot, ticket, ping_content=content, ping_roles=roles
    )


# ---------------------------------------------------------------------------
# Close / CSAT / reopen
# ---------------------------------------------------------------------------


async def close_private(
    bot: "SupportBot",
    ticket_id: int,
    actor: Actor | None,
    user: discord.abc.User | None,
    *,
    source: str,
    by_name: str | None = None,
    mirror: bool = True,
) -> Ticket:
    """Resolve and post the closing message + CSAT prompt. The thread stays
    open (locked for the player when TICKETS_LOCK_ON_CLOSE) so the opener can
    rate; it's archived when they rate or after CSAT_TIMEOUT_HOURS. A ticket
    that was already rated has nothing to wait for and is archived now.
    Raises the core refusal errors.

    `by_name` is who closed it when that isn't a Discord user (a Zammad agent);
    `mirror=False` means the close came from Zammad, so it isn't sent back."""
    result = await tickets.resolve(ticket_id, actor, by_name=by_name)
    TICKETS_RESOLVED.labels(source=source).inc()
    ticket = result.ticket
    copy = get_copy().tickets
    thread = await threads.get_thread(bot, ticket.thread_id)
    if thread is not None:
        if thread.archived:
            async with threads.best_effort("unarchive"):
                await thread.edit(archived=False)
        who = who_text(user, by_name)
        closed_text = copy.closed.format(user=who)
        closed_style = get_copy().embeds.ticket_closed
        async with threads.best_effort("send_closed"):
            if closed_style.enabled:
                await send_embed(
                    thread,
                    make_embed(
                        closed_style,
                        title=closed_style.title,
                        description=closed_style.description.format(
                            message=closed_text
                        ),
                    ),
                )
            else:
                await send_text(thread, closed_text)
        if ticket.csat_rating is None:
            async with threads.best_effort("send_csat"):
                await send_text(thread, copy.csat_prompt, view=csat_view(ticket.id))
            if bot.settings.tickets_lock_on_close:
                async with threads.best_effort("lock_on_close"):
                    await thread.edit(locked=True)
    await pt.clear_archived(ticket.id)
    if ticket.csat_rating is not None:
        await archive_private(bot, ticket)
    await staff_card.refresh_staff_card(bot, ticket.id)
    if mirror:
        await zammad_hooks.ticket_closed(bot, ticket, display_name_of(user))
    return ticket


def display_name_of(user: discord.abc.User | None) -> str | None:
    if user is None:
        return None
    return getattr(user, "display_name", None) or user.name


def who_text(user: discord.abc.User | None, by_name: str | None) -> str:
    """Who did something, for a thread message: a mention, or the bold name of
    someone without a Discord account here (a Zammad agent)."""
    if user is not None:
        return user.mention
    if by_name:
        return f"**{discord.utils.escape_markdown(by_name)}**"
    return get_copy().card.someone


async def archive_private(bot: "SupportBot", ticket: Ticket) -> None:
    """Lock + archive a closed ticket's thread and record that it's done.
    Tolerates a thread that's already archived (or gone)."""
    thread = await threads.get_thread(bot, ticket.thread_id)
    if thread is not None and not (thread.archived and thread.locked):
        async with threads.best_effort("lock_archive"):
            await thread.edit(locked=True, archived=True)
    await pt.mark_archived(ticket.id)
    log.info(f"Archived closed ticket T-{ticket.number} id={ticket.id}")


async def reopen_private(
    bot: "SupportBot",
    ticket_id: int,
    actor: Actor | None,
    user: discord.abc.User | None,
    *,
    source: str,
    by_name: str | None = None,
    mirror: bool = True,
) -> Ticket:
    """Staff only (the player can't reach a locked thread anyway). `actor=None`
    with `by_name` is a re-open made in Zammad (`mirror=False`: not sent back)."""
    if actor is not None and not actor.is_staff:
        raise PermissionDenied(
            "Only staff can re-open a private ticket", user_id=actor.discord_id
        )
    ticket = await tickets.reopen(ticket_id, actor)
    TICKETS_REOPENED.labels(source=source).inc()
    await pt.clear_archived(ticket.id)  # cancels any pending CSAT-timeout archive
    thread = await threads.get_thread(bot, ticket.thread_id)
    if thread is not None:
        async with threads.best_effort("unlock_unarchive"):
            await thread.edit(archived=False, locked=False)
        opener = await tickets.get_opener(ticket)
        async with threads.best_effort("readd_player"):
            await thread.add_user(discord.Object(opener.discord_id))
        async with threads.best_effort("send_reopened"):
            await send_text(
                thread,
                get_copy().tickets.reopened.format(user=who_text(user, by_name)),
            )
    await staff_card.refresh_staff_card(bot, ticket.id)
    if mirror:
        await zammad_hooks.ticket_reopened(bot, ticket, display_name_of(user))
    return ticket


async def close_from_interaction(
    interaction: discord.Interaction[Any], ticket_id: int
) -> None:
    bot = cast("SupportBot", interaction.client)
    copy = get_copy()
    ticket = await tickets.get_ticket(ticket_id)
    if ticket is None:
        await respond_text(interaction, copy.errors.ticket_missing, ephemeral=True)
        return
    actor = threads.actor_from_user(interaction.user, bot.settings.staff_role_id)
    if not await is_opener_or_staff(ticket, actor):
        await respond_text(interaction, copy.tickets.not_your_ticket, ephemeral=True)
        return
    await interaction.response.defer()
    try:
        await close_private(bot, ticket_id, actor, interaction.user, source="button")
    except (
        PermissionDenied,
        TicketAlreadyClosedError,
        TicketNotFoundError,
        TicketNotClosedError,
    ) as e:
        from nephthys.discord_ops.flows import refusal_message

        await followup_text(interaction, refusal_message(e) or str(e))


async def csat_from_interaction(
    interaction: discord.Interaction[Any], ticket_id: int, rating: int
) -> None:
    bot = cast("SupportBot", interaction.client)
    copy = get_copy()
    actor = threads.actor_from_user(interaction.user, bot.settings.staff_role_id)
    try:
        ticket = await pt.rate_csat(ticket_id, rating, actor)
    except pt.NotTicketOpenerError:
        await respond_text(interaction, copy.tickets.csat_not_opener, ephemeral=True)
        return
    except pt.CSATAlreadyRatedError:
        await respond_text(interaction, copy.tickets.csat_already, ephemeral=True)
        return
    except TicketNotFoundError:
        await respond_text(interaction, copy.errors.ticket_missing, ephemeral=True)
        return
    thanks = copy.tickets.csat_thanks.format(
        rating=rating, emoji=csat_face(rating)[1]
    ).strip()
    try:
        # Keep the row, disabled, so it's clear the rating is done
        await edit_text_response(
            interaction, thanks, view=csat_view(ticket_id, disabled=True)
        )
    except discord.HTTPException as e:
        # e.g. the thread was archived by the CSAT timeout: the rating is saved,
        # so just thank them privately
        log.info(f"Couldn't edit the CSAT prompt for ticket id={ticket_id}: {e}")
        async with threads.best_effort("csat_thanks_fallback"):
            await respond_text(interaction, thanks, ephemeral=True)
    # Rating ends the CSAT wait. (A rating left on an old prompt after the
    # ticket was re-opened is recorded, but doesn't archive an open ticket.)
    if ticket.status == TicketStatus.CLOSED and ticket.archived_at is None:
        await archive_private(bot, ticket)
    await staff_card.refresh_staff_card(bot, ticket_id)
    await zammad_hooks.internal_note(
        bot,
        ticket,
        f"CSAT: the player rated this ticket {rating}/5 in Discord.",
    )


# ---------------------------------------------------------------------------
# Messages in a private thread
# ---------------------------------------------------------------------------


async def record_private_message(
    bot: "SupportBot", message: discord.Message, ticket: Ticket, actor: Actor
) -> None:
    """The player's first message becomes the ticket description (shown on the
    staff card); staff replies claim, as in #community-help."""
    from nephthys.discord_ops.flows import record_reply

    opener = await tickets.get_opener(ticket)
    is_opener = message.author.id == opener.discord_id
    if (
        is_opener
        and not ticket.description
        and await pt.set_first_message(ticket.id, message.content)
    ):
        await staff_card.refresh_staff_card(bot, ticket.id)
    await record_reply(bot, message, ticket, actor)
    await zammad_hooks.thread_message(
        bot, message, ticket, is_staff=actor.is_staff, is_opener=is_opener
    )
