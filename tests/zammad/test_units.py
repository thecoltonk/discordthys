"""Signature verification, the webhook endpoint, text conversion, config and
the migration."""

import json
from typing import Any
from typing import cast

import pytest
from aiohttp.test_utils import TestClient
from aiohttp.test_utils import TestServer

from nephthys.config import ConfigError
from nephthys.core.permissions import Actor
from nephthys.core.ticket_categories import DEFAULT_TICKET_CATEGORIES
from nephthys.core.ticket_categories import parse_ticket_categories
from nephthys.core.ticket_categories import TicketCategoriesError
from nephthys.database.raw_migration import postgresql_code_block
from nephthys.database.tables import Ticket
from nephthys.database.tables import ZammadOutbox
from nephthys.piccolo_migrations import cloudsail_005_zammad
from nephthys.web.health import build_app
from nephthys.web.health import ZAMMAD_WEBHOOK_PATH
from nephthys.zammad import outbox
from nephthys.zammad.text import article_text
from nephthys.zammad.text import html_to_text
from nephthys.zammad.text import is_marker
from nephthys.zammad.text import marker
from nephthys.zammad.text import mirrored_body
from nephthys.zammad.text import sign
from nephthys.zammad.text import split_message
from nephthys.zammad.text import verify_signature
from nephthys.zammad.text import webhook_username
from tests.factories import new_ticket
from tests.fakes import FakeBot
from tests.helpers import make_settings

SECRET = "test-webhook-secret-0123456789"

# ---------------------------------------------------------------------------
# Signature
# ---------------------------------------------------------------------------


def test_signature_matches_a_real_zammad_delivery():
    """Captured from Zammad 7.2.3: X-Hub-Signature is `sha1=` + the hex
    HMAC-SHA1 of the raw request body, keyed with the webhook's token."""
    body = b'{"ticket":{"id":2},"article":null}'
    assert (
        sign("s3cret", body)
        == "sha1=" + __import__("hmac").new(b"s3cret", body, "sha1").hexdigest()
    )
    assert verify_signature("s3cret", body, sign("s3cret", body))
    assert verify_signature("s3cret", body, sign("s3cret", body).upper())


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "sha1=",
        "sha1=" + "0" * 40,
        "sha256=abc",
        sign("another-secret", b'{"a":1}'),
        sign(SECRET, b'{"a":2}'),  # signature of a different body
        sign(SECRET, b'{"a":1}')[5:],  # missing the sha1= prefix
    ],
)
def test_bad_signatures_are_rejected(header: str | None):
    assert verify_signature(SECRET, b'{"a":1}', header) is False


def test_an_empty_secret_never_verifies():
    assert verify_signature("", b"x", sign("", b"x")) is False


# ---------------------------------------------------------------------------
# The webhook endpoint
# ---------------------------------------------------------------------------


class RecordingSync:
    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []
        self.error: Exception | None = None

    async def handle_webhook(self, payload: dict[str, Any]) -> str:
        if self.error is not None:
            raise self.error
        self.payloads.append(payload)
        return "queued: article"


async def endpoint(sync: Any = None, **env: str) -> tuple[TestClient, FakeBot]:
    bot = FakeBot(
        make_settings(
            TICKETS_CHANNEL_ID="600",
            ZAMMAD_URL="http://zammad",
            ZAMMAD_TOKEN="token",
            ZAMMAD_WEBHOOK_SECRET=SECRET,
            **env,
        )
    )
    bot.zammad = sync
    client = TestClient(TestServer(build_app(cast(Any, bot))))
    await client.start_server()
    return client, bot


