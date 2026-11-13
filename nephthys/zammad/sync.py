"""The Zammad sync engine: the outbox worker, webhook intake, reconciliation.

    Discord flow ──► hooks.py ──► zammad_outbox (to_zammad) ──┐
                                                               ├─► worker ─► Zammad / Discord
    Zammad trigger ─► POST /zammad/webhook ─► ingest ─► zammad_outbox (to_discord) ┘

Both directions go through the same table and the same worker, so both get
retries with backoff, per-ticket ordering and idempotency.
"""

import asyncio
import logging
import time
from datetime import timedelta
from typing import Any
from typing import TYPE_CHECKING

import aiohttp
import discord

from nephthys.core import tickets
from nephthys.database.enums import TicketStatus
from nephthys.database.tables import Ticket
from nephthys.zammad import outbox
from nephthys.zammad import to_discord
from nephthys.zammad import to_zammad
from nephthys.zammad.client import ZammadClient
from nephthys.zammad.client import ZammadError
from nephthys.zammad.outbox import Row
from nephthys.zammad.text import display_name
from nephthys.zammad.text import is_marker
from nephthys.zammad.to_zammad import is_closed_state

if TYPE_CHECKING:
    from nephthys.bot import SupportBot

log = logging.getLogger(__name__)

IDLE_POLL_SECONDS = 15.0
# Log-channel alerts: at most one per topic per this many seconds
ALERT_INTERVAL_SECONDS = 1800.0
# A backlog older than this is reported
BACKLOG_ALERT_SECONDS = 600.0
RECONCILE_CLOSED_FOR = timedelta(days=2)


