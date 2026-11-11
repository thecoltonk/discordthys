from nephthys.config import load_settings
from nephthys.config import Settings

BASE_ENV = {
    "DISCORD_TOKEN": "test-token",
    "GUILD_ID": "100",
    "COMMUNITY_CHANNEL_ID": "200",
    "STAFF_ROLE_ID": "300",
    "STAFF_CHANNEL_ID": "400",
    "LOG_CHANNEL_ID": "500",
    "DATABASE_URL": "postgresql://unused",
    "FAQ_URL": "https://example.com/faq",
    "SERVER_ADDRESS": "play.example.com",
    # Tests opt in to the sticky explicitly (it schedules background reposts)
    "STICKY_ENABLED": "false",
}


def make_settings(**overrides: str) -> Settings:
    env = dict(BASE_ENV)
    env.update(overrides)
    return load_settings(env)