async def test_webhook_accepts_only_correctly_signed_calls():
    sync = RecordingSync()
    client, _ = await endpoint(sync)
    try:
        body = json.dumps({"ticket": {"id": 1}, "article": None}).encode()
        headers = {"Content-Type": "application/json"}

        unsigned = await client.post(ZAMMAD_WEBHOOK_PATH, data=body, headers=headers)
        assert unsigned.status == 401
        wrong = await client.post(
            ZAMMAD_WEBHOOK_PATH,
            data=body,
            headers={
                **headers,
                "X-Hub-Signature": sign("wrong-secret-wrong-secret", body),
            },
        )
        assert wrong.status == 401
        tampered = await client.post(
            ZAMMAD_WEBHOOK_PATH,
            data=body.replace(b"1", b"2"),
            headers={**headers, "X-Hub-Signature": sign(SECRET, body)},
        )
        assert tampered.status == 401
        assert sync.payloads == []  # nothing unsigned ever reaches the sync

        good = await client.post(
            ZAMMAD_WEBHOOK_PATH,
            data=body,
            headers={**headers, "X-Hub-Signature": sign(SECRET, body)},
        )
        assert good.status == 200
        assert await good.json() == {"ok": True, "result": "queued: article"}
        assert sync.payloads == [{"ticket": {"id": 1}, "article": None}]

        for junk in (b"not json", b"[1, 2]"):
            bad = await client.post(
                ZAMMAD_WEBHOOK_PATH,
                data=junk,
                headers={"X-Hub-Signature": sign(SECRET, junk)},
            )
            assert bad.status == 400

        # A failure inside answers 5xx, so Zammad retries the delivery
        sync.error = RuntimeError("database is down")
        failed = await client.post(
            ZAMMAD_WEBHOOK_PATH,
            data=body,
            headers={"X-Hub-Signature": sign(SECRET, body)},
        )
        assert failed.status == 503
        assert (await client.get(ZAMMAD_WEBHOOK_PATH)).status == 405
    finally:
        await client.close()


async def test_webhook_endpoint_is_closed_while_the_sync_is_off():
    client, _ = await endpoint(None)
    try:
        body = b"{}"
        response = await client.post(
            ZAMMAD_WEBHOOK_PATH,
            data=body,
            headers={"X-Hub-Signature": sign(SECRET, body)},
        )
        assert response.status == 404
    finally:
        await client.close()


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------


def test_html_articles_become_discord_text():
    html = (
        "<div>Hi <b>Alex</b>,<br>your <i>rank</i> is fixed &amp; active.</div>"
        "<ul><li>one</li><li>two</li></ul>"
        "<blockquote>earlier<br>message</blockquote>"
        '<p>See <a href="https://cloudsail.uk/store" rel="nofollow">the store</a> or '
        '<a href="https://cloudsail.uk">https://cloudsail.uk</a>.</p>'
        "<script>alert(1)</script><style>p{}</style>"
    )
    assert html_to_text(html) == (
        "Hi **Alex**,\nyour *rank* is fixed & active.\n\n• one\n• two\n\n"
        "> earlier\n> message\n\n"
        "See the store (https://cloudsail.uk/store) or https://cloudsail.uk."
    )
    assert article_text("plain &amp; simple", "text/plain") == "plain & simple"
    assert article_text("a < b", "text/plain") == "a < b"
    assert article_text(None, None) == ""


def test_split_message_respects_the_discord_limit():
    assert split_message("") == []
    assert split_message("short") == ["short"]
    text = "\n\n".join(f"paragraph {i} " + "x" * 400 for i in range(12))
    parts = split_message(text)
    assert len(parts) > 1 and all(len(p) <= 1900 for p in parts)
    assert "\n\n".join(parts) == text  # cut on paragraph boundaries
    assert [len(p) for p in split_message("y" * 4000)] == [1900, 1900, 200]


def test_webhook_usernames_are_made_acceptable_to_discord():
    assert webhook_username("Bea Agent", "Support") == "Bea Agent"
    assert webhook_username("Discord Dave", "Support") == "Dave"
    assert webhook_username("clyde", "Support") == "Support"
    assert webhook_username("  ", "Support") == "Support"
    assert webhook_username("everyone", "Support") == "Support"
    assert len(webhook_username("x" * 200, "Support")) == 80


def test_mirrored_article_bodies_and_markers():
    assert (
        mirrored_body("Alex", "Player", " hi ", []) == "Alex (Player via Discord)\n\nhi"
    )
    assert mirrored_body("Sam", "Staff", "", ["[Attachment not mirrored: a.zip]"]) == (
        "Sam (Staff via Discord)\n\n[Attachment not mirrored: a.zip]"
    )
    assert is_marker(marker("msg", 123))
    assert not is_marker("<abc@mail.example.com>") and not is_marker(None)


