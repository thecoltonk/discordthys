"""A fake Zammad REST API, modelled on a real Zammad 7.2 instance.

Everything here copies behaviour observed on the real thing (response shapes,
status codes, validation errors, trigger evaluation, webhook signing and
retries), so the sync can be tested in CI without one. The same test-suite
also runs against a real Zammad when `ZAMMAD_TEST_URL` is set (see
`conftest.py`), which is what keeps this fake honest.
"""

import asyncio
import base64
import hashlib
import hmac
import itertools
import json
import re
from datetime import datetime
from datetime import UTC
from typing import Any

import aiohttp
from aiohttp import web

ADMIN_TOKEN = "fake-admin-token"
USER = web.RequestKey("user", dict)
API = "/api/v1"
STATES = {1: "new", 2: "open", 3: "pending reminder", 4: "closed", 5: "merged"}
PRIORITIES = {1: "1 low", 2: "2 normal", 3: "3 high"}
SENDERS = {1: "Agent", 2: "Customer", 3: "System"}
TYPES = {1: "email", 5: "phone", 10: "note", 11: "web"}
DEFAULT_NO_REPLY = (
    "(mailer-daemon|postmaster|abuse|root|noreply|noreply.+?|no-reply|no-reply.+?)@.+?"
)


def now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def error(status: int, message: str) -> web.Response:
    return web.json_response({"error": message, "error_human": message}, status=status)


