"""The two-way sync, end to end: fake Discord <-> sync engine <-> Zammad.

Every test runs against the fake Zammad, and against a real one too when
configured (see conftest.py)."""

from typing import Any
from typing import cast

import pytest

from nephthys.cogs.community_help import CommunityHelp
from nephthys.cogs.ticket_commands import TicketCommands
from nephthys.cogs.tickets import Tickets
from nephthys.core import private_tickets as pt
from nephthys.core import tickets
from nephthys.core.copytext import DEFAULT_COPY as C
from nephthys.database.enums import TicketStatus
from nephthys.database.enums import UserType
from nephthys.database.tables import User
from nephthys.database.tables import ZammadOutbox
from nephthys.ui.ticket_buttons import CloseTicketButton
from nephthys.ui.ticket_buttons import CSATButton
from nephthys.zammad import outbox
from tests.fakes import make_interaction
from tests.fakes import make_message
from tests.fakes import make_user
from tests.zammad.conftest import AGENT_NAME
from tests.zammad.conftest import DEFAULT_AVATAR
from tests.zammad.conftest import new_player
from tests.zammad.conftest import PNG
from tests.zammad.conftest import PUBLIC_URL
from tests.zammad.conftest import World

SECRET_TEXT = "SECRET-internal-do-not-leak"


def fields(embed: Any) -> dict[str, str]:
    return {f.name: f.value for f in embed.fields}


def all_discord_text(world: World) -> str:
    """Everything the bot sent anywhere in Discord, as one string."""
    parts = []
    for messages in world.bot.world.channel_sends.values():
        for m in messages:
            parts.append(m.content or "")
            embed = getattr(m, "embed", None)
            if embed is not None:
                parts.append(f"{embed.title} {embed.description}")
                parts.extend(f"{f.name} {f.value}" for f in embed.fields)
    return "\n".join(parts)


async def reply(world: World, ticket: Any, body: str, **extra: Any) -> dict[str, Any]:
    """The agent writes an article in Zammad (public unless told otherwise)."""
    ticket = await world.fresh(ticket)
    return await world.agent.create_article(
        {
            "ticket_id": ticket.zammad_ticket_id,
            "body": body,
            "content_type": "text/html",
            "type": "note",
            "internal": False,
            **extra,
        }
    )


# ---------------------------------------------------------------------------
# Discord -> Zammad
# ---------------------------------------------------------------------------


async def test_leaf_category_creates_the_zammad_ticket(world: World):
    ticket, _thread, player = await world.open_ticket("store")
    ticket = await world.synced(ticket)

    z = await world.admin.get_ticket(ticket.zammad_ticket_id)
    assert z["title"] == f"T-{ticket.number} Store"
    assert z["group"] == "Store"
    assert z["priority"] == "2 normal"
    assert z["customer"] == f"discord:{player.id}"
    assert ticket.zammad_ticket_number == z["number"]
    assert ticket.zammad_group == "Store"

    customer = await world.admin.get_user(z["customer_id"])
    assert customer["login"] == f"discord:{player.id}"
    assert customer["email"] == f"{player.id}@discord.invalid"
    assert customer["firstname"] == "Alex"
    assert customer["discord_uid"] == str(player.id)
    opener = await tickets.get_opener(ticket)
    assert opener.zammad_user_id == customer["id"]

    [first] = await world.articles(ticket)
    assert (first["type"], first["internal"], first["sender"]) == (
        "note",
        False,
        "Customer",
    )
    assert first["body"].startswith("Alex (Player via Discord)")
    assert f"T-{ticket.number}" in first["body"]

    # The staff card links to the Zammad ticket with a button (first), then to
    # the thread; no Zammad text field, and the footer shows the T-number
    card = world.card(ticket)
    assert [(b.label, b.url) for b in card.view.children] == [
        (C.card.open_zammad_button, f"{PUBLIC_URL}/#ticket/zoom/{z['id']}"),
        (
            C.card.open_thread_button,
            f"https://discord.com/channels/100/{ticket.thread_id}",
        ),
    ]
    assert "Zammad" not in fields(card.embed)
    assert card.embed.footer.text == f"Ticket T-{ticket.number}"