# ---------------------------------------------------------------------------
# Config and categories
# ---------------------------------------------------------------------------


def test_sync_needs_url_token_secret_and_tickets():
    base = {"TICKETS_CHANNEL_ID": "600"}
    assert make_settings(**base).zammad_enabled is False
    assert (
        make_settings(**base, ZAMMAD_URL="http://zammad-nginx:8080").zammad_enabled
        is False
    )
    assert make_settings(**base, ZAMMAD_TOKEN="t").zammad_enabled is False
    on = make_settings(
        **base,
        ZAMMAD_URL="http://zammad-nginx:8080/",
        ZAMMAD_TOKEN="t",
        ZAMMAD_WEBHOOK_SECRET=SECRET,
    )
    assert on.zammad_enabled and on.zammad_url == "http://zammad-nginx:8080"
    assert on.zammad_webhook_url == "http://cloudsail-support-bot:3000/zammad/webhook"

    with pytest.raises(ConfigError) as missing:
        make_settings(ZAMMAD_URL="http://z", ZAMMAD_TOKEN="t")
    assert "TICKETS_CHANNEL_ID" in str(missing.value)
    assert "ZAMMAD_WEBHOOK_SECRET" in str(missing.value)
    with pytest.raises(ConfigError, match="at least 16"):
        make_settings(
            **base,
            ZAMMAD_URL="http://z",
            ZAMMAD_TOKEN="t",
            ZAMMAD_WEBHOOK_SECRET="short",
        )
    with pytest.raises(ConfigError, match="http://"):
        make_settings(**base, ZAMMAD_URL="zammad-nginx:8080")


def test_category_to_group_and_priority_mapping():
    assert DEFAULT_TICKET_CATEGORIES.zammad_groups == (
        "Server Issue",
        "Store",
        "Bans & Appeals",
        "Reports",
        "Other",
    )
    assert DEFAULT_TICKET_CATEGORIES.zammad.priority("high") == "3 high"
    custom = parse_ticket_categories(
        '[zammad.priorities]\nlow = "low"\nmedium = "mid"\nhigh = "URGENT"\n'
        '[[categories]]\nkey = "store"\nlabel = "Store"\nblurb = "x"\n'
        'zammad_group = "Billing::Store"\n'
        '[[categories]]\nkey = "other"\nlabel = "Other"\nblurb = "x"\n'
    )
    assert custom.zammad_groups == ("Billing::Store", "Other")
    assert custom.zammad.priority("high") == "URGENT"
    with pytest.raises(TicketCategoriesError, match="missing: high"):
        parse_ticket_categories(
            '[zammad.priorities]\nlow = "a"\nmedium = "b"\n'
            '[[categories]]\nkey = "o"\nlabel = "O"\nblurb = "x"\n'
        )


# ---------------------------------------------------------------------------
# Migration and queue rules
# ---------------------------------------------------------------------------


async def columns(table: str) -> set[str]:
    rows = await Ticket.raw(
        "SELECT column_name FROM information_schema.columns WHERE table_name = {}",
        table,
    )
    return {r["column_name"] for r in rows}


async def test_migration_runs_forwards_and_backwards(db: None):
    added = {
        "zammad_ticket_id",
        "zammad_ticket_number",
        "zammad_group",
        "assigned_name",
        "closed_by_name",
    }
    ticket = await new_ticket()
    assert added <= await columns("ticket")
    assert "zammad_user_id" in await columns("app_user")
    try:
        await Ticket.raw(postgresql_code_block(cloudsail_005_zammad.BACKWARDS))
        assert not added & await columns("ticket")
        assert "zammad_user_id" not in await columns("app_user")
        assert await columns("zammad_outbox") == set()
        assert await columns("zammad_message_map") == set()
        # Existing data survives going back...
        rows = await Ticket.raw("SELECT id FROM ticket")
        assert [r["id"] for r in rows] == [ticket.id]
    finally:
        await Ticket.raw("DROP TABLE IF EXISTS zammad_message_map, zammad_outbox")
        await Ticket.raw(postgresql_code_block(cloudsail_005_zammad.BACKWARDS))
        await Ticket.raw(postgresql_code_block(cloudsail_005_zammad.FORWARDS))
    # ...and forwards again
    assert added <= await columns("ticket")
    refreshed = await Ticket.objects().where(Ticket.id == ticket.id).first()
    assert refreshed is not None and refreshed.zammad_ticket_id is None


