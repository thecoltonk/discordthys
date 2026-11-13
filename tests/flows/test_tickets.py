"""#tickets (Phase 2): panel, create, category flow, alerts, close, CSAT, escalate."""

from types import SimpleNamespace
from typing import Any
from typing import cast

import discord
import pytest

from nephthys.cogs.community_help import CommunityHelp
from nephthys.cogs.ticket_commands import TicketCommands
from nephthys.cogs.tickets import Tickets
from nephthys.core import private_tickets as pt
from nephthys.core import tickets
from nephthys.core.copytext import DEFAULT_COPY as C
from nephthys.core.ticket_categories import Category
from nephthys.core.ticket_categories import DEFAULT_TICKET_CATEGORIES
from nephthys.core.ticket_categories import TicketCategories
from nephthys.core.ticket_categories import use_ticket_categories
from nephthys.database.enums import TicketStatus
from nephthys.database.enums import TicketSurface
from nephthys.database.enums import UserType
from nephthys.discord_ops import flows
from nephthys.ui.ticket_buttons import CategoryButton
from nephthys.ui.ticket_buttons import CloseTicketButton
from nephthys.ui.ticket_buttons import CreateTicketButton
from nephthys.ui.ticket_buttons import CSATButton
from nephthys.ui.ticket_buttons import SubcategoryButton
from tests.fakes import FakeBot
from tests.fakes import make_interaction
from tests.fakes import make_message
from tests.fakes import make_text_channel
from tests.fakes import make_user
from tests.helpers import make_settings

pytestmark = pytest.mark.usefixtures("db")

TICKETS = 600
STAFF_ROLE = 300
STORE_ROLE = 301


@pytest.fixture(autouse=True)
def default_categories():
    use_ticket_categories(DEFAULT_TICKET_CATEGORIES)
    yield
    use_ticket_categories(DEFAULT_TICKET_CATEGORIES)


def tickets_bot(**env: str) -> FakeBot:
    env = {"TICKETS_CHANNEL_ID": str(TICKETS), "STORE_ROLE_ID": str(STORE_ROLE), **env}
    bot = FakeBot(make_settings(**env))
    make_text_channel(bot, TICKETS)
    return bot


def ids(view: discord.ui.View) -> list[str]:
    return [cast(Any, item).custom_id for item in view.children]


def fields(embed: Any) -> dict[str, str]:
    return {f.name: f.value for f in embed.fields}


async def create(bot: FakeBot, user=None) -> tuple[Any, Any, Any, Any]:
    """Click Create Ticket; return (ticket, thread, user, interaction)."""
    user = user or make_user(name="Alex")
    i = make_interaction(bot, user)
    await CreateTicketButton().callback(i)
    channel = bot.world.text_channels[TICKETS]
    if not channel.created_threads:
        return None, None, user, i
    thread, _ = channel.created_threads[-1]
    ticket = await tickets.get_ticket_by_thread(thread.id)
    return ticket, thread, user, i


def opener(bot: FakeBot, ticket) -> Any:
    return bot.message(ticket.thread_id, ticket.opener_message_id)


async def fresh(ticket):
    return await tickets.require_ticket(ticket.id)


async def pick(bot, ticket, user, category, sub=None):
    i = make_interaction(bot, user)
    if sub is None:
        await CategoryButton(ticket.id, category).callback(i)
    else:
        await SubcategoryButton(ticket.id, category, sub).callback(i)
    return i


def staff_sends(bot: FakeBot) -> list[Any]:
    return bot.sends(bot.settings.staff_channel_id or 0)


# ---------------------------------------------------------------------------


async def test_panel_is_a_sticky_with_create_button():
    bot = tickets_bot()
    cog = Tickets(cast(Any, bot))
    assert cog.sticky is not None
    await cog.sticky.ensure()
    [panel] = bot.sends(TICKETS)
    assert panel.embed.title == "Need help?"
    assert f"<#{bot.settings.community_channel_id}>" in panel.embed.description
    assert ids(panel.view) == ["cs:tcreate"]
    assert Tickets(cast(Any, FakeBot())).sticky is None  # disabled without channel