async def test_no_zammad_ticket_before_a_leaf_is_reached(world: World):
    ticket, thread, player = await world.open_ticket(category=None)
    await world.say(thread, player, "hello? typed before picking anything")
    await world.drain()
    assert (await world.fresh(ticket)).zammad_ticket_id is None
    # Server Issue has subcategories: still not a leaf
    from nephthys.ui.ticket_buttons import CategoryButton
    from nephthys.ui.ticket_buttons import SubcategoryButton

    await CategoryButton(ticket.id, "server_issue").callback(
        make_interaction(world.bot, player)
    )
    await world.drain()
    assert (await world.fresh(ticket)).zammad_ticket_id is None
    assert (await outbox.stats()).pending == 0  # waiting by design isn't backlog

    await SubcategoryButton(ticket.id, "server_issue", "urgent").callback(
        make_interaction(world.bot, player)
    )
    ticket = await world.synced(ticket)
    z = await world.admin.get_ticket(ticket.zammad_ticket_id)
    assert z["title"] == f"T-{ticket.number} Server Issue (Urgent)"
    assert (z["group"], z["priority"]) == ("Server Issue", "3 high")
    # The early message was kept and mirrored once the ticket existed
    bodies = [a["body"] for a in await world.articles(ticket)]
    assert (
        bodies[1] == "Alex (Player via Discord)\n\nhello? typed before picking anything"
    )


async def test_thread_messages_become_public_note_articles(world: World):
    ticket, thread, player = await world.open_ticket("store")
    staff = make_user(staff=True, name="Sam")
    await world.say(thread, player, "my rank didn't arrive", files={"proof.png": PNG})
    await world.say(thread, staff, "looking into it")
    await world.say(thread, player, "thanks!")
    ticket = await world.synced(ticket)

    articles = await world.articles(ticket)
    assert [a["body"] for a in articles[1:]] == [
        "Alex (Player via Discord)\n\nmy rank didn't arrive",
        "Sam (Staff via Discord)\n\nlooking into it",
        "Alex (Player via Discord)\n\nthanks!",
    ]
    # Always public notes: never email-type, never internal
    assert {(a["type"], a["internal"]) for a in articles} == {("note", False)}
    assert [a["sender"] for a in articles[1:]] == ["Customer", "Agent", "Customer"]

    [attachment] = articles[1]["attachments"]
    assert attachment["filename"] == "proof.png"
    data = await world.admin.download_attachment(
        ticket.zammad_ticket_id, articles[1]["id"], attachment["id"]
    )
    assert data == PNG


async def test_one_customer_per_player_across_tickets(world: World):
    player = new_player("Robin")
    first, _, _ = await world.open_ticket("store", player=player)
    first = await world.synced(first)
    second, _, _ = await world.open_ticket("other", player=player)
    second = await world.synced(second)
    a = await world.admin.get_ticket(first.zammad_ticket_id)
    b = await world.admin.get_ticket(second.zammad_ticket_id)
    assert a["customer_id"] == b["customer_id"]
    assert b["group"] == "Other" and b["priority"] == "1 low"


async def test_existing_customer_is_found_not_duplicated(world: World):
    """The bot lost its record of the customer (e.g. a restored database)."""
    player = new_player("Robin")
    first, _, _ = await world.open_ticket("store", player=player)
    first = await world.synced(first)
    await User.update({User.zammad_user_id: None}).where(User.discord_id == player.id)
    second, _, _ = await world.open_ticket("other", player=player)
    second = await world.synced(second)
    a = await world.admin.get_ticket(first.zammad_ticket_id)
    b = await world.admin.get_ticket(second.zammad_ticket_id)
    assert a["customer_id"] == b["customer_id"]


