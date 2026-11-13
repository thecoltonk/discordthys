"""The log channel: only real errors notify; everything else is @silent."""

from typing import Any
from typing import cast

import pytest

from nephthys.bot import SupportBot
from nephthys.discord_ops import heartbeat
from nephthys.discord_ops.heartbeat import send_heartbeat
from tests.fakes import FakeBot
from tests.helpers import make_settings

LOG = 500


def log_sends(bot: FakeBot) -> list[Any]:
    return bot.sends(LOG)


async def test_notices_are_silent_and_errors_notify():
    bot = FakeBot(make_settings(MAINTAINER_ID="777"))
    await send_heartbeat(cast(Any, bot), "✅ caught up")
    await send_heartbeat(cast(Any, bot), "❌ broke", error=True)
    await send_heartbeat(cast(Any, bot), "❌ broke badly", ping=True)
    notice, error, ping = log_sends(bot)
    assert notice.silent is True
    assert not getattr(error, "silent", False)
    assert error.allowed_mentions.users is False  # an error, but nobody pinged
    assert not getattr(ping, "silent", False) and ping.content.startswith("<@777> ")
    assert [u.id for u in ping.allowed_mentions.users] == [777]


async def test_no_log_channel_no_messages():
    bot = FakeBot(make_settings(LOG_CHANNEL_ID=""))
    await send_heartbeat(cast(Any, bot), "❌ broke", error=True)
    assert not bot.world.channel_sends


@pytest.mark.parametrize(
    ("env", "expected"), [({}, []), ({"LOG_STARTUP_NOTICE": "true"}, [True])]
)
async def test_startup_notice_is_opt_in_and_silent(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, str], expected: list[bool]
):
    sent: list[bool] = []

    async def fake_send(
        bot: Any, text: str, *, error: bool = False, ping: bool = False
    ):
        assert "online" in text
        sent.append(not (error or ping))  # True = sent @silent

    monkeypatch.setattr(heartbeat, "send_heartbeat", fake_send)
    bot = SupportBot(make_settings(**env), connect_db=False)
    await bot.on_ready()
    await bot.on_ready()  # reconnects don't announce again
    assert sent == expected
    await bot.close()


def test_startup_notice_setting_defaults_off():
    assert make_settings().log_startup_notice is False
    assert make_settings(LOG_STARTUP_NOTICE="true").log_startup_notice is True