async def test_panel_reposts_after_channel_activity_but_not_its_own():
    bot = tickets_bot(STICKY_DEBOUNCE_SECONDS="0")
    cog = Tickets(cast(Any, bot))
    channel = SimpleNamespace(id=TICKETS)
    assert cog.sticky is not None
    await cog.on_message(
        make_message(bot, make_user(bot=True), "panel", channel=channel)
    )
    assert cog.sticky._task is None
    system = make_message(
        bot,
        make_user(bot=True),
        "",
        channel=channel,
        msg_type=discord.MessageType.thread_created,
    )
    await cog.on_message(system)
    await cog.sticky.wait_idle()
    assert len([m for m in bot.sends(TICKETS) if getattr(m, "embed", None)]) == 1


async def test_create_ticket_opens_private_thread_with_opener():
    bot = tickets_bot()
    ticket, thread, user, i = await create(bot)
    assert ticket and thread
    channel = bot.world.text_channels[TICKETS]
    _, kwargs = channel.created_threads[0]
    assert kwargs["type"] == discord.ChannelType.private_thread
    assert kwargs["invitable"] is False
    assert thread.name == "Alex (T-26)"
    assert thread.added_users == [user.id]

    assert ticket.surface == TicketSurface.PRIVATE and ticket.number == 26
    msg = opener(bot, ticket)
    assert msg.content.startswith("## Ticket T-26\n")
    assert f"<@{user.id}>" in msg.content
    assert msg.suppress_embeds is True
    assert [u.id for u in msg.allowed_mentions.users] == [user.id]  # first opener pings
    assert ids(msg.view) == [
        f"cs:tcat:{ticket.id}:server_issue",
        f"cs:tcat:{ticket.id}:store",
        f"cs:tcat:{ticket.id}:bans_appeals",
        f"cs:tcat:{ticket.id}:reports",
        f"cs:tcat:{ticket.id}:other",
        f"cs:tclose:{ticket.id}",
    ]
    assert msg.view.children[-1].item.row == 4
    i.response.defer.assert_awaited_once()
    assert i.followup.send.call_args.args[0] == C.tickets.created.format(
        link=f"https://discord.com/channels/100/{thread.id}"
    )
    assert i.followup.send.call_args.kwargs["ephemeral"] is True
    assert not staff_sends(bot)  # no alert until a leaf category is picked

    ticket2, *_ = await create(bot)
    assert ticket2 and ticket2.number == 27


async def test_open_ticket_limit():
    bot = tickets_bot(TICKETS_MAX_OPEN_PER_USER="2")
    user = make_user()
    first, *_ = await create(bot, user)
    await create(bot, user)
    _third, _, _, i = await create(bot, user)
    assert len(bot.world.text_channels[TICKETS].created_threads) == 2
    text = i.response.send_message.call_args.args[0]
    assert text.startswith("you already have 2 open ticket(s)")
    assert first and str(first.thread_id) in text
    # Closing one frees a slot
    await tickets.resolve(first.id, None)
    t, *_ = await create(bot, user)
    assert t is not None
    unlimited = tickets_bot(TICKETS_MAX_OPEN_PER_USER="0")
    for _ in range(3):
        await create(unlimited, user)
    assert len(unlimited.world.text_channels[TICKETS].created_threads) == 3


async def test_tickets_disabled_and_creation_failures():
    bot = FakeBot()
    i = make_interaction(bot, make_user())
    await CreateTicketButton().callback(i)
    assert i.response.send_message.call_args.args[0] == C.tickets.tickets_disabled

    bot = tickets_bot()
    bot.world.text_channels[TICKETS].create_thread_fails = True
    ticket, _, _, i = await create(bot)
    assert ticket is None
    assert i.followup.send.call_args.args[0] == C.tickets.create_failed


