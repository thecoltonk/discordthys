from aiohttp.test_utils import TestClient
from aiohttp.test_utils import TestServer

from nephthys.bot import SupportBot
from nephthys.web.health import build_app
from tests.helpers import make_settings


async def test_health_reports_unhealthy_before_login():
    bot = SupportBot(make_settings(), connect_db=False)
    async with TestClient(TestServer(build_app(bot))) as client:
        resp = await client.get("/health")
        assert resp.status == 503
        body = await resp.json()
        assert body["healthy"] is False
        assert body["discord_ready"] is False

        metrics = await client.get("/metrics")
        assert metrics.status == 200
    await bot.close()
