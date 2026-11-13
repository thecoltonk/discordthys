"""Harness for the Zammad sync tests.

The integration tests run against the in-process fake (`fake_zammad.py`)
always, and against a real Zammad as well when these are set:

    ZAMMAD_TEST_URL            e.g. http://127.0.0.1:8090 (or the railsserver)
    ZAMMAD_TEST_ADMIN_TOKEN    an admin token with `admin` + `ticket.agent`
    ZAMMAD_TEST_CALLBACK_HOST  an address of this machine that Zammad can reach
                               (e.g. the Docker bridge gateway, 172.18.0.1)

Against a real Zammad the tests create groups, two users (a bot user and an
agent), customers and tickets, and run `zammad-setup`. Use a throwaway instance.
"""

import asyncio
import os
import random
from collections.abc import AsyncIterator
from collections.abc import Awaitable
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from typing import cast

import pytest
from aiohttp import web

from nephthys.core import tickets
from nephthys.core.ticket_categories import DEFAULT_TICKET_CATEGORIES
from nephthys.core.ticket_categories import use_ticket_categories
from nephthys.database.tables import ZammadOutbox
from nephthys.ui.ticket_buttons import CategoryButton
from nephthys.ui.ticket_buttons import CreateTicketButton
from nephthys.ui.ticket_buttons import SubcategoryButton
from nephthys.web.health import build_app
from nephthys.zammad.client import ZammadClient
from nephthys.zammad.client import ZammadError
from nephthys.zammad.setup import run_setup
from nephthys.zammad.sync import ZammadSync
from tests.fakes import FakeBot
from tests.fakes import make_interaction
from tests.fakes import make_message
from tests.fakes import make_text_channel
from tests.fakes import make_user
from tests.helpers import make_settings
from tests.zammad.fake_zammad import ADMIN_TOKEN
from tests.zammad.fake_zammad import FakeZammad

TICKETS = 600
SECRET = "test-webhook-secret-0123456789"
BOT_LOGIN = "discord-bot@example.com"
AGENT_LOGIN = "bea@example.com"
AGENT_NAME = "Bea Agent"
PUBLIC_URL = "https://support.example.com"
DEFAULT_AVATAR = "https://cdn.example.com/support.png"
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c6360f8cfc0f01f00050001ff89993d1d0000000049454e44ae426082"
)


def new_player(name: str = "Alex") -> Any:
    """A Discord member with an ID nobody used before (customers persist on a
    real Zammad)."""
    user = make_user(name=name)
    user.id = random.randrange(10**17, 10**18)
    user.mention = f"<@{user.id}>"
    return user


@dataclass
class Backend:
    """The Zammad under test: the fake, or a real one."""

    kind: str
    url: str
    admin_token: str
    callback_host: str
    fake: FakeZammad | None = None

    @property
    def real(self) -> bool:
        return self.fake is None

    def as_user(self, login: str | None = None) -> ZammadClient:
        return ZammadClient(self.url, self.admin_token, on_behalf_of=login)

    async def prepare(self) -> None:
        """Groups for the default categories, a bot user and an agent."""
        admin = self.as_user()
        try:
            wanted = DEFAULT_TICKET_CATEGORIES.zammad_groups
            groups = {g["name"]: g for g in await admin.list("groups")}
            for name in wanted:
                if name not in groups:
                    groups[name] = await admin.create(
                        "groups", {"name": name, "active": True}
                    )
            access = {str(groups[name]["id"]): ["full"] for name in wanted}
            # Admins don't see other groups' tickets without group access either
            me = await admin.me()
            await admin.update_user(
                me["id"], {"group_ids": {**(me.get("group_ids") or {}), **access}}
            )
            for login, first, last in (
                (BOT_LOGIN, "Discord", "Bot"),
                (AGENT_LOGIN, "Bea", "Agent"),
            ):
                try:
                    user = await admin.create_user(
                        {
                            "login": login,
                            "email": login,
                            "firstname": first,
                            "lastname": last,
                            "roles": ["Agent"],
                        }
                    )
                except ZammadError:
                    user = await admin.find_user(login=login, email=login)
                    assert user is not None
                await admin.update_user(user["id"], {"group_ids": access})
        finally:
            await admin.close()