async def test_add_user_failure_deletes_thread():
    bot = tickets_bot()
    channel = bot.world.text_channels[TICKETS]
    original = channel.create_thread.side_effect

    async def failing(**kwargs):
        thread = await original(**kwargs)
        thread.add_user_fails = True
        return thread

    channel.create_thread.side_effect = failing
    user = make_user()
    i = make_interaction(bot, user)
    await CreateTicketButton().callback(i)
    thread, _ = channel.created_threads[0]
    assert thread.deleted
    assert await tickets.get_ticket_by_thread(thread.id) is None
    assert i.followup.send.call_args.args[0] == C.tickets.create_failed


async def test_category_flow_with_subcategory_and_high_priority_alert():
    bot = tickets_bot()
    ticket, thread, user, _ = await create(bot)
    assert ticket
    first_opener_id = ticket.opener_message_id

    await pick(bot, ticket, user, "server_issue")
    ticket = await fresh(ticket)
    assert (thread.id, first_opener_id) in bot.world.deleted_messages
    msg = opener(bot, ticket)
    assert "You have selected: **❗ Server Issue**" in msg.content
    assert "What kind of server issue is it?" in msg.content
    assert msg.allowed_mentions.users is False  # reposts don't ping again
    assert ids(msg.view) == [
        f"cs:tsub:{ticket.id}:server_issue:urgent",
        f"cs:tsub:{ticket.id}:server_issue:bug_report",
        f"cs:tsub:{ticket.id}:server_issue:other_technical",
        f"cs:tclose:{ticket.id}",
    ]
    assert not staff_sends(bot)

    second_opener_id = ticket.opener_message_id
    await pick(bot, ticket, user, "server_issue", "urgent")
    ticket = await fresh(ticket)
    assert (ticket.category, ticket.subcategory, ticket.priority) == (
        "server_issue",
        "urgent",
        "high",
    )
    assert (thread.id, second_opener_id) in bot.world.deleted_messages
    msg = opener(bot, ticket)
    lines = msg.content.splitlines()
    assert lines.count("You have selected: **❗ Server Issue**") == 1
    assert "You have selected: **💥 Urgent**" in lines
    assert "Staff have been alerted" in msg.content
    assert ids(msg.view) == [f"cs:tclose:{ticket.id}"]  # leaf: only Close remains

    [alert] = staff_sends(bot)
    assert alert.content == f"<@&{STAFF_ROLE}> new **🔴 High** ticket"
    assert [r.id for r in alert.allowed_mentions.roles] == [STAFF_ROLE]
    assert alert.embed.title == "T-26 · ❗ Server Issue › 💥 Urgent"
    f = fields(alert.embed)
    assert f["Priority"] == "🔴 High"
    assert f["Status"].startswith("🟡 Waiting")
    assert alert.embed.description == C.card.awaiting_first_message
    assert ids(alert.view) == [None]  # link button only (no category select)
    assert ticket.staff_card_message_id == alert.id


async def test_leaf_category_pings_owning_role():
    bot = tickets_bot()
    ticket, _, user, _ = await create(bot)
    assert ticket
    await pick(bot, ticket, user, "store")
    ticket = await fresh(ticket)
    assert ticket.priority == "medium" and ticket.subcategory is None
    [alert] = staff_sends(bot)
    assert alert.content == f"<@&{STORE_ROLE}> new **🟡 Medium** ticket"
    assert "Please tell us your **IGN**" in opener(bot, ticket).content


async def test_mod_role_falls_back_to_staff_and_high_pings_both():
    use_ticket_categories(
        TicketCategories(
            categories=(
                Category(
                    key="hack", label="Hacked", role="mod", priority="high", blurb="b"
                ),
            )
        )
    )
    bot = tickets_bot(MOD_ROLE_ID="302")
    ticket, _, user, _ = await create(bot)
    assert ticket
    await pick(bot, ticket, user, "hack")
    [alert] = staff_sends(bot)
    assert alert.content == f"<@&302> <@&{STAFF_ROLE}> new **🔴 High** ticket"

    bot = tickets_bot()  # no MOD_ROLE_ID: falls back to Staff (pinged once)
    ticket, _, user, _ = await create(bot)
    assert ticket
    await pick(bot, ticket, user, "hack")
    assert staff_sends(bot)[0].content == f"<@&{STAFF_ROLE}> new **🔴 High** ticket"


