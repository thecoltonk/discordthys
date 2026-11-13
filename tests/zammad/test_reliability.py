"""Outages, retries, ordering, idempotency and reporting.

Most of these need to break Zammad on purpose, so they use the fake only."""

from datetime import timedelta
from typing import Any
from typing import cast

import pytest

from nephthys.cogs.staff import Staff
from nephthys.core.copytext import DEFAULT_COPY as C
from nephthys.database.enums import TicketStatus
from nephthys.database.tables import ZammadMessageMap
from nephthys.database.tables import ZammadOutbox
from nephthys.ui.ticket_buttons import CloseTicketButton
from nephthys.zammad import outbox
from nephthys.zammad import sync as sync_module
from tests.fakes import make_interaction
from tests.fakes import make_user
from tests.zammad.conftest import AGENT_NAME
from tests.zammad.conftest import Backend
from tests.zammad.conftest import PNG
from tests.zammad.conftest import World
from tests.zammad.test_sync_flows import reply


def fake_of(world: World) -> Any:
    if world.backend.fake is None:
        pytest.skip("needs to break Zammad on purpose: fake only")
    return world.backend.fake


async def statuses(kind: str | None = None) -> list[tuple[str, str, int]]:
    rows = await ZammadOutbox.raw(
        "SELECT kind, status, attempts FROM zammad_outbox ORDER BY id"
    )
    return [
        (r["kind"], r["status"], r["attempts"])
        for r in rows
        if kind is None or r["kind"] == kind
    ]


async def test_discord_flows_never_wait_on_zammad(world: World):
    """With Zammad completely down, the whole Discord ticket flow still works;
    everything is delivered, in order, once Zammad is back."""
    fake = fake_of(world)
    fake.fail_next = 10_000
    ticket, thread, player = await world.open_ticket("store")
    staff = make_user(staff=True, name="Sam")
    await world.say(thread, player, "one")
    await world.say(thread, staff, "two")
    await world.say(thread, player, "three")
    await CloseTicketButton(ticket.id).callback(make_interaction(world.bot, player))
    await world.drain()

    # Discord side is complete: opener, staff card, claim, close + CSAT
    ticket = await world.fresh(ticket)
    assert ticket.status == TicketStatus.CLOSED
    assert ticket.staff_card_message_id is not None and ticket.assigned_to is not None
    assert C.tickets.csat_prompt in [m.content for m in world.thread_posts(thread)]
    assert ticket.zammad_ticket_id is None
    # Only the head of the queue was tried; the rest wait behind it, untouched
    assert await statuses() == [
        ("create_ticket", "pending", 1),
        ("article", "pending", 0),
        ("article", "pending", 0),
        ("article", "pending", 0),
        ("close", "pending", 0),
    ]
    # ...and it isn't retried before its backoff has passed
    await world.drain()
    assert (await statuses())[0] == ("create_ticket", "pending", 1)

    fake.fail_next = 0
    await world.make_due()
    ticket = await world.synced(ticket)
    assert {s for _, s, _ in await statuses()} == {"done"}
    bodies = [a["body"] for a in await world.articles(ticket)]
    assert [b.split("\n\n")[-1] for b in bodies[1:]] == [
        "one",
        "two",
        "three",
        "Closed in Discord by Alex.",
    ]
    assert (await world.admin.get_ticket(ticket.zammad_ticket_id))["state"] == "closed"


async def test_a_failing_message_holds_back_later_ones(world: World):
    """Per-ticket ordering: message 2 fails once, so message 3 must wait for
    it rather than overtake it. Another ticket is not held up."""
    fake = fake_of(world)
    ticket, thread, player = await world.open_ticket("store")
    other, other_thread, other_player = await world.open_ticket("other")
    ticket = await world.synced(ticket)
    await world.say(thread, player, "first")
    await world.drain()

    fake.fail_next = 1
    await world.say(thread, player, "second")
    await world.say(thread, player, "third")
    await world.say(other_thread, other_player, "elsewhere")
    await world.drain()
    assert [
        b["body"].split("\n\n")[-1] for b in (await world.articles(ticket))[1:]
    ] == ["first"]
    assert [b["body"].split("\n\n")[-1] for b in (await world.articles(other))[1:]] == [
        "elsewhere"
    ]

    await world.make_due()
    await world.drain()
    assert [
        b["body"].split("\n\n")[-1] for b in (await world.articles(ticket))[1:]
    ] == [
        "first",
        "second",
        "third",
    ]