async def test_escalated_ticket_includes_the_public_question(world: World):
    bot = world.bot
    asker = new_player("Quinn")
    question = make_message(bot, asker, "someone stole my diamonds at spawn, help")
    await CommunityHelp(cast(Any, bot)).on_message(question)
    community = await tickets.get_ticket_by_message(question.id)
    assert community is not None
    public = bot.world.threads[community.thread_id]
    mod = make_user(staff=True, name="Mod")
    cog = Tickets(cast(Any, bot))
    await cast(Any, Tickets.escalate).callback(
        cog, make_interaction(bot, mod, channel=public), False
    )
    private = await pt.escalation_of(community.id)
    assert private is not None
    from nephthys.ui.ticket_buttons import CategoryButton

    await CategoryButton(private.id, "reports").callback(make_interaction(bot, asker))
    private = await world.synced(private)
    [first] = await world.articles(private)
    assert (
        "Original public question:\nsomeone stole my diamonds at spawn, help"
        in first["body"]
    )
    assert f"/{community.thread_id}" in first["body"]
    assert (first["type"], first["internal"]) == ("note", False)


# ---------------------------------------------------------------------------
# Zammad -> Discord
# ---------------------------------------------------------------------------


async def test_agent_public_reply_is_posted_through_the_webhook(world: World):
    ticket, thread, player = await world.open_ticket("store")
    await world.say(thread, player, "my rank didn't arrive")
    ticket = await world.synced(ticket)

    article = await reply(
        world,
        ticket,
        '<div>Hi <b>Alex</b>,<br>fixed! See <a href="https://cloudsail.uk/store">the store</a>.</div>',
        attachments=[
            {
                "filename": "receipt.png",
                "data": __import__("base64").b64encode(PNG).decode(),
                "mime-type": "image/png",
            }
        ],
    )

    async def posted() -> Any:
        return world.replies(thread)

    [message] = await world.eventually(posted, "the agent's reply in Discord")
    assert (
        message.content
        == "Hi **Alex**,\nfixed! See the store (https://cloudsail.uk/store)."
    )
    # The agent's Zammad display name, not a Discord account
    assert message.username == AGENT_NAME
    assert message.avatar_url == DEFAULT_AVATAR  # this agent has no picture
    assert message.thread_id == thread.id
    # No embeds, no link previews, no pings
    assert not hasattr(message, "embed") and not hasattr(message, "embeds")
    assert message.suppress_embeds is True
    assert message.allowed_mentions.to_dict() == {"parse": []}
    # The attachment is forwarded as a file
    [file] = message.files
    assert file.filename == "receipt.png"
    assert file.fp.read() == PNG

    # The first public reply is the staff claim, shown with the agent's name
    ticket = await world.fresh(ticket)
    assert ticket.status == TicketStatus.IN_PROGRESS
    assert ticket.assigned_name == AGENT_NAME and ticket.assigned_to is None
    assert ticket.last_msg_by == UserType.HELPER
    assert fields(world.card(ticket).embed)[C.card.field_status] == (
        f"🔵 Claimed by {AGENT_NAME} (Zammad)"
    )

    # ...and it is never mirrored back to Zammad (no echo)
    before = len(await world.articles(ticket))
    await world.quiet()
    assert len(await world.articles(ticket)) == before
    assert len(world.replies(thread)) == 1
    mapped = await outbox.is_known_article(article["id"])
    assert mapped


async def test_agent_avatar_uses_the_public_zammad_address(world: World):
    """Zammad serves avatars at /api/v1/users/image/<hash>; Discord fetches
    them from ZAMMAD_PUBLIC_URL."""
    ticket, thread, _ = await world.open_ticket("store")
    ticket = await world.synced(ticket)
    payload = {
        "ticket": {"id": ticket.zammad_ticket_id},
        "article": {
            "id": 987_654_321,
            "ticket_id": ticket.zammad_ticket_id,
            "internal": False,
            "sender": "Agent",
            "type": "note",
            "content_type": "text/plain",
            "body": "hello",
            "created_by_id": 424_242,
            "created_by": {"firstname": "Bea", "lastname": "Agent", "image": "abc123"},
        },
    }
    assert "article" in await world.sync.handle_webhook(payload)
    await world.sync.drain()
    [message] = world.replies(thread)
    assert message.username == "Bea Agent"
    assert message.avatar_url == f"{PUBLIC_URL}/api/v1/users/image/abc123"