async def test_pick_permissions_and_double_clicks():
    bot = tickets_bot()
    ticket, _, user, _ = await create(bot)
    assert ticket
    rando = await pick(bot, ticket, make_user(), "store")
    assert rando.response.send_message.call_args.args[0] == C.tickets.not_your_ticket
    assert (await fresh(ticket)).category is None

    staff = await pick(bot, ticket, make_user(staff=True), "reports")
    staff.response.defer.assert_awaited_once()
    again = await pick(bot, ticket, user, "store")  # stale button
    assert again.response.send_message.call_args.args[0] == C.tickets.already_picked
    assert (await fresh(ticket)).category == "reports"

    gone = await pick(bot, ticket, user, "minigames")
    assert gone.response.send_message.call_args.args[0] == C.tickets.option_gone
    assert len(staff_sends(bot)) == 1


async def test_first_message_and_claim_update_the_card():
    bot = tickets_bot()
    cog = Tickets(cast(Any, bot))
    ticket, thread, user, _ = await create(bot)
    assert ticket
    await pick(bot, ticket, user, "store")

    await cog.on_message(
        make_message(bot, user, "my rank is missing\nbought VIP", channel=thread)
    )
    await cog.on_message(make_message(bot, user, "please help", channel=thread))
    ticket = await fresh(ticket)
    assert ticket.description == "my rank is missing\nbought VIP"
    card = bot.message(
        bot.settings.staff_channel_id or 0, ticket.staff_card_message_id or 0
    )
    assert card.embed.description == "my rank is missing\nbought VIP"

    mod = make_user(staff=True)
    await cog.on_message(make_message(bot, mod, "on it", channel=thread))
    ticket = await fresh(ticket)
    assert (
        ticket.status == TicketStatus.IN_PROGRESS
        and ticket.last_msg_by == UserType.HELPER
    )
    assert fields(card.embed)["Status"] == f"🔵 Claimed by <@{mod.id}>"


async def test_close_by_opener_then_csat():
    bot = tickets_bot()
    ticket, thread, user, _ = await create(bot)
    assert ticket
    await pick(bot, ticket, user, "store")
    mod = make_user(staff=True)
    await tickets.record_message(await fresh(ticket), threads_actor(mod))

    rando = make_interaction(bot, make_user())
    await CloseTicketButton(ticket.id).callback(rando)
    assert rando.response.send_message.call_args.args[0] == C.tickets.not_your_ticket

    await CloseTicketButton(ticket.id).callback(make_interaction(bot, user))
    ticket = await fresh(ticket)
    assert ticket.status == TicketStatus.CLOSED
    closing, csat = bot.sends(ticket.thread_id)[-2:]
    assert closing.content == C.tickets.closed.format(user=user.mention)
    assert csat.content == C.tickets.csat_prompt
    assert ids(csat.view) == [f"cs:csat:{ticket.id}:{n}" for n in range(1, 6)]
    # Locked for the player, but left open so they can still rate
    assert thread.locked and not thread.archived
    assert ticket.archived_at is None
    card = bot.message(
        bot.settings.staff_channel_id or 0, ticket.staff_card_message_id or 0
    )
    assert fields(card.embed)["Status"].startswith("✅ Resolved")

    again = make_interaction(bot, user)
    await CloseTicketButton(ticket.id).callback(again)
    assert again.followup.send.call_args.args[0] == C.errors.already_resolved

    staff_vote = make_interaction(bot, mod)
    await CSATButton(ticket.id, 5).callback(staff_vote)
    assert (
        staff_vote.response.send_message.call_args.args[0] == C.tickets.csat_not_opener
    )

    vote = make_interaction(bot, user)
    await CSATButton(ticket.id, 4).callback(vote)
    kwargs = vote.response.edit_message.call_args.kwargs
    assert kwargs == {
        "content": C.tickets.csat_thanks.format(rating=4),
        "view": None,
        "suppress_embeds": True,
    }
    assert (await fresh(ticket)).csat_rating == 4
    assert fields(card.embed)["CSAT"] == "4/5"
    # Rating ends the wait: the thread is archived now
    assert thread.locked and thread.archived
    assert (await fresh(ticket)).archived_at is not None

    second = make_interaction(bot, user)
    await CSATButton(ticket.id, 1).callback(second)
    assert second.response.send_message.call_args.args[0] == C.tickets.csat_already
    assert (await fresh(ticket)).csat_rating == 4