@pytest.fixture(params=["fake", "real"])
async def backend(request: pytest.FixtureRequest) -> AsyncIterator[Backend]:
    if request.param == "real":
        url = os.environ.get("ZAMMAD_TEST_URL")
        token = os.environ.get("ZAMMAD_TEST_ADMIN_TOKEN")
        if not url or not token:
            pytest.skip("no real Zammad (set ZAMMAD_TEST_URL, ZAMMAD_TEST_ADMIN_TOKEN)")
        real = Backend(
            kind="real",
            url=url,
            admin_token=token,
            callback_host=os.environ.get("ZAMMAD_TEST_CALLBACK_HOST", "127.0.0.1"),
        )
        await real.prepare()
        yield real
        return
    fake = FakeZammad()
    url = await fake.start()
    backend = Backend(
        kind="fake",
        url=url,
        admin_token=ADMIN_TOKEN,
        callback_host="127.0.0.1",
        fake=fake,
    )
    await backend.prepare()
    yield backend
    await fake.stop()


class World:
    """A fake Discord + the real sync engine + the bot's HTTP server, wired to
    a Zammad backend."""

    def __init__(self, backend: Backend, **env: str):
        self.backend = backend
        self.env = {
            "TICKETS_CHANNEL_ID": str(TICKETS),
            "ZAMMAD_URL": backend.url,
            "ZAMMAD_TOKEN": backend.admin_token,
            "ZAMMAD_WEBHOOK_SECRET": SECRET,
            "ZAMMAD_PUBLIC_URL": PUBLIC_URL,
            "ZAMMAD_DEFAULT_AVATAR_URL": DEFAULT_AVATAR,
            **env,
        }
        self.bot = FakeBot(make_settings(**self.env))
        self.channel = make_text_channel(self.bot, TICKETS)
        self.client = backend.as_user(BOT_LOGIN)
        self.agent = backend.as_user(AGENT_LOGIN)
        self.admin = backend.as_user()
        self.sync = ZammadSync(cast(Any, self.bot), client=self.client)
        self.bot.zammad = self.sync
        self.runner: web.AppRunner | None = None
        self.webhook_url = ""
        self.files: dict[str, bytes] = {}
        self.files_url = ""

    @property
    def settings(self) -> Any:
        return self.bot.settings

    async def start(self, *, setup: bool = True) -> "World":
        app = build_app(cast(Any, self.bot))

        async def cdn(request: web.Request) -> web.Response:
            data = self.files.get(request.match_info["name"])
            if data is None:
                return web.Response(status=404)
            return web.Response(body=data)

        app.router.add_get("/cdn/{name}", cdn)  # stands in for Discord's CDN
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "0.0.0.0", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
        self.files_url = f"http://127.0.0.1:{port}/cdn"
        self.webhook_url = f"http://{self.backend.callback_host}:{port}/zammad/webhook"
        self.env["ZAMMAD_WEBHOOK_URL"] = self.webhook_url
        self.bot.settings = make_settings(**self.env)
        if setup:
            report = await self.setup()
            assert report.success, report.lines
        return self

    async def setup(self) -> Any:
        return await run_setup(
            self.settings,
            client=self.client,
            admin=self.admin,
            restart_poll=5.0 if self.backend.real else 0.01,
        )

    async def stop(self) -> None:
        await self.sync.stop()
        for client in (self.agent, self.admin):
            await client.close()
        if self.runner is not None:
            await self.runner.cleanup()

    # -- driving Discord ---------------------------------------------------------

    async def open_ticket(
        self, category: str | None = "store", sub: str | None = None, player: Any = None
    ) -> tuple[Any, Any, Any]:
        """Create Ticket (+ category picks). Returns (ticket, thread, player)."""
        player = player or new_player()
        await CreateTicketButton().callback(make_interaction(self.bot, player))
        thread, _ = self.channel.created_threads[-1]
        ticket = await tickets.get_ticket_by_thread(thread.id)
        assert ticket is not None
        if category:
            await CategoryButton(ticket.id, category).callback(
                make_interaction(self.bot, player)
            )
            if sub:
                await SubcategoryButton(ticket.id, category, sub).callback(
                    make_interaction(self.bot, player)
                )
        return await tickets.require_ticket(ticket.id), thread, player

    async def say(self, thread: Any, author: Any, text: str, files: Any = None) -> Any:
        """A person posts in the ticket thread (goes through the real cog)."""
        from nephthys.cogs.tickets import Tickets

        message = make_message(self.bot, author, text, channel=thread)
        message.attachments = []
        for name, data in (files or {}).items():
            self.files[name] = data
            message.attachments.append(
                type(
                    "Attachment",
                    (),
                    {
                        "id": random.randrange(10**17),
                        "url": f"{self.files_url}/{name}",
                        "filename": name,
                        "size": len(data),
                        "content_type": "image/png",
                    },
                )()
            )
        thread.human_messages.append(message)
        await Tickets(cast(Any, self.bot)).on_message(message)
        return message

    # -- driving the sync -----------------------------------------------------------

    async def make_due(self) -> None:
        """Skip the backoff wait of everything that's queued."""
        await ZammadOutbox.raw(
            "UPDATE zammad_outbox SET next_attempt_at = now() WHERE status = 'pending'"
        )

    async def drain(self) -> None:
        if self.backend.fake is not None:
            await self.backend.fake.settle()
        await self.sync.drain()

    async def fresh(self, ticket: Any) -> Any:
        return await tickets.require_ticket(ticket.id)

    async def synced(self, ticket: Any) -> Any:
        """Drain, and return the ticket once it exists in Zammad."""
        await self.drain()
        ticket = await self.fresh(ticket)
        assert ticket.zammad_ticket_id is not None, await self.rows()
        return ticket

    async def eventually(
        self, condition: Callable[[], Awaitable[Any]], what: str = "condition"
    ) -> Any:
        """Drain until `condition()` is truthy. A real Zammad delivers webhooks
        from a background job, a few seconds after the change."""
        deadline = 60.0 if self.backend.real else 3.0
        waited = 0.0
        while True:
            await self.drain()
            result = await condition()
            if result:
                return result
            if waited >= deadline:
                raise AssertionError(
                    f"timed out waiting for {what}: {await self.rows()}"
                )
            await asyncio.sleep(0.5 if self.backend.real else 0.02)
            waited += 0.5 if self.backend.real else 0.02

    async def quiet(self) -> None:
        """Give late webhooks time to arrive, then drain: for asserting that
        something did NOT happen."""
        if self.backend.real:
            for _ in range(24):
                await asyncio.sleep(0.5)
                await self.drain()
        else:
            for _ in range(3):
                await asyncio.sleep(0.02)
                await self.drain()

    async def rows(self) -> list[dict[str, Any]]:
        return await ZammadOutbox.raw(
            "SELECT id, ticket, direction, kind, status, attempts, last_error "
            "FROM zammad_outbox ORDER BY id"
        )

    async def articles(self, ticket: Any) -> list[dict[str, Any]]:
        ticket = await self.fresh(ticket)
        return await self.admin.articles(ticket.zammad_ticket_id)

    def thread_posts(self, thread: Any) -> list[Any]:
        return self.bot.sends(thread.id)

    def replies(self, thread: Any) -> list[Any]:
        """Messages posted through the #tickets webhook (agent replies)."""
        return [
            m for m in self.thread_posts(thread) if getattr(m, "via_webhook", False)
        ]

    def log(self) -> list[str]:
        return [m.content for m in self.bot.sends(self.settings.log_channel_id)]

    def card(self, ticket: Any) -> Any:
        return self.bot.message(
            ticket.staff_card_channel_id, ticket.staff_card_message_id
        )


@pytest.fixture(autouse=True)
def default_categories():
    use_ticket_categories(DEFAULT_TICKET_CATEGORIES)
    yield
    use_ticket_categories(DEFAULT_TICKET_CATEGORIES)


@pytest.fixture
async def world(db: None, backend: Backend) -> AsyncIterator[World]:
    world = await World(backend).start()
    yield world
    await world.stop()