async def test_internal_notes_never_reach_discord(world: World):
    ticket, thread, player = await world.open_ticket("store")
    await world.say(thread, player, "hi")
    ticket = await world.synced(ticket)

    await reply(world, ticket, SECRET_TEXT, internal=True)
    # An internal note attached to a state change does fire the trigger
    await world.agent.update_ticket(
        ticket.zammad_ticket_id,
        {
            "state": "closed",
            "article": {
                "body": SECRET_TEXT + " (closing)",
                "type": "note",
                "internal": True,
            },
        },
    )

    async def closed() -> bool:
        return (await world.fresh(ticket)).status == TicketStatus.CLOSED

    await world.eventually(closed, "the Zammad close to reach Discord")
    await world.quiet()
    assert world.replies(thread) == []
    assert "SECRET" not in all_discord_text(world)
    rows = await ZammadOutbox.select().where(ZammadOutbox.kind == outbox.POST_ARTICLE)
    assert rows == []


async def test_staff_messages_from_discord_do_not_echo_back(world: World):
    """A staff message mirrored to Zammad is a public article by an agent-type
    sender: exactly what the trigger looks for. It must not come back."""
    ticket, thread, player = await world.open_ticket("store")
    staff = make_user(staff=True, name="Sam")
    await world.say(thread, player, "hi")
    await world.say(thread, staff, "hello from discord staff")
    ticket = await world.synced(ticket)
    await world.quiet()
    assert world.replies(thread) == []
    assert "hello from discord staff" not in all_discord_text(world)
    assert len(await world.articles(ticket)) == 3


async def test_messages_posted_for_zammad_are_not_mirrored_back(world: World):
    """Webhook messages and the bot's own fallback messages in the thread are
    ignored by the cog, so they never become articles."""
    ticket, thread, _player = await world.open_ticket("store")
    ticket = await world.synced(ticket)
    cog = Tickets(cast(Any, world.bot))
    via_webhook = make_message(
        world.bot, make_user(name=AGENT_NAME), "hi", channel=thread
    )
    via_webhook.webhook_id = 4242
    fallback = make_message(
        world.bot, make_user(bot=True), "**Bea (Support):** hi", channel=thread
    )
    await cog.on_message(via_webhook)
    await cog.on_message(fallback)
    await world.drain()
    assert len(await world.articles(ticket)) == 1
    assert await ZammadOutbox.count().where(ZammadOutbox.kind == outbox.ARTICLE) == 0


async def test_long_replies_are_split_and_never_double_posted(world: World):
    ticket, thread, _ = await world.open_ticket("store")
    ticket = await world.synced(ticket)
    text = " ".join(f"word{i}" for i in range(900))  # ~6000 characters
    await reply(world, ticket, text, content_type="text/plain")

    async def posted() -> bool:
        return len(world.replies(thread)) >= 3

    await world.eventually(posted, "the split reply")
    await world.quiet()
    parts = world.replies(thread)
    assert all(len(m.content) <= 2000 for m in parts)
    assert " ".join(m.content for m in parts) == text


# ---------------------------------------------------------------------------
# Lifecycle, both directions
# ---------------------------------------------------------------------------


async def test_close_in_zammad_runs_the_discord_close_and_csat(world: World):
    ticket, thread, player = await world.open_ticket("store")
    await world.say(thread, player, "hi")
    ticket = await world.synced(ticket)
    await world.agent.update_ticket(ticket.zammad_ticket_id, {"state": "closed"})

    async def closed() -> bool:
        return (await world.fresh(ticket)).status == TicketStatus.CLOSED

    await world.eventually(closed, "the close")
    ticket = await world.fresh(ticket)
    assert ticket.closed_by is None and ticket.closed_by_name == AGENT_NAME
    texts = [m.content for m in world.thread_posts(thread)]
    assert C.tickets.closed.format(user=f"**{AGENT_NAME}**") in texts
    assert C.tickets.csat_prompt in texts
    assert thread.locked is True
    status = fields(world.card(ticket).embed)[C.card.field_status]
    assert status.startswith(f"✅ Resolved by {AGENT_NAME} (Zammad)")

    # The close isn't sent back to Zammad
    await world.quiet()
    assert await ZammadOutbox.count().where(ZammadOutbox.kind == outbox.CLOSE) == 0
    assert (await world.fresh(ticket)).status == TicketStatus.CLOSED

    # The player's CSAT rating becomes an internal note in Zammad
    await CSATButton(ticket.id, 4).callback(make_interaction(world.bot, player))
    await world.drain()
    last = (await world.articles(ticket))[-1]
    assert last["internal"] is True and last["type"] == "note"
    assert "4/5" in last["body"]
    await world.quiet()
    assert "4/5" not in "".join(m.content or "" for m in world.replies(thread))