class ZammadSync:
    def __init__(self, bot: "SupportBot", client: ZammadClient | None = None):
        settings = bot.settings
        assert settings.zammad_url and settings.zammad_token
        self.bot = bot
        self.client = client or ZammadClient(
            settings.zammad_url,
            settings.zammad_token,
            timeout=settings.zammad_timeout_seconds,
        )
        # Sets customers' avatars (needs admin.user); None = avatars are off
        self.avatar_client: ZammadClient | None = (
            ZammadClient(
                settings.zammad_url,
                settings.zammad_avatar_token,
                timeout=settings.zammad_timeout_seconds,
            )
            if settings.zammad_avatar_token
            else None
        )
        # Why avatars stopped for this run (e.g. the token lacks admin.user)
        self.avatars_off_reason: str | None = None
        self._http: aiohttp.ClientSession | None = None
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._webhook: Any = None
        self.webhook_error: str | None = None
        self._alerted: dict[str, float] = {}
        self._reported_failed = 0
        self._backlog_reported = False

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run_forever(), name="zammad-sync")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        await self.client.close()
        if self.avatar_client is not None:
            await self.avatar_client.close()
        if self._http is not None and not self._http.closed:
            await self._http.close()

    def wake(self) -> None:
        self._wake.set()

    def http(self) -> aiohttp.ClientSession:
        """Plain session for downloading Discord attachments."""
        if self._http is None or self._http.closed:
            self._http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60))
        return self._http

    # -- the worker ------------------------------------------------------------

    async def run_forever(self) -> None:
        await self.bot.wait_until_ready()
        while True:
            try:
                worked = await self.run_once()
                await self.report_health()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Zammad sync pass failed")
                worked = 0
            if worked:
                continue  # more rows may be due right behind these
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=IDLE_POLL_SECONDS)
            except TimeoutError:
                pass

    async def run_once(self) -> int:
        """Work every queue's next due row once. Returns how many succeeded."""
        succeeded = 0
        for row in await outbox.due_rows():
            if await self.process(row):
                succeeded += 1
        return succeeded

    async def drain(self, rounds: int = 50) -> None:
        """Run until nothing is due (tests, and `zammad-retry`)."""
        for _ in range(rounds):
            if not await self.run_once():
                return

    async def process(self, row: Row) -> bool:
        handlers = (
            to_zammad.HANDLERS
            if row.direction == outbox.TO_ZAMMAD
            else to_discord.HANDLERS
        )
        handler = handlers.get(row.kind)
        ticket = await tickets.get_ticket(row.ticket_id)
        try:
            if handler is None:
                raise to_discord.GiveUp(f"unknown outbox kind {row.kind!r}")
            if ticket is None:
                raise to_discord.GiveUp("the ticket no longer exists")
            await handler(self, row, ticket)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            permanent = isinstance(e, to_discord.GiveUp | discord.NotFound) or (
                isinstance(e, ZammadError) and e.permanent
            )
            error = f"{type(e).__name__}: {e}"
            gave_up = await outbox.mark_error(
                row,
                error,
                permanent=permanent,
                max_attempts=self.bot.settings.zammad_outbox_max_attempts,
            )
            level = logging.ERROR if gave_up else logging.WARNING
            log.log(
                level,
                f"Zammad sync {row.direction}/{row.kind} row={row.id} "
                f"ticket={row.ticket_id} attempt {row.attempts + 1} failed"
                f"{' (giving up)' if gave_up else ''}: {error}",
                exc_info=not isinstance(e, ZammadError | to_discord.GiveUp),
            )
            return False
        await outbox.mark_done(row.id)
        return True

    # -- log-channel reporting (rate-limited) ----------------------------------------

    async def alert(
        self, topic: str, text: str, *, error: bool = False, ping: bool = False
    ) -> bool:
        """One log-channel message per topic per ALERT_INTERVAL_SECONDS.
        Only `error` (or `ping`) messages notify; the rest are @silent."""
        now = time.monotonic()
        last = self._alerted.get(topic)
        if last is not None and now - last < ALERT_INTERVAL_SECONDS:
            return False
        self._alerted[topic] = now
        from nephthys.discord_ops.heartbeat import send_heartbeat

        await send_heartbeat(self.bot, text, error=error, ping=ping)
        return True

    async def report_health(self) -> None:
        stats = await outbox.stats()
        if stats.failed > self._reported_failed:
            await self.alert(
                "failed",
                f"❌ Zammad sync gave up on {stats.failed} item(s). Last error: "
                f"`{stats.last_error}`. Fix the cause, then run `nephthys zammad-retry`.",
                ping=True,
            )
        self._reported_failed = stats.failed
        stuck = (stats.oldest_pending_seconds or 0) > BACKLOG_ALERT_SECONDS
        if stuck:
            minutes = int((stats.oldest_pending_seconds or 0) // 60)
            if await self.alert(
                "backlog",
                f"⚠️ Zammad sync is behind: {stats.pending} item(s) queued, the oldest "
                f"for {minutes} min. Last error: `{stats.last_error}`. Discord keeps "
                "working; everything is delivered once Zammad is reachable again.",
            ):
                self._backlog_reported = True
        elif self._backlog_reported and stats.pending == 0:
            self._backlog_reported = False
            self._alerted.pop("backlog", None)
            from nephthys.discord_ops.heartbeat import send_heartbeat

            await send_heartbeat(self.bot, "✅ Zammad sync has caught up.")

    # -- the #tickets webhook (Discord side) ------------------------------------------

    async def get_webhook(self) -> Any:
        """The bot's own webhook on #tickets, created on first use (needs
        Manage Webhooks). None when it can't be had: callers fall back."""
        if self._webhook is not None:
            return self._webhook
        from nephthys.discord_ops.private_flow import tickets_channel

        channel = await tickets_channel(self.bot)
        if channel is None:
            self.webhook_error = "the #tickets channel wasn't found"
            return None
        me = self.bot.user
        try:
            for hook in await channel.webhooks():
                owner = getattr(hook, "user", None)
                if hook.token and me is not None and owner and owner.id == me.id:
                    self._webhook = hook
                    return hook
            self._webhook = await channel.create_webhook(
                name=to_discord.WEBHOOK_NAME,
                reason="Posts Zammad agent replies into ticket threads",
            )
        except discord.HTTPException as e:
            self.webhook_error = f"{type(e).__name__}: {e}"
            return None
        return self._webhook

    def forget_webhook(self) -> None:
        self._webhook = None

    # -- intake: Zammad webhook + reconciliation ------------------------------------------

    async def bot_user_id(self) -> int:
        return int((await self.client.me())["id"])

    async def ingest_article(
        self, ticket: Ticket, article: dict[str, Any], author: dict[str, Any] | None
    ) -> bool:
        """Queue a Zammad article for Discord if - and only if - it is a public
        reply written by an agent in Zammad. Returns True when queued."""
        # Internal notes never leave Zammad. `is False` on purpose: anything
        # that isn't explicitly public (missing, null, "false") is dropped.
        if article.get("internal") is not False:
            return False
        if article.get("sender") != "Agent":
            return False
        if int(article.get("ticket_id") or 0) != ticket.zammad_ticket_id:
            return False
        article_id = int(article["id"])
        # Loop prevention: the bot's own articles (mirrored Discord messages)
        if is_marker(article.get("message_id")):
            return False
        if article.get("created_by_id") == await self.bot_user_id():
            return False
        if await outbox.is_known_article(article_id):
            return False
        name = display_name(author) or str(article.get("from") or "").strip()
        queued = await outbox.enqueue(
            ticket.id,
            outbox.TO_DISCORD,
            outbox.POST_ARTICLE,
            {
                "article_id": article_id,
                "zammad_ticket_id": ticket.zammad_ticket_id,
                "body": article.get("body") or "",
                "content_type": article.get("content_type") or "text/plain",
                "author_name": name,
                "author_image": (author or {}).get("image"),
                "attachments": [
                    {
                        "id": a.get("id"),
                        "filename": a.get("filename"),
                        "size": a.get("size"),
                    }
                    for a in (article.get("attachments") or [])
                    if isinstance(a, dict) and a.get("id") is not None
                ],
            },
            dedupe_key=f"zd:article:{article_id}",
        )
        return queued

    async def queue_sync(self, ticket: Ticket, by_name: str | None = None) -> bool:
        if await outbox.has_pending(
            ticket.id, outbox.TO_DISCORD, (outbox.SYNC_TICKET,)
        ):
            return False
        return await outbox.enqueue(
            ticket.id, outbox.TO_DISCORD, outbox.SYNC_TICKET, {"by_name": by_name}
        )

    async def handle_webhook(self, payload: dict[str, Any]) -> str:
        """A verified webhook delivery. Only queues work; returns what it did."""
        zammad_ticket = payload.get("ticket")
        if not isinstance(zammad_ticket, dict) or zammad_ticket.get("id") is None:
            return "ignored: no ticket"
        ticket = (
            await Ticket.objects()
            .where(Ticket.zammad_ticket_id == int(zammad_ticket["id"]))
            .first()
        )
        if ticket is None:
            # Created directly in Zammad (email etc.): not ours, leave it alone
            return "ignored: not a Discord ticket"
        queued = []
        article = payload.get("article")
        if isinstance(article, dict) and article.get("id") is not None:
            author = article.get("created_by")
            if await self.ingest_article(
                ticket, article, author if isinstance(author, dict) else None
            ):
                queued.append("article")
        by_name = display_name(zammad_ticket.get("updated_by")) or None
        if zammad_ticket.get("updated_by_id") == await self.bot_user_id():
            by_name = None  # the bot's own update echoing back
        if await self.queue_sync(ticket, by_name):
            queued.append("sync")
        if queued:
            self.wake()
        return "queued: " + ", ".join(queued) if queued else "nothing to do"

    async def reconcile(self) -> int:
        """Catch up on anything a webhook didn't deliver (Zammad only retries a
        webhook for about two minutes, so a longer bot restart would otherwise
        lose replies). Returns how many items were queued."""
        queued = 0
        authors: dict[int, dict[str, Any]] = {}
        closed_after = outbox.utcnow() - RECONCILE_CLOSED_FOR
        for ticket in await outbox.synced_tickets(closed_after):
            assert ticket.zammad_ticket_id is not None
            try:
                current = await self.client.get_ticket(ticket.zammad_ticket_id)
                for article in await self.client.articles(ticket.zammad_ticket_id):
                    if (
                        article.get("internal") is not False
                        or article.get("sender") != "Agent"
                    ):
                        continue
                    author_id = article.get("created_by_id")
                    if isinstance(author_id, int) and author_id not in authors:
                        try:
                            authors[author_id] = await self.client.get_user(author_id)
                        except ZammadError:
                            authors[author_id] = {}
                    author = (
                        authors.get(author_id) if isinstance(author_id, int) else None
                    )
                    if await self.ingest_article(ticket, article, author or None):
                        queued += 1
            except ZammadError as e:
                log.info(f"Zammad reconcile skipped T-{ticket.number}: {e}")
                if e.status is None:
                    break  # unreachable: no point trying the rest
                continue
            differs = (
                is_closed_state(current.get("state"))
                != (ticket.status == TicketStatus.CLOSED)
            ) or (
                isinstance(current.get("group"), str)
                and current.get("group") != ticket.zammad_group
            )
            if differs and await self.queue_sync(ticket):
                queued += 1
        if queued:
            log.info(f"Zammad reconcile queued {queued} missed item(s)")
            self.wake()
        return queued