async def test_schema_rules(db: None):
    a, b = await new_ticket(), await new_ticket()
    await Ticket.update({Ticket.zammad_ticket_id: 7}).where(Ticket.id == a.id)
    with pytest.raises(Exception, match="unique|duplicate"):
        await Ticket.update({Ticket.zammad_ticket_id: 7}).where(Ticket.id == b.id)
    with pytest.raises(Exception, match="check"):
        await outbox.enqueue(a.id, "sideways", outbox.ARTICLE)

    # The same event queued twice is one row
    assert await outbox.enqueue(a.id, outbox.TO_ZAMMAD, outbox.ARTICLE, dedupe_key="k")
    assert not await outbox.enqueue(
        a.id, outbox.TO_ZAMMAD, outbox.ARTICLE, dedupe_key="k"
    )
    # One Zammad article per Discord message
    await outbox.record_mirror(
        a.id, origin="discord", discord_message_id=1, zammad_article_id=10
    )
    await outbox.record_mirror(
        a.id, origin="discord", discord_message_id=1, zammad_article_id=11
    )
    await outbox.record_mirror(
        a.id, origin="discord", discord_message_id=2, zammad_article_id=10
    )
    rows = await Ticket.raw(
        "SELECT discord_message_id, zammad_article_id FROM zammad_message_map"
    )
    assert [(r["discord_message_id"], r["zammad_article_id"]) for r in rows] == [
        (1, 10)
    ]
    # Deleting a ticket takes its queue and map with it
    await Ticket.delete().where(Ticket.id == a.id)
    assert await ZammadOutbox.count() == 0
    assert (await Ticket.raw("SELECT count(*) AS n FROM zammad_message_map"))[0][
        "n"
    ] == 0


async def test_queue_order_per_ticket_and_direction(db: None):
    a, b = await new_ticket(), await new_ticket()
    for kind in (outbox.ARTICLE, outbox.ARTICLE, outbox.CREATE_TICKET, outbox.CLOSE):
        await outbox.enqueue(a.id, outbox.TO_ZAMMAD, kind)
    await outbox.enqueue(a.id, outbox.TO_DISCORD, outbox.SYNC_TICKET)
    await outbox.enqueue(
        b.id, outbox.TO_ZAMMAD, outbox.ARTICLE
    )  # b has no Zammad ticket

    def heads(rows: list[outbox.Row]) -> list[tuple[int, str, str]]:
        return [(r.ticket_id, r.direction, r.kind) for r in rows]

    # One row per queue; create_ticket jumps ahead; b waits for its ticket
    due = await outbox.due_rows()
    assert sorted(heads(due)) == [
        (a.id, outbox.TO_DISCORD, outbox.SYNC_TICKET),
        (a.id, outbox.TO_ZAMMAD, outbox.CREATE_TICKET),
    ]
    create = next(r for r in due if r.kind == outbox.CREATE_TICKET)

    # While the head is backing off, nothing behind it runs
    assert not await outbox.mark_error(create, "boom", permanent=False, max_attempts=5)
    assert heads(await outbox.due_rows()) == [
        (a.id, outbox.TO_DISCORD, outbox.SYNC_TICKET)
    ]

    await outbox.mark_done(create.id)
    await Ticket.update({Ticket.zammad_ticket_id: 1}).where(Ticket.id == a.id)
    first = next(r for r in await outbox.due_rows() if r.direction == outbox.TO_ZAMMAD)
    assert first.kind == outbox.ARTICLE and first.id == min(
        r["id"]
        for r in await ZammadOutbox.select().where(ZammadOutbox.kind == outbox.ARTICLE)
    )
    # A row that's given up on is stepped over
    gave_up = False
    for _ in range(outbox.PERMANENT_ATTEMPTS):
        gave_up = await outbox.mark_error(first, "422", permanent=True, max_attempts=25)
        first.attempts += 1
    assert gave_up
    await ZammadOutbox.raw("UPDATE zammad_outbox SET next_attempt_at = now()")
    nxt = next(r for r in await outbox.due_rows() if r.direction == outbox.TO_ZAMMAD)
    assert nxt.kind == outbox.ARTICLE and nxt.id > first.id

    stats = await outbox.stats()
    assert (stats.pending, stats.failed) == (3, 1)  # b's waiting message isn't backlog
    await outbox.drop_unstarted(b.id)
    await outbox.drop_unstarted(a.id)  # a is in Zammad: nothing is dropped
    assert await ZammadOutbox.count().where(ZammadOutbox.ticket == b.id) == 0
    assert (await outbox.stats()).pending == 3