async def test_close_and_reopen_in_discord_sync_to_zammad(world: World):
    ticket, thread, player = await world.open_ticket("store")
    await world.say(thread, player, "hi")
    ticket = await world.synced(ticket)

    await CloseTicketButton(ticket.id).callback(make_interaction(world.bot, player))
    await world.drain()
    assert (await world.admin.get_ticket(ticket.zammad_ticket_id))["state"] == "closed"
    note = (await world.articles(ticket))[-1]
    assert note["internal"] is True and note["body"] == "Closed in Discord by Alex."
    # Zammad's echo of our own close changes nothing
    await world.quiet()
    assert (await world.fresh(ticket)).status == TicketStatus.CLOSED
    assert [m.content for m in world.thread_posts(thread)].count(
        C.tickets.csat_prompt
    ) == 1

    mod = make_user(staff=True, name="Mod")
    await cast(Any, TicketCommands.reopen).callback(
        TicketCommands(cast(Any, world.bot)),
        make_interaction(world.bot, mod, channel=thread),
    )
    await world.drain()
    assert (await world.admin.get_ticket(ticket.zammad_ticket_id))["state"] == "open"
    await world.quiet()
    ticket = await world.fresh(ticket)
    assert ticket.status == TicketStatus.OPEN and thread.locked is False


async def test_reopen_in_zammad_reopens_the_discord_ticket(world: World):
    ticket, thread, player = await world.open_ticket("store")
    ticket = await world.synced(ticket)
    await CloseTicketButton(ticket.id).callback(make_interaction(world.bot, player))
    await world.drain()
    await world.quiet()
    assert thread.locked is True

    await world.agent.update_ticket(ticket.zammad_ticket_id, {"state": "open"})

    async def reopened() -> bool:
        return (await world.fresh(ticket)).status != TicketStatus.CLOSED

    await world.eventually(reopened, "the re-open")
    assert thread.locked is False and thread.archived is False
    assert player.id in thread.added_users
    texts = [m.content for m in world.thread_posts(thread)]
    assert C.tickets.reopened.format(user=f"**{AGENT_NAME}**") in texts
    await world.quiet()
    assert await ZammadOutbox.count().where(ZammadOutbox.kind == outbox.REOPEN) == 0
    assert (await world.admin.get_ticket(ticket.zammad_ticket_id))["state"] == "open"


async def test_group_change_in_zammad_is_recorded(world: World):
    ticket, _thread, _ = await world.open_ticket("store")
    ticket = await world.synced(ticket)
    await world.agent.update_ticket(ticket.zammad_ticket_id, {"group": "Reports"})

    async def moved() -> bool:
        return (await world.fresh(ticket)).zammad_group == "Reports"

    await world.eventually(moved, "the group change")
    assert (await world.fresh(ticket)).status == TicketStatus.OPEN


async def test_tickets_created_directly_in_zammad_are_left_alone(world: World):
    ticket, _thread, _player = await world.open_ticket("store")
    ticket = await world.synced(ticket)
    z = await world.admin.get_ticket(ticket.zammad_ticket_id)
    # Same Discord customer, but the ticket was made in Zammad, not by the bot
    direct = await world.agent.create_ticket(
        {
            "title": "made in zammad",
            "group": "Store",
            "customer_id": z["customer_id"],
            "article": {
                "body": "a public agent article",
                "type": "note",
                "internal": False,
            },
        }
    )
    await world.agent.update_ticket(direct["id"], {"state": "closed"})
    await world.quiet()
    assert (
        await ZammadOutbox.count().where(ZammadOutbox.direction == outbox.TO_DISCORD)
        == 0
    )
    assert "a public agent article" not in all_discord_text(world)
    assert (await world.fresh(ticket)).status == TicketStatus.OPEN


