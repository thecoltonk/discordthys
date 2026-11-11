"""Minimal stand-ins for discord.py objects, enough to drive the flows.

They use `spec=` so `isinstance(x, discord.Thread)` etc. still works.
"""

import itertools
from collections import defaultdict
from datetime import datetime
from datetime import UTC
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from unittest.mock import MagicMock

import discord

from nephthys.config import Settings
from tests.helpers import make_settings

_ids = itertools.count(2_000_000_000_000_000)

BOT_USER_ID = 999


def next_id() -> int:
    return next(_ids)


def not_found() -> discord.NotFound:
    return discord.NotFound(MagicMock(status=404, reason="Not Found"), "Unknown")


class SentMessage(SimpleNamespace):
    pass


def make_user(
    *,
    staff: bool = False,
    bot: bool = False,
    name: str = "player",
    settings: Settings | None = None,
) -> MagicMock:
    settings = settings or make_settings()
    user = MagicMock(spec=discord.Member)
    user.id = next_id()
    user.bot = bot
    user.name = name
    user.display_name = name
    user.mention = f"<@{user.id}>"
    user.roles = [SimpleNamespace(id=settings.staff_role_id)] if staff else []
    return user


class FakeWorld:
    """Tracks everything the bot does to Discord."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.channel_sends: dict[int, list[SentMessage]] = defaultdict(list)
        # (channel_id, message_id) -> set of emoji the bot has on it
        self.reactions: dict[tuple[int, int], set[str]] = defaultdict(set)
        self.threads: dict[int, MagicMock] = {}
        self.deleted_messages: list[tuple[int, int]] = []
        self.channel_history: dict[int, list[Any]] = defaultdict(list)


class FakePartialMessage:
    def __init__(self, world: FakeWorld, channel_id: int, message_id: int):
        self.world = world
        self.channel_id = channel_id
        self.id = message_id

    async def add_reaction(self, emoji: str) -> None:
        self.world.reactions[(self.channel_id, self.id)].add(emoji)

    async def remove_reaction(self, emoji: str, member: Any) -> None:
        assert member.id == BOT_USER_ID
        self.world.reactions[(self.channel_id, self.id)].discard(emoji)

    async def delete(self) -> None:
        self.world.deleted_messages.append((self.channel_id, self.id))


class FakeMessageable:
    def __init__(self, world: FakeWorld, channel_id: int):
        self.world = world
        self.id = channel_id

    async def send(self, content: str | None = None, **kwargs: Any) -> SentMessage:
        msg = SentMessage(
            id=next_id(), content=content, channel_id=self.id, author_bot=True, **kwargs
        )
        self.world.channel_sends[self.id].append(msg)
        self.world.channel_history[self.id].append(msg)
        return msg

    def get_partial_message(self, message_id: int) -> FakePartialMessage:
        return FakePartialMessage(self.world, self.id, message_id)


def make_thread(world: FakeWorld, thread_id: int, parent_id: int) -> MagicMock:
    thread = MagicMock(spec=discord.Thread)
    thread.id = thread_id
    thread.parent_id = parent_id
    thread.archived = False
    thread.human_messages = []

    async def send(content: str | None = None, **kwargs: Any) -> SentMessage:
        if thread.archived:
            thread.archived = False  # Discord unarchives on send
        msg = SentMessage(id=next_id(), content=content, **kwargs)
        world.channel_sends[thread_id].append(msg)
        return msg

    async def edit(**kwargs: Any) -> MagicMock:
        if "archived" in kwargs:
            thread.archived = kwargs["archived"]
        return thread

    async def delete(**kwargs: Any) -> None:
        world.threads.pop(thread_id, None)
        thread.deleted = True

    def history(**kwargs: Any):
        async def gen():
            for m in thread.human_messages:
                yield m

        return gen()

    thread.send = AsyncMock(side_effect=send)
    thread.edit = AsyncMock(side_effect=edit)
    thread.delete = AsyncMock(side_effect=delete)
    thread.history = history
    thread.deleted = False
    world.threads[thread_id] = thread
    return thread


class FakeBot:
    """Stands in for `SupportBot` in flows (duck-typed)."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or make_settings()
        self.world = FakeWorld(self.settings)
        self.user = SimpleNamespace(id=BOT_USER_ID)

    def get_channel(self, channel_id: int) -> Any:
        return self.world.threads.get(channel_id)

    async def fetch_channel(self, channel_id: int) -> Any:
        if channel_id in self.world.threads:
            return self.world.threads[channel_id]
        raise not_found()

    def get_partial_messageable(self, channel_id: int) -> FakeMessageable:
        return FakeMessageable(self.world, channel_id)

    def sends(self, channel_id: int) -> list[SentMessage]:
        return self.world.channel_sends[channel_id]

    def reactions_on(self, message_id: int) -> set[str]:
        return self.world.reactions[(self.settings.community_channel_id, message_id)]


def make_message(
    bot: FakeBot,
    author: MagicMock,
    content: str = "How do I claim land?",
    *,
    channel: Any = None,
    msg_type: discord.MessageType = discord.MessageType.default,
) -> MagicMock:
    settings = bot.settings
    msg = MagicMock(spec=discord.Message)
    msg.id = next_id()
    msg.author = author
    msg.content = content
    msg.clean_content = content
    msg.type = msg_type
    msg.webhook_id = None
    msg.created_at = datetime.now(UTC)
    msg.guild = SimpleNamespace(id=settings.guild_id)
    if channel is None:
        channel = SimpleNamespace(id=settings.community_channel_id)
    msg.channel = channel
    msg.jump_url = (
        f"https://discord.com/channels/{settings.guild_id}/{channel.id}/{msg.id}"
    )

    async def create_thread(**kwargs: Any) -> MagicMock:
        thread = make_thread(bot.world, msg.id, channel.id)
        thread.name = kwargs["name"]
        thread.auto_archive_duration = kwargs.get("auto_archive_duration")
        return thread

    msg.create_thread = AsyncMock(side_effect=create_thread)
    msg.delete = AsyncMock()
    msg.reply = AsyncMock()
    return msg


def make_interaction(bot: FakeBot, user: MagicMock) -> MagicMock:
    interaction = MagicMock()
    interaction.client = bot
    interaction.user = user
    interaction.response.defer = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction
