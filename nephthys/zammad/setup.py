"""`nephthys zammad-setup`: configure Zammad for the #tickets sync.

Idempotent: run it as often as you like. It only uses Zammad's REST API and
creates or updates exactly these things:

1. the custom User attribute that holds the Discord user ID,
2. the webhook that calls the bot (signed with ZAMMAD_WEBHOOK_SECRET),
3. the trigger that fires that webhook for Discord tickets,
4. an addition to the "no auto response" address filter, so no trigger can
   ever email a Discord customer's placeholder address,

and it checks that the group of every #tickets category exists and that the
bot's API user can work in it.
"""

import asyncio
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field
from typing import Any

from nephthys.config import Settings
from nephthys.core.ticket_categories import get_ticket_categories
from nephthys.zammad.client import ZammadClient
from nephthys.zammad.client import ZammadError
from nephthys.zammad.to_zammad import DISCORD_ATTRIBUTE

log = logging.getLogger(__name__)

WEBHOOK_NAME = "Cloudsail Support bot (Discord #tickets)"
TRIGGER_NAME = "Cloudsail Support bot: notify Discord"
NOTE = "Managed by `nephthys zammad-setup`. Manual edits are overwritten."
NO_REPLY_SETTING = "send_no_auto_response_reg_exp"
# Regex fragment (Ruby, matched case-insensitively) for the customers' addresses
NO_REPLY_FRAGMENT = r".+@discord\.invalid"
RESTART_WAIT_SECONDS = 240
RESTART_POLL_SECONDS = 5.0


def attribute_definition() -> dict[str, Any]:
    shown = {"shown": True, "null": True}
    return {
        "name": DISCORD_ATTRIBUTE,
        "object": "User",
        "display": "Discord ID",
        "active": True,
        "position": 1500,
        "data_type": "input",
        "data_option": {"type": "text", "maxlength": 32, "null": True, "default": ""},
        "screens": {
            "view": {"-all-": {"shown": True}},
            "edit": {"-all-": shown},
            "create": {"-all-": {"shown": False, "null": True}},
        },
    }


def trigger_condition(bot_user_id: int) -> dict[str, Any]:
    """Discord tickets only (customer login "discord:<id>"), when an agent
    adds a public article - but not the bot's own mirrored messages - or when
    the state or group changes. Internal notes alone never match."""
    return {
        "operator": "AND",
        "conditions": [
            {
                "name": "customer.login",
                "operator": "starts with one of",
                "value": ["discord:"],
            },
            {
                "operator": "OR",
                "conditions": [
                    {
                        "operator": "AND",
                        "conditions": [
                            {
                                "name": "article.internal",
                                "operator": "is",
                                "value": "false",
                            },
                            {
                                "name": "article.sender_id",
                                "operator": "is",
                                "value": ["1"],  # Agent
                            },
                            {
                                "name": "article.created_by_id",
                                "operator": "is not",
                                "pre_condition": "specific",
                                "value": [str(bot_user_id)],
                            },
                        ],
                    },
                    {"name": "ticket.state_id", "operator": "has changed"},
                    {"name": "ticket.group_id", "operator": "has changed"},
                ],
            },
        ],
    }


def has_fragment(pattern: str) -> bool:
    return "discord\\.invalid" in pattern or "discord.invalid" in pattern


def extended_pattern(pattern: str) -> str:
    return f"({pattern})|({NO_REPLY_FRAGMENT})" if pattern else NO_REPLY_FRAGMENT


@dataclass
class SetupReport:
    lines: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    def ok(self, text: str) -> None:
        self.lines.append(f"  ✓ {text}")

    def info(self, text: str) -> None:
        self.lines.append(f"  · {text}")

    def problem(self, text: str) -> None:
        self.lines.append(f"  ✗ {text}")
        self.problems.append(text)

    @property
    def success(self) -> bool:
        return not self.problems