def test_agent_display_name_template_and_80_char_limit():
    from nephthys.core.copytext import DEFAULT_COPY
    from nephthys.zammad.text import agent_username

    template = DEFAULT_COPY.zammad.agent_name
    assert template == "{name} (Support)"
    assert agent_username(template, "Bea Agent", "Support") == "Bea Agent (Support)"
    # Too long: the name is shortened, the suffix survives, 80 characters max
    long = agent_username(template, "Bartholomew " * 10, "Support")
    assert len(long) == 80 and long.endswith("… (Support)")
    # Reserved words are removed; nothing usable left -> the fallback alone
    assert agent_username(template, "Discord Dave", "Support") == "Dave (Support)"
    assert agent_username(template, "", "Support") == "Support"
    assert agent_username(template, "clyde", "Support") == "Support"
    # A custom template from copy
    assert agent_username("{name} · Cloudsail", "Bea", "Support") == "Bea · Cloudsail"


async def test_migration_006_round_trip(db: None):
    from nephthys.core.users import upsert_user
    from nephthys.piccolo_migrations import cloudsail_006_zammad_profiles as m006

    added = {
        "avatar_key",
        "avatar_url",
        "zammad_name",
        "zammad_avatar_key",
        "zammad_avatar_at",
    }
    assert added <= await columns("app_user")
    user = await upsert_user(Actor(discord_id=1, username="a", is_staff=False))
    try:
        await Ticket.raw(postgresql_code_block(m006.BACKWARDS))
        assert not added & await columns("app_user")
        rows = await Ticket.raw("SELECT id FROM app_user")
        assert [r["id"] for r in rows] == [user.id]
    finally:
        await Ticket.raw(postgresql_code_block(m006.BACKWARDS))
        await Ticket.raw(postgresql_code_block(m006.FORWARDS))
    assert added <= await columns("app_user")


async def test_upsert_keeps_the_last_known_avatar(db: None):
    from nephthys.core.users import upsert_user

    with_avatar = Actor(
        discord_id=7,
        username="p",
        is_staff=False,
        avatar_key="abc",
        avatar_url="https://cdn.discordapp.com/avatars/7/abc.png?size=256",
    )
    user = await upsert_user(with_avatar)
    assert (user.avatar_key, user.avatar_url) == (
        with_avatar.avatar_key,
        with_avatar.avatar_url,
    )
    # An actor without avatar info (e.g. a Zammad-side action) doesn't erase it
    user = await upsert_user(Actor(discord_id=7, username="p2", is_staff=False))
    assert user.username == "p2" and user.avatar_key == "abc"


def test_avatar_is_read_from_the_member_as_png():
    from unittest.mock import MagicMock

    import discord

    from nephthys.discord_ops.threads import actor_from_user
    from tests.fakes import make_member
    from tests.helpers import make_settings

    settings = make_settings()
    member = make_member(settings, user_id=5)
    actor = actor_from_user(member, settings.staff_role_id)
    # No custom avatar: Discord's default one
    assert actor.avatar_url and "/embed/avatars/" in actor.avatar_url
    animated = MagicMock(spec=discord.Member)
    animated.id, animated.display_name, animated.roles = 5, "x", []
    animated.display_avatar = discord.Asset._from_avatar(MagicMock(), 5, "a_hash")
    actor = actor_from_user(animated, settings.staff_role_id)
    assert actor.avatar_key == "a_hash"
    assert (
        actor.avatar_url == "https://cdn.discordapp.com/avatars/5/a_hash.png?size=256"
    )