def threads_actor(user):
    from nephthys.discord_ops.threads import actor_from_user

    return actor_from_user(user, STAFF_ROLE)


async def test_staff_resolve_and_reopen_commands_in_private_thread():
    bot = tickets_bot()
    commands = TicketCommands(cast(Any, bot))
    ticket, thread, user, _ = await create(bot)
    assert ticket
    await pick(bot, ticket, user, "other")
    mod = make_user(staff=True)

    i = make_interaction(bot, mod, channel=thread)
    await cast(Any, TicketCommands.resolve).callback(commands, i)
    assert i.followup.send.call_args.args[0] == C.commands.resolved
    assert thread.locked and not thread.archived  # waiting for the rating
    assert bot.sends(ticket.thread_id)[-1].content == C.tickets.csat_prompt

    thread.added_users.clear()
    i = make_interaction(bot, mod, channel=thread)
    await cast(Any, TicketCommands.reopen).callback(commands, i)
    assert i.followup.send.call_args.args[0] == C.commands.reopened
    assert not thread.locked and not thread.archived
    assert thread.added_users == [user.id]
    assert bot.sends(ticket.thread_id)[-1].content == C.tickets.reopened.format(
        user=mod.mention
    )
    assert (await fresh(ticket)).status == TicketStatus.OPEN

    # Closing again after a rating doesn't ask for CSAT a second time, and
    # there's nothing to wait for, so it archives straight away
    await pt.rate_csat(ticket.id, 5, threads_actor(user))
    i = make_interaction(bot, mod, channel=thread)
    await cast(Any, TicketCommands.resolve).callback(commands, i)
    assert bot.sends(ticket.thread_id)[-1].content == C.tickets.closed.format(
        user=mod.mention
    )
    assert thread.locked and thread.archived
    assert (await fresh(ticket)).archived_at is not None


async def test_player_cannot_reopen_private_ticket():
    bot = tickets_bot()
    ticket, _, user, _ = await create(bot)
    assert ticket
    await tickets.resolve(ticket.id, None)
    with pytest.raises(Exception) as exc:
        await flows.reopen_ticket(
            cast(Any, bot), ticket.id, threads_actor(user), user, source="x"
        )
    assert type(exc.value).__name__ == "PermissionDenied"


# ---------------------------------------------------------------------------
# /escalate
# ---------------------------------------------------------------------------


async def community_question(
    bot: FakeBot, op=None, content="my account got hacked, password is hunter2"
):
    cog = CommunityHelp(cast(Any, bot))
    op = op or make_user(name="Sam")
    q = make_message(bot, op, content)
    await cog.on_message(q)
    ticket = await tickets.get_ticket_by_message(q.id)
    assert ticket
    return ticket, bot.world.threads[ticket.thread_id], op, q


async def escalate(bot, cog, user, channel, delete_original=False):
    i = make_interaction(bot, user, channel=channel)
    await cast(Any, Tickets.escalate).callback(cog, i, delete_original)
    return i


