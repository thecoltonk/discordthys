"""A small async client for the parts of Zammad's REST API the sync uses.

Only documented REST endpoints are called; Zammad itself is never modified.
"""

import base64
import json
import logging
from typing import Any

import aiohttp

log = logging.getLogger(__name__)

# 4xx responses that can fix themselves (bad token being rotated, rate limits,
# Zammad restarting behind nginx); everything else in 4xx won't
RETRIABLE_4XX = {401, 403, 408, 409, 425, 429}


class ZammadError(Exception):
    """A failed Zammad API call. `status` is None for network errors."""

    def __init__(self, message: str, *, status: int | None = None):
        super().__init__(message)
        self.status = status

    @property
    def permanent(self) -> bool:
        """Retrying the same request can't succeed (e.g. 404, 422)."""
        return (
            self.status is not None
            and 400 <= self.status < 500
            and self.status not in RETRIABLE_4XX
        )


def error_detail(text: str) -> str:
    """Zammad's `error` message when the body is JSON, else the raw text."""
    try:
        data = json.loads(text)
    except ValueError:
        return text[:300]
    if isinstance(data, dict) and data.get("error"):
        return str(data.get("error_human") or data["error"])[:300]
    return text[:300]


class ZammadClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: float = 20.0,
        on_behalf_of: str | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self._token = token
        # Zammad's X-On-Behalf-Of (admin tokens only): act as another user.
        # The bot never sets it; the integration tests use it to play agents.
        self._on_behalf_of = on_behalf_of
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: aiohttp.ClientSession | None = None
        self._me: dict[str, Any] | None = None

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            headers = {
                "Authorization": f"Token token={self._token}",
                "Accept": "application/json",
                "User-Agent": "cloudsail-support-bot",
            }
            if self._on_behalf_of:
                headers["X-On-Behalf-Of"] = self._on_behalf_of
            self._session = aiohttp.ClientSession(
                timeout=self._timeout, headers=headers
            )
        return self._session

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
        raw: bool = False,
    ) -> Any:
        url = f"{self.base_url}/api/v1{path}"
        try:
            async with self._get_session().request(
                method, url, json=json, params=params
            ) as response:
                if response.status >= 400:
                    raise ZammadError(
                        f"{method} {path} -> HTTP {response.status}: "
                        f"{error_detail(await response.text())}",
                        status=response.status,
                    )
                if raw:
                    return await response.read()
                if response.status == 204:
                    return None
                return await response.json(content_type=None)
        except (TimeoutError, aiohttp.ClientError) as e:
            raise ZammadError(f"{method} {path} failed: {type(e).__name__}: {e}") from e

    # -- identity ----------------------------------------------------------

    async def me(self) -> dict[str, Any]:
        """The API user (cached). Its id marks articles the bot wrote."""
        if self._me is None:
            self._me = dict(await self.request("GET", "/users/me"))
        return self._me

    # -- users ---------------------------------------------------------------

    async def create_user(self, data: dict[str, Any]) -> dict[str, Any]:
        return await self.request("POST", "/users", json=data)

    async def update_user(self, user_id: int, data: dict[str, Any]) -> dict[str, Any]:
        return await self.request("PUT", f"/users/{user_id}", json=data)

    async def get_user(self, user_id: int) -> dict[str, Any]:
        return await self.request("GET", f"/users/{user_id}")

    async def find_user(self, *, login: str, email: str) -> dict[str, Any] | None:
        """Exact-login lookup. Searches by email because the search backend
        (Elasticsearch or the SQL fallback) tokenises ":" in logins."""
        for query in (email, login):
            found = await self.request(
                "GET", "/users/search", params={"query": query, "limit": 10}
            )
            for user in found or []:
                if user.get("login") == login:
                    return user
        return None

    # -- tickets -------------------------------------------------------------

    async def create_ticket(self, data: dict[str, Any]) -> dict[str, Any]:
        return await self.request("POST", "/tickets", json=data)

    async def get_ticket(self, ticket_id: int) -> dict[str, Any]:
        """With `expand`, so `state`, `group` and `priority` are names."""
        return await self.request(
            "GET", f"/tickets/{ticket_id}", params={"expand": "true"}
        )

    async def update_ticket(
        self, ticket_id: int, data: dict[str, Any]
    ) -> dict[str, Any]:
        return await self.request("PUT", f"/tickets/{ticket_id}", json=data)

    async def find_ticket(
        self, *, title: str, customer_id: int
    ) -> dict[str, Any] | None:
        found = await self.request(
            "GET", "/tickets/search", params={"query": f'"{title}"', "limit": 10}
        )
        if isinstance(found, dict):  # older shape: {"tickets": [...], "assets": ...}
            assets = (found.get("assets") or {}).get("Ticket") or {}
            found = list(assets.values())
        for ticket in found or []:
            if (
                ticket.get("title") == title
                and ticket.get("customer_id") == customer_id
            ):
                return ticket
        return None

    # -- articles ------------------------------------------------------------

    async def create_article(self, data: dict[str, Any]) -> dict[str, Any]:
        return await self.request("POST", "/ticket_articles", json=data)

    async def articles(self, ticket_id: int) -> list[dict[str, Any]]:
        return await self.request("GET", f"/ticket_articles/by_ticket/{ticket_id}")

    async def find_article(
        self, ticket_id: int, message_id: str
    ) -> dict[str, Any] | None:
        for article in await self.articles(ticket_id):
            if article.get("message_id") == message_id:
                return article
        return None

    async def download_attachment(
        self, ticket_id: int, article_id: int, attachment_id: int
    ) -> bytes:
        return await self.request(
            "GET",
            f"/ticket_attachment/{ticket_id}/{article_id}/{attachment_id}",
            raw=True,
        )

    # -- admin (zammad-setup) --------------------------------------------------

    async def list(self, resource: str) -> list[dict[str, Any]]:
        return await self.request(
            "GET", f"/{resource}", params={"per_page": 500, "expand": "true"}
        )

    async def create(self, resource: str, data: dict[str, Any]) -> dict[str, Any]:
        return await self.request("POST", f"/{resource}", json=data)

    async def update(
        self, resource: str, item_id: int, data: dict[str, Any]
    ) -> dict[str, Any]:
        return await self.request("PUT", f"/{resource}/{item_id}", json=data)


def encode_attachment(
    filename: str, data: bytes, mime_type: str | None
) -> dict[str, str]:
    return {
        "filename": filename,
        "data": base64.b64encode(data).decode("ascii"),
        "mime-type": mime_type or "application/octet-stream",
    }
