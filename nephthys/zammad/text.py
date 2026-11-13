"""Pure helpers for the Zammad sync: signatures, text conversion, naming."""

import hashlib
import hmac
import re
from html import unescape
from html.parser import HTMLParser

DISCORD_MESSAGE_MAX = 2000
# Leaves room for the "**Name (Support):** " prefix of the fallback message
CHUNK_MAX = 1900
WEBHOOK_NAME_MAX = 80
# Discord rejects webhook usernames containing these (case-insensitive)
FORBIDDEN_NAME_PARTS = ("discord", "clyde", "```", "@", "#", ":")
FORBIDDEN_NAMES = ("everyone", "here")


def sign(secret: str, body: bytes) -> str:
    """Zammad's webhook signature: `sha1=<hex HMAC-SHA1 of the raw body>`."""
    return "sha1=" + hmac.new(secret.encode(), body, hashlib.sha1).hexdigest()


def verify_signature(secret: str, body: bytes, header: str | None) -> bool:
    """Constant-time check of the `X-Hub-Signature` header."""
    if not secret or not header:
        return False
    return hmac.compare_digest(sign(secret, body), header.strip().lower())


def marker(kind: str, ident: int | str) -> str:
    """Stored as the article's `message_id` in Zammad. It marks an article as
    written by this bot (never mirror it back) and makes retries idempotent."""
    return f"<discord-{kind}-{ident}@discord.invalid>"


def is_marker(message_id: object) -> bool:
    return isinstance(message_id, str) and message_id.startswith("<discord-")


def customer_login(discord_id: int) -> str:
    return f"discord:{discord_id}"


def customer_email(discord_id: int) -> str:
    # .invalid can never resolve (RFC 2606), so nothing sent there can arrive
    return f"{discord_id}@discord.invalid"


def webhook_username(name: str, fallback: str) -> str:
    """A username Discord's webhook API accepts (1-80 characters, no reserved
    words). Falls back when nothing usable is left."""
    cleaned = _strip_forbidden(name)[:WEBHOOK_NAME_MAX].strip()
    if not cleaned or cleaned.lower() in FORBIDDEN_NAMES:
        return fallback
    return cleaned


def _strip_forbidden(name: str) -> str:
    cleaned = " ".join((name or "").split())
    for part in FORBIDDEN_NAME_PARTS:
        cleaned = re.sub(re.escape(part), "", cleaned, flags=re.IGNORECASE)
    return " ".join(cleaned.split())


def agent_username(template: str, name: str, fallback: str) -> str:
    """The webhook username for an agent's reply: `template` (copy
    `[zammad] agent_name`, e.g. "{name} (Support)") filled with the agent's
    Zammad name. Discord allows at most 80 characters, so a long name is
    shortened (ending in "…") to keep the rest of the template, e.g. the
    "(Support)" suffix. No usable name -> `fallback`."""
    clean = _strip_forbidden(name)
    if not clean or clean.lower() in FORBIDDEN_NAMES:
        return webhook_username(fallback, "Support")
    full = template.format(name=clean)
    room = WEBHOOK_NAME_MAX - len(template.format(name=""))
    if len(full) > WEBHOOK_NAME_MAX and room > 1:
        full = template.format(name=clean[: room - 1].rstrip() + "…")
    return webhook_username(full, fallback)


def display_name(user: object, fallback: str = "") -> str:
    """ "Firstname Lastname" from a Zammad user object (webhook or REST)."""
    if not isinstance(user, dict):
        return fallback
    parts = [str(user.get(k) or "").strip() for k in ("firstname", "lastname")]
    name = " ".join(p for p in parts if p and p != "-")
    return name or fallback


class _Converter(HTMLParser):
    """Zammad article HTML -> Discord-flavoured plain text."""

    BLOCKS = frozenset(
        {"p", "div", "tr", "ul", "ol", "table", "h1", "h2", "h3", "h4", "pre"}
    )
    BOLD = frozenset({"b", "strong", "h1", "h2", "h3", "h4"})
    ITALIC = frozenset({"i", "em"})
    SKIP = frozenset({"style", "script", "head", "title"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.links: list[str | None] = []
        self.skip = 0
        self.quote = 0
        self.link_start = 0

    def emit(self, text: str) -> None:
        if not self.skip:
            self.out.append(text)

    def newline(self) -> None:
        self.emit("\n" + ("> " if self.quote else ""))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.SKIP:
            self.skip += 1
        elif tag == "br":
            self.newline()
        elif tag == "blockquote":
            self.quote += 1
            self.newline()
        elif tag == "li":
            self.newline()
            self.emit("• ")
        elif tag in self.BLOCKS:
            self.newline()
        if tag in self.BOLD:
            self.emit("**")
        elif tag in self.ITALIC:
            self.emit("*")
        elif tag == "code":
            self.emit("`")
        elif tag == "a":
            href = dict(attrs).get("href")
            self.links.append(href if href and href.startswith("http") else None)
            self.link_start = len(self.out)
        elif tag == "img":
            alt = dict(attrs).get("alt")
            if alt:
                self.emit(f"[{alt}]")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
            return
        if tag in self.BOLD:
            self.emit("**")
        elif tag in self.ITALIC:
            self.emit("*")
        elif tag == "code":
            self.emit("`")
        elif tag == "a" and self.links:
            href = self.links.pop()
            label = "".join(self.out[self.link_start :]).strip()
            if href and href != label:
                self.emit(f" ({href})")
        if tag == "blockquote":
            self.quote = max(0, self.quote - 1)
            self.emit("\n")
        elif tag in self.BLOCKS:
            self.newline()

    def handle_data(self, data: str) -> None:
        self.emit(re.sub(r"\s+", " ", data))


def html_to_text(html: str) -> str:
    parser = _Converter()
    parser.feed(html)
    parser.close()
    text = "".join(parser.out).replace("\xa0", " ")
    # Empty emphasis pairs left by empty tags, e.g. <b></b>
    text = text.replace("****", "").replace("``", "")
    lines = [line.strip() for line in text.split("\n")]
    lines = ["" if line == ">" else line for line in lines]
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def article_text(body: str | None, content_type: str | None) -> str:
    """The text of a Zammad article, ready to post in Discord."""
    body = body or ""
    if (content_type or "").lower().startswith("text/html"):
        return html_to_text(body)
    return unescape(body).strip() if "&" in body and "<" not in body else body.strip()


def split_message(text: str, limit: int = CHUNK_MAX) -> list[str]:
    """Split on paragraph, line or word boundaries so each part fits a Discord
    message."""
    chunks: list[str] = []
    rest = text.strip()
    while len(rest) > limit:
        window = rest[:limit]
        cut = max(window.rfind("\n\n"), window.rfind("\n"))
        if cut < limit // 2:
            cut = window.rfind(" ")
        if cut < limit // 2:
            cut = limit
        chunks.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    if rest:
        chunks.append(rest)
    return chunks


def mirrored_body(name: str, role: str, content: str, notes: list[str]) -> str:
    """Body of a Zammad article mirrored from Discord:
    "<name> (Player|Staff via Discord)" then the message."""
    parts = [f"{name} ({role} via Discord)"]
    if content.strip():
        parts.append(content.strip())
    if notes:
        parts.append("\n".join(notes))
    return "\n\n".join(parts)