class FakeZammad:
    def __init__(self) -> None:
        self.ids = itertools.count(10)
        self.users: dict[int, dict[str, Any]] = {}
        self.tokens: dict[str, int] = {}
        self.groups: dict[int, dict[str, Any]] = {}
        self.tickets: dict[int, dict[str, Any]] = {}
        self.articles: dict[int, dict[str, Any]] = {}
        self.attachments: dict[int, bytes] = {}
        self.images: dict[str, bytes] = {}
        self.attributes: dict[int, dict[str, Any]] = {}
        self.webhooks: dict[int, dict[str, Any]] = {}
        self.triggers: dict[int, dict[str, Any]] = {}
        self.settings: dict[int, dict[str, Any]] = {
            139: {
                "id": 139,
                "name": "send_no_auto_response_reg_exp",
                "state_current": {"value": DEFAULT_NO_REPLY},
            }
        }
        self.user_columns = {"login", "firstname", "lastname", "email", "note"}
        # Test controls
        self.requests: list[tuple[str, str]] = []
        self.fail_next = 0  # answer this many API calls with HTTP 500
        self.fail_after_commit = 0  # do the write, then answer 500 (lost reply)
        self.down_for = 0  # refuse this many calls (as during a restart)
        self.deliveries: list[dict[str, Any]] = []
        self.pending: set[asyncio.Task[None]] = set()
        self.webhook_retry_seconds = 0.05
        self.runner: web.AppRunner | None = None
        self.url = ""

        admin = self.add_user(
            login="admin@example.com",
            firstname="Ada",
            lastname="Admin",
            roles=["Admin", "Agent"],
        )
        self.tokens[ADMIN_TOKEN] = admin["id"]
        self.add_group("Users")

    # -- seeding -------------------------------------------------------------

    def add_user(self, **data: Any) -> dict[str, Any]:
        user = {
            "id": next(self.ids),
            "login": data.get("login") or data.get("email"),
            "firstname": data.get("firstname") or "",
            "lastname": data.get("lastname") or "",
            "email": data.get("email") or "",
            "image": None,
            "active": True,
            "roles": data.get("roles") or ["Customer"],
            "group_ids": {},
            "note": data.get("note") or "",
            "created_at": now(),
            "updated_at": now(),
        }
        for column in self.user_columns - set(user):
            user[column] = data.get(column)
        self.users[user["id"]] = user
        return user

    def add_group(self, name: str) -> dict[str, Any]:
        group = {"id": next(self.ids), "name": name, "active": True}
        self.groups[group["id"]] = group
        return group

    # -- server lifecycle ------------------------------------------------------

    async def start(self) -> str:
        app = web.Application(middlewares=[self.middleware])
        r = app.router
        r.add_get(API + "/users/me", self.users_me)
        r.add_get(API + "/users/search", self.users_search)
        r.add_get(API + "/users/image/{hash}", self.users_image)
        r.add_post(API + "/users", self.users_create)
        r.add_get(API + "/users/{id}", self.users_show)
        r.add_put(API + "/users/{id}", self.users_update)
        r.add_get(API + "/tickets/search", self.tickets_search)
        r.add_post(API + "/tickets", self.tickets_create)
        r.add_get(API + "/tickets/{id}", self.tickets_show)
        r.add_put(API + "/tickets/{id}", self.tickets_update)
        r.add_post(API + "/ticket_articles", self.articles_create)
        r.add_get(API + "/ticket_articles/by_ticket/{id}", self.articles_by_ticket)
        r.add_get(API + "/ticket_attachment/{t}/{a}/{id}", self.attachment)
        r.add_post(API + "/object_manager_attributes_execute_migrations", self.migrate)
        for name in ("groups", "webhooks", "triggers", "settings"):
            r.add_get(API + f"/{name}", self.admin_list)
            r.add_post(API + f"/{name}", self.admin_create)
            r.add_put(API + f"/{name}/{{id}}", self.admin_update)
        r.add_get(API + "/object_manager_attributes", self.admin_list)
        r.add_post(API + "/object_manager_attributes", self.admin_create)
        r.add_put(API + "/object_manager_attributes/{id}", self.admin_update)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
        self.url = f"http://127.0.0.1:{port}"
        return self.url

    async def stop(self) -> None:
        for task in list(self.pending):
            task.cancel()
        if self.runner is not None:
            await self.runner.cleanup()

    async def settle(self) -> None:
        """Wait for webhook deliveries in flight."""
        while self.pending:
            await asyncio.gather(*list(self.pending), return_exceptions=True)

    # -- plumbing ----------------------------------------------------------------

    @web.middleware
    async def middleware(self, request: web.Request, handler: Any) -> Any:
        self.requests.append((request.method, request.path))
        if request.match_info.route.name == "image" or "/users/image/" in request.path:
            return await handler(request)  # avatars need no login (as in Zammad)
        if self.down_for > 0:
            self.down_for -= 1
            return web.Response(status=502, text="Bad Gateway")
        if self.fail_next > 0:
            self.fail_next -= 1
            return error(500, "simulated outage")
        auth = request.headers.get("Authorization", "")
        match = re.fullmatch(r"Token token=(.+)", auth)
        user_id = self.tokens.get(match.group(1)) if match else None
        if user_id is None:
            return error(401, "Invalid token")
        behalf = request.headers.get("X-On-Behalf-Of")
        if behalf:
            if "Admin" not in self.users[user_id]["roles"]:
                return error(403, "Not authorized")
            other = next(
                (
                    u
                    for u in self.users.values()
                    if behalf in (u["login"], str(u["id"]))
                ),
                None,
            )
            if other is None:
                return error(403, f"No such user '{behalf}'")
            user_id = other["id"]
        request[USER] = self.users[user_id]
        response = await handler(request)
        if (
            self.fail_after_commit > 0
            and request.method in ("POST", "PUT")
            and response.status < 300
        ):
            self.fail_after_commit -= 1
            return error(500, "simulated lost reply")
        return response

    def is_admin(self, request: web.Request) -> bool:
        return "Admin" in request[USER]["roles"]

    def is_agent(self, request: web.Request) -> bool:
        return "Agent" in request[USER]["roles"]

    def can_use_group(self, user: dict[str, Any], group_id: int) -> bool:
        rights = user["group_ids"].get(str(group_id)) or []
        return "full" in rights or "create" in rights

    def expand_user(self, user: dict[str, Any] | None) -> dict[str, Any] | None:
        if user is None:
            return None
        out = {k: v for k, v in user.items() if v not in (None, "")}
        out["role_ids"] = []
        return out

    def expand_ticket(self, ticket: dict[str, Any], *, nested: bool) -> dict[str, Any]:
        out = dict(ticket)
        group = self.groups[ticket["group_id"]]
        out["state"] = STATES[ticket["state_id"]]
        out["priority"] = PRIORITIES[ticket["priority_id"]]
        out["group"] = group["name"]
        out["customer"] = self.users[ticket["customer_id"]]["login"]
        out["article_ids"] = [
            a["id"] for a in self.articles.values() if a["ticket_id"] == ticket["id"]
        ]
        if nested:  # webhook payload: related objects are objects, not names
            out["group"] = dict(group)
            out["priority"] = {"id": ticket["priority_id"], "name": out["priority"]}
            out["customer"] = self.expand_user(self.users[ticket["customer_id"]])
            out["updated_by"] = self.expand_user(
                self.users.get(ticket["updated_by_id"])
            )
        return out

    def expand_article(
        self, article: dict[str, Any], *, nested: bool
    ) -> dict[str, Any]:
        out = dict(article)
        out["sender"] = SENDERS[article["sender_id"]]
        out["type"] = TYPES[article["type_id"]]
        author = self.users.get(article["created_by_id"])
        if nested:
            out["created_by"] = self.expand_user(author)
            out["attachments"] = [
                {
                    **a,
                    "url": f"http://zammad.example.com/api/v1/ticket_attachment/"
                    f"{article['ticket_id']}/{article['id']}/{a['id']}",
                }
                for a in article["attachments"]
            ]
        else:
            out["created_by"] = author["login"] if author else None
        return out

    # -- users -------------------------------------------------------------------

    async def users_me(self, request: web.Request) -> web.Response:
        return web.json_response(request[USER])

    async def users_show(self, request: web.Request) -> web.Response:
        user = self.users.get(int(request.match_info["id"]))
        return web.json_response(user) if user else error(404, "Couldn't find User")

    async def users_search(self, request: web.Request) -> web.Response:
        if not self.is_agent(request):
            return error(403, "Not authorized")
        query = request.query.get("query", "").lower()
        # Like the search backends, ":" in a query is field syntax: a login such
        # as "discord:123" can't be searched for as-is
        if ":" in query:
            return web.json_response([])
        found = [
            u
            for u in self.users.values()
            if query in u["login"].lower() or query in (u["email"] or "").lower()
        ]
        return web.json_response(found)

    async def users_image(self, request: web.Request) -> web.Response:
        data = self.images.get(request.match_info["hash"])
        if data is None:
            return web.Response(body=b"GIF89a", content_type="image/gif")
        return web.Response(body=data, content_type="image/png")

    async def users_create(self, request: web.Request) -> web.Response:
        if not self.is_agent(request):
            return error(403, "Not authorized")
        data = await request.json()
        email = (data.get("email") or "").lower()
        login = data.get("login") or email
        for user in self.users.values():
            if email and user["email"] == email:
                return error(
                    422, f"Email address '{email}' is already used for another user."
                )
            if user["login"] == login:
                return error(422, "Login has already been taken")
        user = self.add_user(**{**data, "email": email, "login": login})
        return web.json_response(user, status=201)

    async def users_update(self, request: web.Request) -> web.Response:
        user = self.users.get(int(request.match_info["id"]))
        if user is None:
            return error(404, "Couldn't find User")
        data = await request.json()
        if "group_ids" in data and not self.is_admin(request):
            return error(403, "Not authorized")
        for key, value in data.items():
            if key in self.user_columns or key in ("group_ids", "image", "active"):
                user[key] = value
        user["updated_at"] = now()
        return web.json_response(user)

    # -- tickets -------------------------------------------------------------------

    def find_group(self, data: dict[str, Any]) -> dict[str, Any] | None:
        if "group_id" in data:
            return self.groups.get(int(data["group_id"]))
        return next(
            (g for g in self.groups.values() if g["name"] == data.get("group")), None
        )

    def lookup(
        self, table: dict[int, str], data: dict[str, Any], key: str
    ) -> int | None:
        if f"{key}_id" in data:
            return int(data[f"{key}_id"])
        if key in data:
            return next((i for i, name in table.items() if name == data[key]), -1)
        return None

    async def tickets_create(self, request: web.Request) -> web.Response:
        if not self.is_agent(request):
            return error(403, "Not authorized")
        data = await request.json()
        group = self.find_group(data)
        if group is None:
            return error(
                422, "No lookup value found for 'group': " + repr(data.get("group"))
            )
        if not self.can_use_group(request[USER], group["id"]):
            return error(403, "Not authorized")
        if data.get("customer_id") not in self.users:
            return error(422, "Customer must exist")
        priority = self.lookup(PRIORITIES, data, "priority")
        if priority == -1:
            return error(
                422, f"No lookup value found for 'priority': {data['priority']!r}"
            )
        state = self.lookup(STATES, data, "state")
        ticket_id = next(self.ids)
        ticket = {
            "id": ticket_id,
            "number": str(60000 + ticket_id),
            "title": data.get("title") or "",
            "group_id": group["id"],
            "state_id": state or 1,
            "priority_id": priority or 2,
            "customer_id": data["customer_id"],
            "owner_id": 1,
            "created_by_id": request[USER]["id"],
            "updated_by_id": request[USER]["id"],
            "created_at": now(),
            "updated_at": now(),
        }
        self.tickets[ticket_id] = ticket
        if data.get("article"):
            problem = self.add_article(
                request, {**data["article"], "ticket_id": ticket_id}
            )
            if isinstance(problem, web.Response):
                del self.tickets[ticket_id]
                return problem
        return web.json_response(self.expand_ticket(ticket, nested=False), status=201)

    async def tickets_show(self, request: web.Request) -> web.Response:
        ticket = self.tickets.get(int(request.match_info["id"]))
        if ticket is None:
            return error(404, "Couldn't find Ticket")
        if not self.can_use_group(request[USER], ticket["group_id"]):
            return error(403, "Not authorized")
        if request.query.get("expand") == "true":
            return web.json_response(self.expand_ticket(ticket, nested=False))
        return web.json_response(ticket)

    async def tickets_search(self, request: web.Request) -> web.Response:
        query = request.query.get("query", "").strip('"').lower()
        found = [
            t
            for t in self.tickets.values()
            if query in t["title"].lower()
            and self.can_use_group(request[USER], t["group_id"])
        ]
        return web.json_response(found)

    async def tickets_update(self, request: web.Request) -> web.Response:
        ticket = self.tickets.get(int(request.match_info["id"]))
        if ticket is None:
            return error(404, "Couldn't find Ticket")
        if not self.can_use_group(request[USER], ticket["group_id"]):
            return error(403, "Not authorized")
        data = await request.json()
        changed: set[str] = set()
        state = self.lookup(STATES, data, "state")
        if state == -1:
            return error(422, f"No lookup value found for 'state': {data['state']!r}")
        if state is not None and state != ticket["state_id"]:
            ticket["state_id"] = state
            changed.add("state_id")
        if "group" in data or "group_id" in data:
            group = self.find_group(data)
            if group is None:
                return error(422, "No lookup value found for 'group'")
            if group["id"] != ticket["group_id"]:
                ticket["group_id"] = group["id"]
                changed.add("group_id")
        ticket["updated_by_id"] = request[USER]["id"]
        ticket["updated_at"] = now()
        article = None
        if data.get("article"):
            article = self.add_article(
                request, {**data["article"], "ticket_id": ticket["id"]}, fire=False
            )
            if isinstance(article, web.Response):
                return article
        if changed or article:
            self.fire(ticket, article, changed)
        return web.json_response(self.expand_ticket(ticket, nested=False))

    # -- articles ------------------------------------------------------------------

    def add_article(
        self, request: web.Request, data: dict[str, Any], *, fire: bool = True
    ) -> dict[str, Any] | web.Response:
        ticket = self.tickets.get(int(data.get("ticket_id") or 0))
        if ticket is None:
            return error(404, "Couldn't find Ticket")
        if not self.can_use_group(request[USER], ticket["group_id"]):
            return error(403, "Not authorized")
        user = request[USER]
        type_id = next(
            (i for i, n in TYPES.items() if n == data.get("type", "note")), None
        )
        if type_id is None:
            return error(422, "No lookup value found for 'type'")
        sender_default = "Agent" if "Agent" in user["roles"] else "Customer"
        sender = data.get("sender", sender_default)
        sender_id = next((i for i, n in SENDERS.items() if n == sender), None)
        if sender_id is None:
            return error(422, "No lookup value found for 'sender'")
        article_id = next(self.ids)
        attachments = []
        for item in data.get("attachments") or []:
            try:
                content = base64.b64decode(item["data"], validate=True)
            except Exception:  # noqa: BLE001
                return error(422, "Invalid base64 for attachment")
            attachment_id = next(self.ids)
            self.attachments[attachment_id] = content
            attachments.append(
                {
                    "id": attachment_id,
                    "filename": item["filename"],
                    "size": str(len(content)),
                    "preferences": {"Mime-Type": item.get("mime-type")},
                }
            )
        origin = self.users.get(data.get("origin_by_id") or 0)
        author = origin or user
        article = {
            "id": article_id,
            "ticket_id": ticket["id"],
            "type_id": type_id,
            "sender_id": sender_id,
            "from": f"{author['firstname']} {author['lastname']}".strip(),
            "subject": data.get("subject"),
            "message_id": data.get("message_id"),
            "content_type": data.get("content_type", "text/html"),
            "body": data.get("body", ""),
            "internal": bool(data.get("internal", False)),
            "origin_by_id": data.get("origin_by_id"),
            "created_by_id": user["id"],
            "updated_by_id": user["id"],
            "created_at": now(),
            "updated_at": now(),
            "attachments": attachments,
        }
        self.articles[article_id] = article
        ticket["updated_at"] = now()
        ticket["updated_by_id"] = user["id"]
        if fire:
            self.fire(ticket, article, set())
        return article

    async def articles_create(self, request: web.Request) -> web.Response:
        if not self.is_agent(request):
            return error(403, "Not authorized")
        result = self.add_article(request, await request.json())
        if isinstance(result, web.Response):
            return result
        return web.json_response(self.expand_article(result, nested=False), status=201)

    async def articles_by_ticket(self, request: web.Request) -> web.Response:
        ticket_id = int(request.match_info["id"])
        if ticket_id not in self.tickets:
            return error(404, "Couldn't find Ticket")
        return web.json_response(
            [
                self.expand_article(a, nested=False)
                for a in self.articles.values()
                if a["ticket_id"] == ticket_id
            ]
        )

    async def attachment(self, request: web.Request) -> web.Response:
        data = self.attachments.get(int(request.match_info["id"]))
        if data is None:
            return error(404, "No such file")
        return web.Response(body=data, content_type="application/octet-stream")

    # -- admin resources -----------------------------------------------------------

    def table(self, request: web.Request) -> tuple[str, dict[int, dict[str, Any]]]:
        name = request.path[len(API) + 1 :].split("/")[0]
        return name, {
            "groups": self.groups,
            "webhooks": self.webhooks,
            "triggers": self.triggers,
            "settings": self.settings,
            "object_manager_attributes": self.attributes,
        }[name]

    def shown(self, name: str, item: dict[str, Any]) -> dict[str, Any]:
        if name == "webhooks" and item.get("signature_token"):
            return {**item, "signature_token": "**********"}  # never returned
        return item

    async def admin_list(self, request: web.Request) -> web.Response:
        name, table = self.table(request)
        if name != "groups" and not self.is_admin(request):
            return error(403, "Not authorized (user)!")
        return web.json_response([self.shown(name, i) for i in table.values()])

    async def admin_create(self, request: web.Request) -> web.Response:
        name, table = self.table(request)
        if not self.is_admin(request):
            return error(403, "Not authorized (user)!")
        data = await request.json()
        if name == "object_manager_attributes":
            if re.search(r"_ids?$", data.get("name", "")):
                return error(
                    422, "Name can't be used because *_id and *_ids are not allowed"
                )
            data = {**data, "to_create": True, "to_migrate": True}
        if (problem := self.endpoint_problem(name, data)) is not None:
            return problem
        if any(i.get("name") == data.get("name") for i in table.values()):
            return error(422, "Name has already been taken")
        item = {**data, "id": next(self.ids)}
        table[item["id"]] = item
        return web.json_response(self.shown(name, item), status=201)

    def endpoint_problem(self, name: str, data: dict[str, Any]) -> web.Response | None:
        """Zammad resolves a webhook's hostname when it is saved."""
        if name == "webhooks" and ".invalid" in str(data.get("endpoint", "")):
            return error(
                422,
                "Endpoint The provided endpoint is invalid, it could not be "
                "resolved to a safe IP address.",
            )
        return None

    async def admin_update(self, request: web.Request) -> web.Response:
        name, table = self.table(request)
        if not self.is_admin(request):
            return error(403, "Not authorized (user)!")
        item = table.get(int(request.match_info["id"]))
        if item is None:
            return error(404, "Not found")
        data = await request.json()
        if (problem := self.endpoint_problem(name, data)) is not None:
            return problem
        item.update(data)
        return web.json_response(self.shown(name, item))

    async def migrate(self, request: web.Request) -> web.Response:
        if not self.is_admin(request):
            return error(403, "Not authorized (user)!")
        pending = False
        for attribute in self.attributes.values():
            if attribute.pop("to_create", False) | attribute.pop("to_migrate", False):
                pending = True
                attribute["to_create"] = attribute["to_migrate"] = False
                if attribute.get("object") == "User":
                    self.user_columns.add(attribute["name"])
        if pending:
            self.down_for = 2  # Zammad restarts its processes after a migration
        return web.json_response({})

    # -- triggers and webhooks --------------------------------------------------------

    def matches(
        self,
        condition: dict[str, Any],
        ticket: dict[str, Any],
        article: dict[str, Any] | None,
        changed: set[str],
    ) -> bool:
        if "conditions" in condition:
            results = [
                self.matches(c, ticket, article, changed)
                for c in condition["conditions"]
            ]
            return any(results) if condition.get("operator") == "OR" else all(results)
        name, operator, value = (
            condition["name"],
            condition["operator"],
            condition.get("value") or [],
        )
        if name == "customer.login":
            login = self.users[ticket["customer_id"]]["login"]
            assert operator == "starts with one of"
            return any(login.startswith(prefix) for prefix in value)
        if name in ("ticket.state_id", "ticket.group_id"):
            assert operator == "has changed"
            return name.split(".")[1] in changed
        if name.startswith("article."):
            if article is None:
                return False
            actual = article[name.split(".")[1]]
            if name == "article.internal":
                return str(actual).lower() == str(value).lower()
            hit = str(actual) in [str(v) for v in value]
            return hit if operator == "is" else not hit
        raise AssertionError(f"fake Zammad can't evaluate condition {name}")

    def fire(
        self, ticket: dict[str, Any], article: dict[str, Any] | None, changed: set[str]
    ) -> None:
        for trigger in self.triggers.values():
            perform = (trigger.get("perform") or {}).get("notification.webhook")
            if not trigger.get("active") or not perform:
                continue
            if not self.matches(trigger["condition"], ticket, article, changed):
                continue
            for webhook_id in perform["webhook_id"]:
                webhook = self.webhooks.get(int(webhook_id))
                if webhook and webhook.get("active"):
                    task = asyncio.ensure_future(
                        self.deliver(trigger, webhook, ticket["id"], article)
                    )
                    self.pending.add(task)
                    task.add_done_callback(self.pending.discard)

    async def deliver(
        self,
        trigger: dict[str, Any],
        webhook: dict[str, Any],
        ticket_id: int,
        article: dict[str, Any] | None,
    ) -> None:
        """Like TriggerWebhookJob: the payload is built when the job runs, the
        body is signed with HMAC-SHA1, and a failure is retried 4 more times."""
        record: dict[str, Any] = {"statuses": []}
        self.deliveries.append(record)
        async with aiohttp.ClientSession() as session:
            for attempt in range(5):
                await asyncio.sleep(0 if attempt == 0 else self.webhook_retry_seconds)
                body = json.dumps(
                    {
                        "ticket": self.expand_ticket(
                            self.tickets[ticket_id], nested=True
                        ),
                        "article": self.expand_article(article, nested=True)
                        if article
                        else None,
                    }
                ).encode()
                headers = {
                    "Content-Type": "application/json; charset=utf-8",
                    "User-Agent": "Zammad User Agent",
                    "X-Zammad-Trigger": trigger["name"],
                    "X-Zammad-Delivery": f"fake-{len(self.deliveries)}",
                }
                if webhook.get("signature_token"):
                    digest = hmac.new(
                        webhook["signature_token"].encode(), body, hashlib.sha1
                    ).hexdigest()
                    headers["X-Hub-Signature"] = f"sha1={digest}"
                try:
                    async with session.post(
                        webhook["endpoint"], data=body, headers=headers
                    ) as response:
                        record["statuses"].append(response.status)
                        if response.status < 300:
                            return
                except aiohttp.ClientError:
                    record["statuses"].append(None)