@pytest.mark.parametrize("kind", ["forbidden", "send_fails"])
async def test_webhook_failure_falls_back_to_a_plain_bot_message(
    world: World, kind: str
):
    from unittest.mock import MagicMock

    import discord

    ticket, thread, _ = await world.open_ticket("store")
    ticket = await world.synced(ticket)
    if kind == "forbidden":  # the bot may not manage webhooks
        world.channel.webhooks_forbidden = True
    else:  # e.g. Discord refuses webhook posts in this (private) thread
        hook = await world.channel.create_webhook(name="x")
        hook.fail_with = discord.HTTPException(
            MagicMock(status=400, reason="Bad Request"),
            "Cannot execute action on this channel type",
        )
    await reply(world, ticket, "first <b>reply</b>")

    async def posted() -> Any:
        return [
            m for m in world.thread_posts(thread) if "(Support):" in (m.content or "")
        ]

    [message] = await world.eventually(posted, "the fallback message")
    assert message.content == f"**{AGENT_NAME} (Support):** first **reply**"
    assert not getattr(message, "via_webhook", False)
    assert message.suppress_embeds is True and not hasattr(message, "embed")
    assert (await world.fresh(ticket)).assigned_name == AGENT_NAME
    [logged] = [line for line in world.log() if "plain bot message" in line]
    assert f"T-{ticket.number}" in logged

    # A second fallback right after is posted too, but not logged again
    await reply(world, ticket, "second reply")

    async def two() -> bool:
        return len(await posted()) == 2

    await world.eventually(two, "the second fallback message")
    assert len([line for line in world.log() if "plain bot message" in line]) == 1


async def test_oversized_attachments_are_named_not_copied(backend: Any, db: None):
    """Both directions, with ZAMMAD_ATTACHMENT_MAX_MB below the file size. Also
    runs with a copy in which every template uses every placeholder it may,
    proving the code supplies them all."""
    import base64

    from nephthys.core.copytext import use_copy
    from tests.flows.test_copy_flows import every_placeholder_copy

    world = await World(backend, ZAMMAD_ATTACHMENT_MAX_MB="0.00001").start()
    try:
        use_copy(every_placeholder_copy())
        world.channel.webhooks_forbidden = True  # exercise the fallback text too
        ticket, thread, player = await world.open_ticket("store")
        await world.say(thread, player, "see file", files={"big.png": PNG})
        ticket = await world.synced(ticket)
        last = (await world.articles(ticket))[-1]
        assert last["attachments"] == []
        assert last["body"].endswith(
            "see file\n\n[Attachment not mirrored (too large): big.png]"
        )
        card = world.card(ticket)
        assert [b.label for b in card.view.children] == [
            "open_zammad_button:",
            "open_thread_button:",
        ]
        assert card.embed.footer.text == f"footer:{ticket.id}|T-{ticket.number}"

        await reply(
            world,
            ticket,
            "here you go",
            content_type="text/plain",
            attachments=[
                {
                    "filename": "huge.png",
                    "data": base64.b64encode(PNG).decode(),
                    "mime-type": "image/png",
                }
            ],
        )

        async def posted() -> Any:
            return [
                m
                for m in world.thread_posts(thread)
                if (m.content or "").startswith("fallback_reply:")
            ]

        [message] = await world.eventually(posted, "the fallback reply")
        assert message.content == (
            f"fallback_reply:{AGENT_NAME}|here you go\n\nattachment_skipped:huge.png"
        )
        assert not getattr(message, "files", None)
        status = fields(world.card(await world.fresh(ticket)).embed)["field_status:"]
        assert status == f"status_claimed:zammad_agent:{AGENT_NAME}|"
    finally:
        use_copy(C)
        await world.stop()
