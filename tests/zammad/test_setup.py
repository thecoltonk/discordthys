"""`nephthys zammad-setup`: creates what's needed, and is safe to repeat."""

from typing import Any

from nephthys.zammad import setup as zsetup
from nephthys.zammad.setup import format_report
from nephthys.zammad.setup import redact
from nephthys.zammad.setup import TRIGGER_NAME
from nephthys.zammad.setup import WEBHOOK_NAME
from nephthys.zammad.to_zammad import DISCORD_ATTRIBUTE
from tests.zammad.conftest import Backend
from tests.zammad.conftest import BOT_LOGIN
from tests.zammad.conftest import World


async def named(world: World, resource: str, name: str) -> list[dict[str, Any]]:
    return [i for i in await world.admin.list(resource) if i.get("name") == name]


async def test_setup_creates_everything_and_is_idempotent(world: World):
    # The `world` fixture already ran it once; run it twice more
    report = await world.setup()
    assert report.success, format_report(report)
    report = await world.setup()
    assert report.success, format_report(report)

    [webhook] = await named(world, "webhooks", WEBHOOK_NAME)
    assert webhook["endpoint"] == world.webhook_url
    assert webhook["active"] is True and webhook["customized_payload"] is False
    assert webhook["signature_token"] != world.settings.zammad_webhook_secret  # masked

    [trigger] = await named(world, "triggers", TRIGGER_NAME)
    bot_user = await world.client.me()
    assert bot_user["login"] == BOT_LOGIN
    assert trigger["condition"] == zsetup.trigger_condition(bot_user["id"])
    assert trigger["perform"] == {
        "notification.webhook": {"webhook_id": [str(webhook["id"])]}
    }
    assert trigger["active"] is True
    assert "notification.email" not in trigger["perform"]

    attributes = [
        a
        for a in await world.admin.list("object_manager_attributes")
        if a["name"] == DISCORD_ATTRIBUTE
    ]
    assert len(attributes) == 1 and attributes[0]["data_type"] == "input"
    assert not attributes[0].get("to_migrate") and not attributes[0].get("to_create")

    [setting] = await named(world, "settings", zsetup.NO_REPLY_SETTING)
    pattern = setting["state_current"]["value"]
    assert pattern.count("discord") == 1  # added once, not once per run
    import re

    assert re.search(pattern, "123456789@discord.invalid", re.IGNORECASE)
    assert re.search(pattern, "noreply@example.com", re.IGNORECASE)  # original kept
    text = format_report(report)
    assert "Zammad is set up" in text and "✗" not in text


async def test_setup_updates_a_changed_webhook_address(world: World):
    # (A real Zammad only accepts an address it can resolve.)
    moved = f"http://{world.backend.callback_host}:3999/zammad/webhook"
    world.env["ZAMMAD_WEBHOOK_URL"] = moved
    from tests.helpers import make_settings

    world.bot.settings = make_settings(**world.env)
    assert (await world.setup()).success
    [webhook] = await named(world, "webhooks", WEBHOOK_NAME)
    assert webhook["endpoint"] == moved
    assert len(await named(world, "triggers", TRIGGER_NAME)) == 1


async def test_setup_on_a_fresh_zammad(backend: Backend, db: None):
    """First run on the fake: the attribute is new, so Zammad 'restarts' (the
    fake refuses a few calls) and the setup waits for it to come back."""
    fake = backend.fake
    if fake is None:
        import pytest

        pytest.skip("a fresh instance: fake only (see DECISIONS for the live run)")
    world = World(backend)
    await world.start(setup=False)
    try:
        assert fake.attributes == {} and fake.webhooks == {} and fake.triggers == {}
        report = await world.setup()
        assert report.success, format_report(report)
        assert "Zammad restarts" in format_report(report)
        assert DISCORD_ATTRIBUTE in fake.user_columns
        assert len(fake.webhooks) == len(fake.triggers) == 1
        # Second run: nothing is created, and no second restart
        before = len(fake.requests)
        again = await world.setup()
        assert again.success and "Zammad restarts" not in format_report(again)
        posts = [r for r in fake.requests[before:] if r[0] == "POST"]
        assert posts == []
    finally:
        await world.stop()


async def test_setup_reports_missing_groups_and_access(backend: Backend, db: None):
    fake = backend.fake
    if fake is None:
        import pytest

        pytest.skip("removes groups: fake only")
    reports = next(g for g in fake.groups.values() if g["name"] == "Reports")
    del fake.groups[reports["id"]]
    store = next(g for g in fake.groups.values() if g["name"] == "Store")
    bot_user = next(u for u in fake.users.values() if u["login"] == BOT_LOGIN)
    del bot_user["group_ids"][str(store["id"])]
    world = World(backend)
    await world.start(setup=False)
    try:
        report = await world.setup()
        assert not report.success
        text = format_report(report)
        assert 'group "Reports" doesn\'t exist' in text
        assert 'no access to group "Store"' in text
        assert 'group "Other" exists' in text
        assert "2 problem(s)" in text
        # Everything else was still set up
        assert len(fake.webhooks) == len(fake.triggers) == 1
    finally:
        await world.stop()


async def test_setup_with_a_token_that_is_not_admin(backend: Backend, db: None):
    """Run with only the bot's everyday (agent) token: clear problems, no crash."""
    if backend.fake is None:
        import pytest

        pytest.skip("fake only")
    world = World(backend)
    await world.start(setup=False)
    try:
        report = await zsetup.run_setup(
            world.settings, client=world.client, admin=world.client, restart_poll=0.01
        )
        assert not report.success
        assert any("403" in p for p in report.problems)
    finally:
        await world.stop()


async def test_unresolvable_webhook_address_gets_a_useful_hint(world: World):
    """Zammad 7.2 answers 422 "could not be resolved to a safe IP address" for
    a webhook address it can't resolve, e.g. when the bot isn't on its Docker
    network yet."""
    from tests.helpers import make_settings

    world.env["ZAMMAD_WEBHOOK_URL"] = "http://no-such-host.invalid:3000/zammad/webhook"
    world.bot.settings = make_settings(**world.env)
    report = await world.setup()
    assert not report.success
    text = format_report(report)
    assert "could not be resolved" in text
    assert "docker compose exec bot nephthys zammad-setup" in text


def test_zammad_rejects_attribute_names_ending_in_id():
    """Why the attribute isn't literally `discord_id`: Zammad 7.2 answers
    "Name can't be used because *_id and *_ids are not allowed"."""
    assert not DISCORD_ATTRIBUTE.endswith(("_id", "_ids"))
    assert zsetup.attribute_definition()["display"] == "Discord ID"


def test_tokens_are_never_printed():
    from tests.helpers import make_settings

    settings = make_settings(
        TICKETS_CHANNEL_ID="600",
        ZAMMAD_URL="http://z",
        ZAMMAD_TOKEN="runtime-token-abc",
        ZAMMAD_SETUP_TOKEN="setup-token-xyz",
        ZAMMAD_WEBHOOK_SECRET="s" * 20,
    )
    text = redact("failed with runtime-token-abc and setup-token-xyz", settings)
    assert text == "failed with *** and ***"