async def test_escalate_moves_to_private_and_links_both_ways():
    bot = tickets_bot()
    cog = Tickets(cast(Any, bot))
    community, public_thread, op, q = await community_question(bot)
    mod = make_user(staff=True)

    i = await escalate(bot, cog, mod, public_thread)
    private = await pt.escalation_of(community.id)
    assert private and private.surface == TicketSurface.PRIVATE
    private_link = f"https://discord.com/channels/100/{private.thread_id}"
    community_link = f"https://discord.com/channels/100/{community.thread_id}"
    assert i.followup.send.call_args.args[0] == C.tickets.escalate_done.format(
        link=private_link
    )

    # Private side: OP added, details copied, link back to the public thread
    private_thread = bot.world.threads[private.thread_id]
    assert private_thread.added_users == [op.id]
    assert private_thread.name == "Sam (T-26)"
    assert (
        private.description == community.description
        and private.title == community.title
    )
    assert (
        C.tickets.escalated_note.format(link=community_link)
        in opener(bot, private).content
    )

    # Public side: note with link, resolved, ✅, locked + archived
    note = bot.sends(community.thread_id)[-1]
    assert note.content == C.tickets.escalated_public.format(
        user=op.mention, link=private_link
    )
    community = await fresh(community)
    assert community.status == TicketStatus.CLOSED
    assert public_thread.locked and public_thread.archived
    assert bot.reactions_on(q.id) == {"✅"}
    # No community card any more (staff channel is #tickets only)
    assert community.staff_card_message_id is None

    again = await escalate(bot, cog, mod, public_thread)
    assert again.response.send_message.call_args.args[
        0
    ] == C.tickets.already_escalated.format(link=private_link)

    # The private ticket's card (once a category is picked) links back
    await pick(bot, private, op, "other")
    alert = staff_sends(bot)[-1]
    assert (
        fields(alert.embed)["Escalated from"] == f"[#{community.id}]({community_link})"
    )


async def test_escalate_delete_original_copies_then_deletes():
    bot = tickets_bot()
    cog = Tickets(cast(Any, bot))
    community, public_thread, _op, q = await community_question(bot)
    await escalate(bot, cog, make_user(staff=True), public_thread, delete_original=True)
    private = await pt.escalation_of(community.id)
    assert private
    quote, opener_msg = bot.sends(private.thread_id)[-2:]
    assert quote.content == C.tickets.escalated_quote.format(
        quote=community.description
    )
    assert opener_msg.id == private.opener_message_id  # opener stays last
    assert (q.channel.id, q.id) in bot.world.deleted_messages

    # The resulting delete event must not wipe the escalated community ticket
    await flows.handle_question_deleted(cast(Any, bot), q.id)
    assert await tickets.get_ticket(community.id) is not None


async def test_escalate_refusals():
    bot = tickets_bot()
    cog = Tickets(cast(Any, bot))
    community, public_thread, op, _ = await community_question(bot)
    player = await escalate(bot, cog, op, public_thread)
    assert player.response.send_message.call_args.args[0] == C.errors.staff_only
    elsewhere = await escalate(bot, cog, make_user(staff=True), SimpleNamespace(id=5))
    assert (
        elsewhere.response.send_message.call_args.args[0]
        == C.tickets.not_community_thread
    )

    disabled_bot = FakeBot()
    disabled = await escalate(
        disabled_bot,
        Tickets(cast(Any, disabled_bot)),
        make_user(staff=True),
        public_thread,
    )
    assert (
        disabled.response.send_message.call_args.args[0] == C.tickets.tickets_disabled
    )
    assert await pt.escalation_of(community.id) is None


async def test_stale_close_ignores_private_tickets():
    from datetime import datetime
    from datetime import timedelta
    from datetime import UTC

    from nephthys.database.tables import Ticket
    from nephthys.tasks.close_stale import close_stale_tickets

    bot = tickets_bot(STALE_TICKET_DAYS="3")
    ticket, *_ = await create(bot)
    assert ticket
    await Ticket.update(
        {Ticket.last_msg_at: datetime.now(UTC) - timedelta(days=9)}
    ).where(Ticket.id == ticket.id)
    assert await close_stale_tickets(cast(Any, bot), delay=0) == 0
    assert (await fresh(ticket)).status == TicketStatus.OPEN