async def test_lost_replies_never_create_duplicates(world: World):
    """Zammad does the write but the bot never sees the answer (timeout,
    restart). The retry must find what's there instead of writing it again."""
    fake = fake_of(world)
    fake.fail_after_commit = 1  # the customer is created, the reply is lost
    ticket, thread, player = await world.open_ticket("store")
    await world.drain()
    fake.fail_after_commit = 1  # now the ticket is created, the reply is lost
    await world.make_due()
    await world.drain()
    assert (await world.fresh(ticket)).zammad_ticket_id is None
    await world.make_due()
    ticket = await world.synced(ticket)

    fake.fail_after_commit = 1
    await world.say(thread, player, "only once please", files={"a.png": PNG})
    await world.drain()
    await world.make_due()
    await world.drain()

    assert (
        len([u for u in fake.users.values() if u["login"].startswith("discord:")]) == 1
    )
    assert len(fake.tickets) == 1
    bodies = [a["body"] for a in await world.articles(ticket)]
    assert len(bodies) == 2 and bodies[1].endswith("only once please")
    assert await ZammadMessageMap.count() == 1


async def test_repeated_webhook_deliveries_post_once(world: World):
    """Zammad retries a webhook it thinks failed; the same article must not be
    posted twice. Also covers delivery racing reconciliation."""
    ticket, thread, _ = await world.open_ticket("store")
    ticket = await world.synced(ticket)
    article = await reply(world, ticket, "hello once")

    async def posted() -> Any:
        return world.replies(thread)

    await world.eventually(posted, "the reply")
    full = await world.admin.get_ticket(ticket.zammad_ticket_id)
    payload = {
        "ticket": {"id": full["id"], "updated_by_id": article["created_by_id"]},
        "article": {**article, "created_by": {"firstname": "Bea", "lastname": "Agent"}},
    }
    for _ in range(3):
        assert "article" not in await world.sync.handle_webhook(payload)
    assert await world.sync.reconcile() == 0
    await world.quiet()
    assert [m.content for m in world.replies(thread)] == ["hello once"]


async def test_reply_missed_while_the_bot_was_down_is_recovered(world: World):
    """Zammad gives up on a webhook after 5 tries (about two minutes). The
    periodic reconcile picks up whatever was missed."""
    fake = fake_of(world)
    ticket, thread, _ = await world.open_ticket("store")
    ticket = await world.synced(ticket)

    assert world.runner is not None
    await world.runner.cleanup()  # the bot is "down": webhooks are refused
    world.runner = None
    await reply(world, ticket, "sent while the bot was down")
    await world.agent.update_ticket(ticket.zammad_ticket_id, {"state": "closed"})
    await fake.settle()
    assert all(d["statuses"] == [None] * 5 for d in fake.deliveries[-2:])
    await world.sync.drain()
    assert world.replies(thread) == []

    assert await world.sync.reconcile() == 2  # the article and the state change
    await world.sync.drain()
    [message] = world.replies(thread)
    assert message.content == "sent while the bot was down"
    assert message.username == AGENT_NAME
    ticket = await world.fresh(ticket)
    assert ticket.status == TicketStatus.CLOSED and ticket.assigned_name == AGENT_NAME
    assert await world.sync.reconcile() == 0  # nothing left, nothing repeated
    await world.sync.drain()
    assert len(world.replies(thread)) == 1


