"""#tickets categories: structure, priority, owning role and blurbs.

Loaded from `copy/ticket_categories.toml` (next to copy.toml, mounted read-only)
at startup. The defaults below are PRODUCT.md §2. Regenerate the shipped file
with `python -m nephthys.core.ticket_categories > copy/ticket_categories.toml`.

Roles are referenced by name - "staff", "store" or "mod" (STAFF_ROLE_ID,
STORE_ROLE_ID, MOD_ROLE_ID) - or by a numeric role ID, so the file holds no
deployment-specific IDs by default.

Unlike copy.toml this file is read once at startup: category keys are baked
into buttons on live tickets, so changing them mid-flight would orphan them.
"""

import logging
import re
import sys
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import model_validator
from pydantic import ValidationError

log = logging.getLogger(__name__)

Priority = Literal["low", "medium", "high"]
PRIORITIES: tuple[Priority, ...] = ("low", "medium", "high")
ROLE_NAMES = ("staff", "store", "mod")
KEY_PATTERN = re.compile(r"^[a-z0-9_]{1,20}$")
# The opener's last button row holds Close Ticket: 4 rows x 5 buttons remain
MAX_BUTTONS = 20
BLURB_MAX = 1500


class _Base(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str = Field(description="Stable ID used in buttons and stats (a-z, 0-9, _)")
    label: str = Field(max_length=80)
    emoji: str | None = None
    blurb: str = Field(max_length=BLURB_MAX)

    @model_validator(mode="after")
    def check_key(self):
        if not KEY_PATTERN.match(self.key):
            raise ValueError(f"key {self.key!r} must match {KEY_PATTERN.pattern}")
        return self

    @property
    def display(self) -> str:
        return f"{self.emoji} {self.label}" if self.emoji else self.label


class SubCategory(_Base):
    priority: Priority = "medium"


class Category(_Base):
    # Who gets pinged: "staff" | "store" | "mod" | a numeric role ID
    role: str = "staff"
    # Used when there are no subcategories
    priority: Priority = "medium"
    subcategories: tuple[SubCategory, ...] = ()
    # The Zammad group new tickets of this category go to (default: the label)
    zammad_group: str | None = Field(default=None, max_length=160)

    @model_validator(mode="after")
    def check_category(self):
        if self.role not in ROLE_NAMES and not self.role.isdigit():
            raise ValueError(
                f"{self.key}: role must be one of {ROLE_NAMES} or a numeric role ID"
            )
        keys = [s.key for s in self.subcategories]
        if len(keys) != len(set(keys)):
            raise ValueError(f"{self.key}: duplicate subcategory keys")
        if len(keys) > MAX_BUTTONS:
            raise ValueError(f"{self.key}: at most {MAX_BUTTONS} subcategories")
        if not self.zammad_group:
            # Same-named group by default (frozen model, hence __setattr__)
            object.__setattr__(self, "zammad_group", self.label)
        return self

    @property
    def is_leaf(self) -> bool:
        return not self.subcategories

    @property
    def group(self) -> str:
        return self.zammad_group or self.label

    def sub(self, key: str | None) -> SubCategory | None:
        return next((s for s in self.subcategories if s.key == key), None)


DEFAULT_ZAMMAD_PRIORITIES: dict[str, str] = {
    "low": "1 low",
    "medium": "2 normal",
    "high": "3 high",
}


class ZammadMapping(BaseModel):
    """How #tickets values map onto Zammad (only used when the sync is on)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # priority -> the name of a Zammad ticket priority
    priorities: dict[Priority, str] = Field(
        default_factory=lambda: dict(DEFAULT_ZAMMAD_PRIORITIES)  # type: ignore[arg-type]
    )

    @model_validator(mode="after")
    def check_priorities(self):
        missing = [p for p in PRIORITIES if not self.priorities.get(p)]
        if missing:
            raise ValueError(f"zammad.priorities is missing: {', '.join(missing)}")
        return self

    def priority(self, priority: str | None) -> str:
        return self.priorities.get(priority or "medium") or self.priorities["medium"]  # type: ignore[call-overload]


class TicketCategories(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    categories: tuple[Category, ...]
    zammad: ZammadMapping = ZammadMapping()

    @property
    def zammad_groups(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(c.group for c in self.categories))

    @model_validator(mode="after")
    def check_categories(self):
        keys = [c.key for c in self.categories]
        if not keys:
            raise ValueError("at least one category is required")
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate category keys")
        if len(keys) > MAX_BUTTONS:
            raise ValueError(f"at most {MAX_BUTTONS} categories")
        return self

    def get(self, key: str | None) -> Category | None:
        return next((c for c in self.categories if c.key == key), None)

    def label_for(self, key: str | None) -> str | None:
        category = self.get(key)
        return category.display if category else key


DEFAULT_TICKET_CATEGORIES = TicketCategories(
    categories=(
        Category(
            key="server_issue",
            label="Server Issue",
            emoji="❗",
            role="staff",
            blurb="What kind of server issue is it?",
            subcategories=(
                SubCategory(
                    key="urgent",
                    label="Urgent",
                    emoji="💥",
                    priority="high",
                    blurb=(
                        "Sorry about that! Staff have been alerted. Please tell us "
                        "**what happened**, your **IGN**, **where and when** it "
                        "happened, and attach **screenshots** if you have them."
                    ),
                ),
                SubCategory(
                    key="bug_report",
                    label="Bug Report",
                    emoji="🔧",
                    priority="medium",
                    blurb=(
                        "Thanks for reporting a bug! Please tell us **what happened**, "
                        "your **IGN**, **where and when** it happened, and attach "
                        "**screenshots** if you have them."
                    ),
                ),
                SubCategory(
                    key="other_technical",
                    label="Other Technical",
                    emoji="💻",
                    priority="low",
                    blurb=(
                        "Please tell us **what's going on**, your **IGN**, **where "
                        "and when** it happened, and attach **screenshots** if you "
                        "have them."
                    ),
                ),
            ),
        ),
        Category(
            key="store",
            label="Store",
            emoji="🛒",
            role="store",
            priority="medium",
            blurb=(
                "Please tell us your **IGN**, **what you bought**, **roughly when** "
                "you bought it, and **what's wrong**."
            ),
        ),
        Category(
            key="bans_appeals",
            label="Bans & Appeals",
            emoji="🔨",
            role="mod",
            priority="medium",
            blurb=(
                "Please tell us your **IGN** and **what happened**. If you're banned "
                "from Discord, use https://appeals.cloudsail.uk instead."
            ),
        ),
        Category(
            key="reports",
            label="Reports",
            emoji="🚩",
            role="mod",
            priority="medium",
            blurb=(
                "Please tell us **who** you're reporting, **what** they did, **where "
                "and when** it happened, and attach any **evidence** (screenshots or "
                "video)."
            ),
        ),
        Category(
            key="other",
            label="Other",
            emoji="❓",
            role="staff",
            priority="low",
            blurb=(
                "Please describe what you need help with. This also covers "
                "**partnerships** and **website issues**."
            ),
        ),
    )
)


class TicketCategoriesError(ValueError):
    pass


def parse_ticket_categories(text: str, source: str = "ticket_categories.toml"):
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise TicketCategoriesError(f"{source}: invalid TOML: {e}") from e
    try:
        return TicketCategories.model_validate(data)
    except ValidationError as e:
        problems = "\n".join(
            f"  - [{'.'.join(str(p) for p in err['loc'])}] {err['msg']}"
            for err in e.errors()
        )
        raise TicketCategoriesError(f"{source}: invalid categories:\n{problems}") from e


_current = DEFAULT_TICKET_CATEGORIES


def configure_ticket_categories(
    path: str | Path, *, required: bool
) -> TicketCategories:
    global _current
    p = Path(path)
    if not p.is_file():
        if required:
            raise TicketCategoriesError(f"TICKET_CATEGORIES_FILE {p} doesn't exist")
        log.warning(f"{p} not found; using the built-in #tickets categories")
        _current = DEFAULT_TICKET_CATEGORIES
        return _current
    _current = parse_ticket_categories(p.read_text(encoding="utf-8"), str(p))
    log.info(f"Loaded {len(_current.categories)} #tickets categories from {p}")
    return _current


def get_ticket_categories() -> TicketCategories:
    return _current


def use_ticket_categories(categories: TicketCategories) -> None:
    """Replace the categories without a file (tests)."""
    global _current
    _current = categories


# ---------------------------------------------------------------------------
# Generating copy/ticket_categories.toml
# ---------------------------------------------------------------------------


def _s(value: str) -> str:
    out = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{out}"'


HEADER = """\
# Cloudsail Support: #tickets categories (PRODUCT.md §2).
#
# Read once at startup - restart the bot after editing:  docker compose restart bot
# Validate first with:  python -m nephthys.core.ticket_categories --check <file>
#
# Each [[categories]] entry becomes a button on a new ticket. A category either
# has [[categories.subcategories]] (the player picks one next) or is a "leaf"
# that uses its own priority.
#
#   key       stable ID stored on tickets (a-z, 0-9, _). Don't rename keys that
#             open tickets use; add a new category instead.
#   label     button text (max 80 characters); emoji is optional
#   role      who gets pinged on the staff alert: "staff", "store", "mod"
#             (STAFF_ROLE_ID / STORE_ROLE_ID / MOD_ROLE_ID) or a numeric role ID.
#             High priority tickets also ping the Staff role.
#   priority  "low", "medium" or "high"
#   blurb     shown after the pick: tell the player what to include
#   zammad_group  the Zammad group tickets of this category are created in
#             (only used when the Zammad sync is on; default: the label).
#             `nephthys zammad-setup` checks that every group exists.
#
# [zammad.priorities] maps low/medium/high to Zammad priority names.
#
# Regenerate the defaults with:
#   python -m nephthys.core.ticket_categories > copy/ticket_categories.toml
"""


def dump_toml(categories: TicketCategories = DEFAULT_TICKET_CATEGORIES) -> str:
    lines = [HEADER]
    lines.append("")
    lines.append("[zammad.priorities]")
    for p in PRIORITIES:
        lines.append(f"{p} = {_s(categories.zammad.priorities[p])}")
    for c in categories.categories:
        lines.append("")
        lines.append("[[categories]]")
        lines.append(f"key = {_s(c.key)}")
        lines.append(f"label = {_s(c.label)}")
        if c.emoji:
            lines.append(f"emoji = {_s(c.emoji)}")
        lines.append(f"role = {_s(c.role)}")
        lines.append(f"zammad_group = {_s(c.group)}")
        if c.is_leaf:
            lines.append(f"priority = {_s(c.priority)}")
        lines.append(f"blurb = {_s(c.blurb)}")
        for sub in c.subcategories:
            lines.append("")
            lines.append("  [[categories.subcategories]]")
            lines.append(f"  key = {_s(sub.key)}")
            lines.append(f"  label = {_s(sub.label)}")
            if sub.emoji:
                lines.append(f"  emoji = {_s(sub.emoji)}")
            lines.append(f"  priority = {_s(sub.priority)}")
            lines.append(f"  blurb = {_s(sub.blurb)}")
    return "\n".join(lines) + "\n"


def _main(argv: list[str]) -> int:
    if len(argv) > 1 and argv[1] == "--check":
        path = Path(argv[2] if len(argv) > 2 else "copy/ticket_categories.toml")
        try:
            parse_ticket_categories(path.read_text(encoding="utf-8"), str(path))
        except (TicketCategoriesError, OSError) as e:
            print(e, file=sys.stderr)
            return 1
        print("categories OK")
        return 0
    sys.stdout.write(dump_toml())
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