def by_name(items: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    return next((i for i in items if i.get("name") == name), None)


async def wait_for_zammad(
    client: ZammadClient, seconds: float, poll: float = RESTART_POLL_SECONDS
) -> bool:
    """Applying a new attribute restarts Zammad's processes; wait it out."""
    deadline = asyncio.get_running_loop().time() + seconds
    await asyncio.sleep(poll)
    while asyncio.get_running_loop().time() < deadline:
        try:
            await client.request("GET", "/users/me")
            return True
        except ZammadError:
            await asyncio.sleep(poll)
    return False


async def setup_attribute(
    admin: ZammadClient, report: SetupReport, restart_wait: float, restart_poll: float
) -> None:
    definition = attribute_definition()
    attributes = await admin.list("object_manager_attributes")
    existing = next(
        (
            a
            for a in attributes
            if a.get("name") == DISCORD_ATTRIBUTE and a.get("object") == "User"
        ),
        None,
    )
    if existing is None:
        existing = await admin.create("object_manager_attributes", definition)
        report.ok(f'created the User attribute "{DISCORD_ATTRIBUTE}" (Discord ID)')
        pending = True
    else:
        pending = bool(existing.get("to_create") or existing.get("to_migrate"))
        if existing.get("display") != definition["display"] or not existing.get(
            "active"
        ):
            await admin.update(
                "object_manager_attributes", int(existing["id"]), definition
            )
            report.ok(f'updated the User attribute "{DISCORD_ATTRIBUTE}"')
            pending = True
        else:
            report.ok(f'User attribute "{DISCORD_ATTRIBUTE}" exists')
    if pending:
        report.info(
            "applying the attribute: Zammad restarts its processes now and is "
            "unavailable for a minute or two"
        )
        await admin.request("POST", "/object_manager_attributes_execute_migrations")
        if await wait_for_zammad(admin, restart_wait, restart_poll):
            report.ok("attribute applied, Zammad is back")
        else:
            report.problem(
                "Zammad didn't come back in time after applying the attribute. "
                "Check `docker compose ps` in Zammad's folder, then run this again."
            )


async def setup_webhook(
    admin: ZammadClient, settings: Settings, report: SetupReport
) -> int:
    data = {
        "name": WEBHOOK_NAME,
        "endpoint": settings.zammad_webhook_url,
        "http_method": "post",
        "signature_token": settings.zammad_webhook_secret,
        "ssl_verify": settings.zammad_webhook_url.startswith("https://"),
        "customized_payload": False,
        "active": True,
        "note": NOTE,
    }
    existing = by_name(await admin.list("webhooks"), WEBHOOK_NAME)
    if existing is None:
        existing = await admin.create("webhooks", data)
        report.ok(f"created the webhook → {settings.zammad_webhook_url}")
    else:
        # Zammad never returns the signature token, so it is always re-sent
        await admin.update("webhooks", int(existing["id"]), data)
        report.ok(f"webhook is up to date → {settings.zammad_webhook_url}")
    return int(existing["id"])


async def setup_trigger(
    admin: ZammadClient, webhook_id: int, bot_user_id: int, report: SetupReport
) -> None:
    data = {
        "name": TRIGGER_NAME,
        "condition": trigger_condition(bot_user_id),
        "perform": {"notification.webhook": {"webhook_id": [str(webhook_id)]}},
        "activator": "action",
        "execution_condition_mode": "selective",
        "active": True,
        "note": NOTE,
    }
    existing = by_name(await admin.list("triggers"), TRIGGER_NAME)
    if existing is None:
        await admin.create("triggers", data)
        report.ok(f'created the trigger "{TRIGGER_NAME}"')
    else:
        await admin.update("triggers", int(existing["id"]), data)
        report.ok(f'trigger "{TRIGGER_NAME}" is up to date')


async def setup_no_email(admin: ZammadClient, report: SetupReport) -> None:
    """Belt and braces: Discord customers only have a `.invalid` address, the
    bot only writes notes, and this makes Zammad's trigger notifications skip
    those addresses altogether."""
    try:
        settings = await admin.list("settings")
        setting = by_name(settings, NO_REPLY_SETTING)
        if setting is None:
            report.problem(f"Zammad has no {NO_REPLY_SETTING} setting")
            return
        current = str((setting.get("state_current") or {}).get("value") or "")
        if has_fragment(current):
            report.ok("trigger emails to @discord.invalid are blocked")
            return
        await admin.update(
            "settings",
            int(setting["id"]),
            {"state_current": {"value": extended_pattern(current)}},
        )
        report.ok("blocked trigger emails to @discord.invalid")
    except ZammadError as e:
        report.problem(
            "couldn't update the no-auto-response filter (the setup token needs "
            f"admin.channel_email): {e}"
        )


def email_triggers(triggers: list[dict[str, Any]]) -> list[str]:
    return [
        str(t.get("name"))
        for t in triggers
        if t.get("active") and "notification.email" in (t.get("perform") or {})
    ]


async def check_groups(
    admin: ZammadClient, me: dict[str, Any], report: SetupReport
) -> None:
    wanted = get_ticket_categories().zammad_groups
    groups = {g.get("name"): g for g in await admin.list("groups")}
    access = me.get("group_ids") or {}
    for name in wanted:
        group = groups.get(name)
        if group is None:
            report.problem(
                f'group "{name}" doesn\'t exist. Create it in Zammad (Admin → '
                "Groups) or map the category to another group in "
                "ticket_categories.toml (zammad_group)."
            )
        elif not group.get("active", True):
            report.problem(f'group "{name}" is inactive')
        else:
            rights = access.get(str(group["id"])) or access.get(group["id"]) or []
            if "full" in rights or {"create", "change", "read"} <= set(rights):
                report.ok(f'group "{name}" exists and the bot user can work in it')
            else:
                report.problem(
                    f'the bot\'s Zammad user has no access to group "{name}". '
                    "Give it Full access (Admin → Users → the bot user → Group "
                    "permissions)."
                )


async def run_setup(
    settings: Settings,
    *,
    client: ZammadClient | None = None,
    admin: ZammadClient | None = None,
    restart_wait: float = RESTART_WAIT_SECONDS,
    restart_poll: float = RESTART_POLL_SECONDS,
) -> SetupReport:
    """`client` uses the bot's everyday token; `admin` the setup token."""
    report = SetupReport()
    if not (settings.zammad_url and settings.zammad_token):
        report.problem("ZAMMAD_URL and ZAMMAD_TOKEN must be set")
        return report
    if not settings.zammad_webhook_secret:
        report.problem("ZAMMAD_WEBHOOK_SECRET must be set")
        return report
    own_client = client is None
    client = client or ZammadClient(
        settings.zammad_url,
        settings.zammad_token,
        timeout=settings.zammad_timeout_seconds,
    )
    own_admin = admin is None
    admin = admin or ZammadClient(
        settings.zammad_url,
        settings.zammad_setup_token or settings.zammad_token,
        timeout=settings.zammad_timeout_seconds,
    )
    try:
        report.lines.append(f"Zammad at {settings.zammad_url}")
        try:
            me = await client.request("GET", "/users/me", params={"expand": "true"})
        except ZammadError as e:
            report.problem(f"can't reach Zammad with ZAMMAD_TOKEN: {e}")
            return report
        report.ok(f"bot user: {me.get('login')} (id {me.get('id')})")
        steps: list[tuple[str, Callable[[], Any]]] = [
            ("groups", lambda: check_groups(admin, me, report)),
            (
                "attribute",
                lambda: setup_attribute(admin, report, restart_wait, restart_poll),
            ),
        ]
        for label, step in steps:
            try:
                await step()
            except ZammadError as e:
                report.problem(f"{label}: {e}")
        try:
            webhook_id = await setup_webhook(admin, settings, report)
            await setup_trigger(admin, webhook_id, int(me["id"]), report)
        except ZammadError as e:
            report.problem(f"webhook/trigger: {e}")
        await setup_no_email(admin, report)
        try:
            names = email_triggers(await admin.list("triggers"))
            if names:
                report.info(
                    "active triggers that send email (they can't reach Discord "
                    "customers, listed for your information): " + ", ".join(names)
                )
        except ZammadError:
            pass
        if not settings.zammad_public_url:
            report.info(
                "ZAMMAD_PUBLIC_URL is unset: no Zammad links on staff cards, "
                "default avatar on agent replies"
            )
    finally:
        if own_client:
            await client.close()
        if own_admin:
            await admin.close()
    return report


def format_report(report: SetupReport) -> str:
    footer = (
        "Zammad is set up for the Discord sync."
        if report.success
        else f"{len(report.problems)} problem(s) need attention (marked ✗)."
    )
    return "\n".join([*report.lines, "", footer])


def redact(text: str, settings: Settings) -> str:
    for secret in (settings.zammad_token, settings.zammad_setup_token):
        if secret:
            text = re.sub(re.escape(secret), "***", text)
    return text