async def test_zammad_outage_delays_agent_attachments_but_loses_nothing(world: World):
    fake = fake_of(world)
    ticket, thread, _ = await world.open_ticket("store")
    ticket = await world.synced(ticket)
    import base64

    await reply(
        world,
        ticket,
        "see attached",
        attachments=[
            {
                "filename": "a.png",
                "data": base64.b64encode(PNG).decode(),
                "mime-type": "image/png",
            }
        ],
    )
    await fake.settle()
    fake.fail_next = 1  # the attachment download fails once
    await world.sync.run_once()
    assert world.replies(thread) == []  # not posted without its file
    await world.make_due()
    await world.sync.drain()
    [message] = world.replies(thread)
    assert [f.filename for f in message.files] == ["a.png"]


async def test_discord_close_wins_over_a_stale_zammad_state(world: World):
    """Closed in Discord while Zammad is unreachable: when Zammad (still
    'open') is looked at before the close got through, Discord isn't re-opened."""
    fake = fake_of(world)
    ticket, thread, player = await world.open_ticket("store")
    ticket = await world.synced(ticket)
    fake.fail_next = 1
    await CloseTicketButton(ticket.id).callback(make_interaction(world.bot, player))
    await world.sync.run_once()  # the close fails and waits for its retry
    await world.sync.queue_sync(ticket)
    await world.sync.reconcile()
    await world.sync.drain()
    assert (await world.fresh(ticket)).status == TicketStatus.CLOSED
    await world.make_due()
    await world.drain()
    assert (await world.admin.get_ticket(ticket.zammad_ticket_id))["state"] == "closed"
    assert (await world.fresh(ticket)).status == TicketStatus.CLOSED


async def test_permanent_failures_are_given_up_and_reported(world: World):
    """A message Zammad rejects outright (HTTP 422) is retried a few times,
    then marked failed and stepped over, so later messages still arrive. It
    shows up in the log channel and /stats, and `zammad-retry` re-queues it."""
    fake = fake_of(world)
    ticket, thread, player = await world.open_ticket("store")
    ticket = await world.synced(ticket)
    await world.say(thread, player, "this one is rejected")
    # Make Zammad reject it: an article type that doesn't exist
    to_zammad_article = fake.add_article

    def rejecting(request: Any, data: dict[str, Any], **kwargs: Any) -> Any:
        if "this one is rejected" in data.get("body", ""):
            data = {**data, "type": "carrier pigeon"}
        return to_zammad_article(request, data, **kwargs)

    fake.add_article = rejecting
    for _ in range(outbox.PERMANENT_ATTEMPTS):
        await world.make_due()
        await world.sync.run_once()
    assert await statuses("article") == [("article", "failed", 3)]

    await world.say(thread, player, "this one is fine")
    await world.drain()
    assert (await world.articles(ticket))[-1]["body"].endswith("this one is fine")

    stats = await outbox.stats()
    assert (stats.pending, stats.failed) == (0, 1)
    assert "422" in (stats.last_error or "")
    await world.sync.report_health()
    await world.sync.report_health()
    alerts = [line for line in world.log() if "gave up" in line]
    assert len(alerts) == 1 and "zammad-retry" in alerts[0]  # reported once

    fake.add_article = to_zammad_article
    assert await outbox.retry_failed() == 1
    await world.drain()
    assert (await outbox.stats()).healthy
    assert (await world.articles(ticket))[-1]["body"].endswith("this one is rejected")


async def test_backlog_is_reported_once_and_recovery_is_announced(
    world: World, monkeypatch: pytest.MonkeyPatch
):
    fake = fake_of(world)
    fake.fail_next = 10_000
    ticket, thread, player = await world.open_ticket("store")
    await world.sync.run_once()
    await world.sync.report_health()
    assert not [line for line in world.log() if "behind" in line]  # too fresh to matter

    await ZammadOutbox.raw(
        "UPDATE zammad_outbox SET created_at = now() - interval '20 minutes'"
    )
    for _ in range(3):
        await world.sync.report_health()
    [alert] = [line for line in world.log() if "behind" in line]
    assert "1 item(s) queued" in alert and "20 min" in alert and "500" in alert

    # /stats shows the backlog on the #tickets embed
    stats_text = sync_stats_field(await run_stats(world))
    assert stats_text.startswith("⚠️ 1 queued (oldest 20 min) · 0 failed")

    fake.fail_next = 0
    await world.make_due()
    await world.drain()
    await world.sync.report_health()
    assert world.log()[-1] == "✅ Zammad sync has caught up."
    assert sync_stats_field(await run_stats(world)) == C.stats.zammad_ok

    # A later backlog is reported again (the rate limit was reset on recovery)
    assert sync_module.ALERT_INTERVAL_SECONDS >= 600


async def run_stats(world: World) -> Any:
    staff = make_user(staff=True)
    interaction = make_interaction(world.bot, staff)
    await cast(Any, Staff.stats).callback(Staff(cast(Any, world.bot)), interaction, 7)
    return interaction.followup.send.call_args.kwargs["embeds"]


def sync_stats_field(embeds: list[Any]) -> str:
    private = embeds[1]
    return {f.name: f.value for f in private.fields}[C.stats.field_zammad]


async def test_alerts_are_rate_limited(world: World):
    assert await world.sync.alert("topic", "first") is True
    assert await world.sync.alert("topic", "second") is False
    assert await world.sync.alert("other-topic", "third") is True
    assert world.log() == ["first", "third"]


async def test_backoff_grows_and_is_capped():
    delays = [outbox.backoff_seconds(n, jitter=False) for n in range(1, 12)]
    assert delays[:5] == [5, 10, 20, 40, 80]
    assert delays == sorted(delays) and delays[-1] == outbox.BACKOFF_MAX_SECONDS
    assert all(
        d <= outbox.backoff_seconds(n) <= d * 1.2 + 1e-9
        for n, d in enumerate(delays, start=1)
    )


async def test_retries_give_up_after_the_configured_attempts(
    backend: Backend, db: None
):
    if backend.fake is None:
        pytest.skip("fake only")
    world = await World(backend, ZAMMAD_OUTBOX_MAX_ATTEMPTS="4").start()
    try:
        backend.fake.fail_next = 10_000
        ticket, _, _ = await world.open_ticket("store")
        for expected in (1, 2, 3):
            await world.make_due()
            await world.sync.run_once()
            assert await statuses() == [("create_ticket", "pending", expected)]
            [row] = await ZammadOutbox.objects()
            wait = row.next_attempt_at - outbox.utcnow()
            base = outbox.backoff_seconds(expected, jitter=False)
            assert (
                timedelta(seconds=base - 2) < wait < timedelta(seconds=base * 1.2 + 2)
            )
        await world.make_due()
        await world.sync.run_once()
        assert await statuses() == [("create_ticket", "failed", 4)]
    finally:
        await world.stop()


async def test_sync_is_off_without_url_and_token(db: None):
    """Integration off: nothing is queued, nothing breaks."""
    from tests.fakes import FakeBot
    from tests.fakes import make_text_channel
    from tests.helpers import make_settings
    from nephthys.core import tickets
    from nephthys.ui.ticket_buttons import CategoryButton
    from nephthys.ui.ticket_buttons import CreateTicketButton
    from nephthys.cogs.tickets import Tickets
    from tests.fakes import make_message

    for env in (
        {},
        {"ZAMMAD_URL": "http://zammad-nginx:8080"},
        {"ZAMMAD_TOKEN": "x" * 20},
    ):
        bot = FakeBot(make_settings(TICKETS_CHANNEL_ID="600", **env))
        assert bot.settings.zammad_enabled is False
        channel = make_text_channel(bot, 600)
        player = make_user(name="Alex")
        await CreateTicketButton().callback(make_interaction(bot, player))
        thread, _ = channel.created_threads[-1]
        ticket = await tickets.get_ticket_by_thread(thread.id)
        assert ticket is not None
        await CategoryButton(ticket.id, "store").callback(make_interaction(bot, player))
        await Tickets(cast(Any, bot)).on_message(
            make_message(bot, player, "hi", channel=thread)
        )
        await CloseTicketButton(ticket.id).callback(make_interaction(bot, player))
        assert await ZammadOutbox.count() == 0
