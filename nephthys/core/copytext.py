"""All player- and staff-facing text, loaded from `copy/copy.toml`.

The defaults below are the shipped text. `copy/copy.toml` (generated from them
with `python -m nephthys.core.copytext > copy/copy.toml`) is mounted read-only
into the container, like `kb/`. Edits apply without a restart: the file is
checked for changes every few seconds, and an invalid edit is logged and
ignored (the last good copy stays live).

Templates use `{placeholder}` fields. Each field declares which placeholders it
may use, and unknown ones are rejected when the file is loaded, so a typo
can't crash the bot mid-conversation. Use `{{` and `}}` for literal braces.

Not covered: slash-command names/descriptions (registered with Discord at
startup) and log-channel diagnostics (for maintainers, not copy).
"""

import logging
import re
import string
import sys
import threading
import time
import tomllib
from pathlib import Path
from typing import Any
from typing import ClassVar

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import model_validator
from pydantic import ValidationError

log = logging.getLogger(__name__)

RELOAD_CHECK_SECONDS = 5.0


def placeholders_in(template: str) -> set[str]:
    """The `{names}` used in a format string (raises ValueError if malformed)."""
    names: set[str] = set()
    for _, field_name, _, _ in string.Formatter().parse(template):
        if field_name is None:
            continue
        if field_name == "" or field_name.isdigit():
            raise ValueError("positional placeholders like {} aren't allowed")
        names.add(field_name.split(".")[0].split("[")[0])
    return names


class Section(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    # field name -> placeholders it may use (fields not listed may use none)
    PLACEHOLDERS: ClassVar[dict[str, set[str]]] = {}

    @model_validator(mode="after")
    def check_placeholders(self) -> "Section":
        for name in type(self).model_fields:
            value = getattr(self, name)
            if not isinstance(value, str):
                continue
            allowed = self.PLACEHOLDERS.get(name, set())
            try:
                used = placeholders_in(value)
            except ValueError as e:
                raise ValueError(f"{name}: invalid template ({e})") from e
            unknown = used - allowed
            if unknown:
                hint = ", ".join(f"{{{p}}}" for p in sorted(allowed)) or "none"
                raise ValueError(
                    f"{name}: unknown placeholder(s) "
                    f"{', '.join('{' + u + '}' for u in sorted(unknown))}; allowed: {hint}"
                )
        return self


class CommunityCopy(Section):
    """#community-help messages players see."""

    PLACEHOLDERS: ClassVar[dict[str, set[str]]] = {
        "auto_reply": {"faq_url"},
        "resolved": {"user"},
        "resolved_stale": {"days"},
        "reopened": {"user"},
        "thread_name_fallback": {"name"},
    }

    auto_reply: str = Field(
        default="someone should be along to help you soon! in the meantime check out the "
        "[FAQ]({faq_url}). once your question is answered, hit the button below to "
        "mark it as resolved :D",
        description="Posted in each new ticket thread when FAQ_URL is set.",
    )
    auto_reply_no_faq: str = Field(
        default="someone should be along to help you soon! once your question is answered, "
        "hit the button below to mark it as resolved :D",
        description="Posted in each new ticket thread when FAQ_URL is unset.",
    )
    resolve_button: str = Field(default="Mark as resolved", description="Button label.")
    reopen_button: str = Field(default="Re-open", description="Button label.")
    resolved: str = Field(
        default="marked as resolved by {user} — got another question? make a new post!",
        description="Closing message ({user} is a mention that doesn't ping).",
    )
    resolved_stale: str = Field(
        default="closing this one since it's been quiet for {days} days — got another "
        "question? make a new post!",
        description="Closing message for automatic stale closes.",
    )
    reopened: str = Field(
        default="re-opened by {user}! someone will be along to help soon.",
        description="Posted when a ticket is re-opened (button, /reopen or the poster replying).",
    )
    thread_name_fallback: str = Field(
        default="Question from {name}",
        description="Thread name when the question has no text (e.g. only an image).",
    )
    something_went_wrong: str = Field(
        default="something went wrong there — staff have been told!",
        description="Private reply when a button fails unexpectedly.",
    )


class ErrorsCopy(Section):
    """Private (ephemeral) refusals."""

    not_allowed: str = "only the person who asked or a staff member can do that, sorry!"
    already_resolved: str = "this ticket is already resolved!"
    not_resolved: str = "this ticket isn't resolved, so there's nothing to re-open."
    ticket_missing: str = "i couldn't find a ticket for this thread."
    staff_only: str = "this command is for staff only."
    not_a_ticket_thread: str = "use this inside a ticket thread."
    already_rated: str = "someone already rated this one."
    ai_already_answered: str = "this has already been answered, thanks!"


class CommandsCopy(Section):
    """Staff slash commands: /faq, /ip, /resolve, /reopen, /waiting."""

    PLACEHOLDERS: ClassVar[dict[str, set[str]]] = {
        "faq_reply": {"faq_url"},
        "ip_reply": {"server_address"},
        "waiting_line": {"id", "ref", "title", "link", "last_message"},
        "waiting_more": {"limit"},
    }

    faq_reply: str = Field(
        default="check out the FAQ, it might have your answer: {faq_url}",
        description="Posted publicly by /faq.",
    )
    faq_unconfigured: str = Field(
        default="no FAQ configured.",
        description="Private reply to /faq when FAQ_URL is unset.",
    )
    ip_reply: str = Field(
        default="the server address is **{server_address}** — see you in game!",
        description="Posted publicly by /ip.",
    )
    ip_unconfigured: str = "the server address hasn't been set up yet (SERVER_ADDRESS)."
    resolved: str = Field(
        default="resolved ✅", description="Private reply to /resolve."
    )
    reopened: str = Field(
        default="re-opened 🔄", description="Private reply to /reopen."
    )
    waiting_empty: str = "nothing waiting — nice work, team!"
    waiting_line: str = Field(
        default="• [{ref} {title}]({link}) — last message {last_message}",
        description=(
            "One line per waiting ticket. {ref} is #<id> or T-<number>; "
            "{last_message} is a relative time."
        ),
    )
    waiting_more: str = "_…and more. Showing the oldest {limit}._"


class CardCopy(Section):
    """Staff ticket cards in STAFF_CHANNEL_ID."""

    PLACEHOLDERS: ClassVar[dict[str, set[str]]] = {
        "asked_by": {"user", "past_tickets"},
        "past_tickets_one": {"count"},
        "past_tickets_many": {"count"},
        "status_waiting": {"reopened"},
        "status_claimed": {"claimer", "reopened"},
        "status_resolved": {"by", "credit"},
        "resolved_by": {"user"},
        "credit": {"user"},
        "category_ai": {"category"},
        "ai_line": {"mode", "confidence", "outcome"},
        "csat_value": {"rating"},
        "escalated_value": {"id", "link"},
        "escalated_to_value": {"number", "link"},
    }

    untitled: str = "Untitled question"
    no_text: str = "*(no text — see the thread)*"
    someone: str = Field(
        default="someone", description="Stands in for an unknown user."
    )
    field_asked_by: str = "Asked by"
    asked_by: str = "{user} · {past_tickets}"
    first_ticket: str = "first ticket"
    past_tickets_one: str = "{count} past ticket"
    past_tickets_many: str = "{count} past tickets"
    field_status: str = "Status"
    status_waiting: str = "🟡 Waiting for staff{reopened}"
    status_claimed: str = "🔵 Claimed by {claimer}{reopened}"
    status_resolved: str = "✅ Resolved {by}{credit}"
    resolved_by: str = "by {user}"
    resolved_automatically: str = "automatically (stale)"
    credit: str = " · credit {user}"
    no_credit: str = " · no staff credit"
    reopened_suffix: str = " (re-opened)"
    field_category: str = "Category"
    category_none: str = "—"
    category_ai: str = Field(
        default="{category} (AI)", description="An AI-set category."
    )
    category_placeholder: str = "Set a category…"
    field_ai: str = "🤖 AI"
    ai_line: str = "{mode} answer ({confidence}) — {outcome}"
    ai_pending: str = "awaiting feedback"
    ai_helpful: str = "👍 rated helpful"
    ai_not_helpful: str = "👎 rated not helpful"
    ai_fixed: str = "✅ player said it fixed it"
    ai_needs_human: str = "🙋 player still needs a human"
    open_thread_button: str = "Open thread"
    category_pending: str = "category not picked yet"
    awaiting_first_message: str = "*(waiting for the player's first message)*"
    field_priority: str = "Priority"
    field_csat: str = "CSAT"
    csat_value: str = "{rating}/5"
    field_escalated: str = "Escalated from"
    escalated_value: str = "[#{id}]({link})"
    field_escalated_to: str = "Escalated to"
    escalated_to_value: str = "[T-{number}]({link})"


class StatsCopy(Section):
    """Shared by /stats and the daily summary."""

    PLACEHOLDERS: ClassVar[dict[str, set[str]]] = {
        "csat_value": {"average", "count"},
        "resolved_value": {"resolved", "without_staff"},
        "field_waiting": {"days"},
        "leaderboard_line": {"rank", "user", "count"},
        "minutes": {"n"},
        "hours": {"n"},
        "days": {"n"},
    }

    surface_community: str = "#community-help"
    surface_tickets: str = "#tickets"
    field_opened: str = "Opened"
    field_resolved: str = "Resolved"
    resolved_value: str = "{resolved} ({without_staff} without staff)"
    field_median: str = "Median first staff response"
    field_open_now: str = "Open (unclaimed) now"
    field_in_progress: str = "In progress now"
    field_waiting: str = "Waiting > {days} days"
    field_by_category: str = "Opened by category"
    uncategorised: str = "Uncategorised"
    field_by_priority: str = "By priority"
    priority_unset: str = "not picked yet"
    field_csat: str = "CSAT average"
    csat_value: str = "{average}/5 from {count} rating(s)"
    csat_none: str = "no ratings yet"
    field_top_helpers: str = "Top helpers (resolved, by claimer)"
    leaderboard_line: str = "{rank}. {user} — {count}"
    leaderboard_empty: str = "nobody yet"
    not_available: str = "n/a"
    minutes: str = "{n} min"
    hours: str = "{n} h"
    days: str = "{n} days"


class SummaryCopy(Section):
    """The daily summary post."""

    PLACEHOLDERS: ClassVar[dict[str, set[str]]] = {
        "field_waiting": {"days", "count"},
        "waiting_line": {"id", "ref", "title", "link", "last_message"},
        "waiting_more": {"count"},
        "ai_shadow_accuracy": {
            "day_pct",
            "day_helpful",
            "day_rated",
            "week_pct",
            "week_helpful",
            "week_rated",
        },
        "ai_shadow_counts": {"posted", "unrated"},
        "ai_public": {"posted", "fixed", "needs_human", "no_response", "week_fix_rate"},
        "ai_attempts": {"attempts", "skipped"},
        "ai_attempts_reasons": {"attempts", "skipped", "reasons"},
    }

    field_by_category: str = "By category"
    field_waiting: str = "Waiting > {days} days ({count})"
    waiting_none: str = "none 🎉"
    waiting_line: str = "• [{ref} {title}]({link}) — {last_message}"
    waiting_more: str = "…and {count} more (see /waiting)"
    field_ai: str = "🤖 AI"
    ai_none: str = "no AI activity"
    ai_shadow_accuracy: str = (
        "**Shadow accuracy** (👍 / rated): 24h {day_pct} ({day_helpful}/{day_rated}), "
        "7d {week_pct} ({week_helpful}/{week_rated})"
    )
    ai_shadow_counts: str = "Suggestions 24h: {posted} posted, {unrated} unrated"
    ai_public: str = (
        "**Public answers** 24h: {posted} posted — {fixed} fixed, {needs_human} "
        "needed a human, {no_response} no response; 7d fix rate {week_fix_rate}"
    )
    ai_attempts: str = "Attempts 24h: {attempts} ({skipped} not posted)"
    ai_attempts_reasons: str = (
        "Attempts 24h: {attempts} ({skipped} not posted: {reasons})"
    )


class AICopy(Section):
    """AI answers (shadow suggestions for staff, public answers for players)."""

    PLACEHOLDERS: ClassVar[dict[str, set[str]]] = {
        "rated_helpful": {"user"},
        "rated_not_helpful": {"user"},
        "outcome_fixed": {"user"},
        "outcome_needs_human": {"user"},
    }

    fixed_button: str = "This fixed it"
    need_human_button: str = "I still need a human"
    need_human_reply: str = (
        "no worries — a human will be along to help soon! feel free to add any extra "
        "details while you wait."
    )
    shadow_field_confidence: str = "Confidence"
    shadow_field_sources: str = "Sources"
    no_sources: str = "none"
    helpful_button: str = "Helpful"
    not_helpful_button: str = "Not helpful"
    field_outcome: str = "Outcome"
    rated_helpful: str = "👍 helpful — rated by {user}"
    rated_not_helpful: str = "👎 not helpful — rated by {user}"
    outcome_fixed: str = "✅ {user} said this fixed it"
    outcome_needs_human: str = "🙋 {user} still needs a human"


class TicketsCopy(Section):
    """#tickets: the panel, private ticket threads, closing, CSAT and escalation."""

    PLACEHOLDERS: ClassVar[dict[str, set[str]]] = {
        "thread_name": {"name", "number"},
        "opener_header": {"number"},
        "welcome": {"user"},
        "escalated_note": {"link"},
        "selected": {"choice"},
        "created": {"link"},
        "too_many_open": {"count", "links"},
        "closed": {"user"},
        "csat_thanks": {"rating"},
        "reopened": {"user"},
        "alert_ping": {"roles", "priority"},
        "escalated_public": {"user", "link"},
        "escalated_quote": {"quote"},
        "escalate_done": {"link"},
        "already_escalated": {"link"},
    }

    create_button: str = "Create Ticket"
    thread_name: str = Field(
        default="{name} (T-{number})",
        description="Private thread name (max 100 characters; longer is cut).",
    )
    opener_header: str = "## Ticket T-{number}"
    welcome: str = Field(
        default=(
            "Hey {user}, thanks for reaching out! Pick the option below that best "
            "fits what you need help with."
        ),
        description="Shown at the top of every new ticket.",
    )
    escalated_note: str = Field(
        default="This ticket was moved here from {link} so we can help you privately.",
        description="Added to tickets created with /escalate.",
    )
    selected: str = Field(
        default="You have selected: **{choice}**",
        description="One line per pick; the lines stack up.",
    )
    close_button: str = "Close Ticket"
    created: str = Field(
        default="your ticket is ready: {link}",
        description="Private reply to Create Ticket.",
    )
    too_many_open: str = Field(
        default=(
            "you already have {count} open ticket(s): {links} — please use one of "
            "those, or close one first."
        ),
        description="Private reply when TICKETS_MAX_OPEN_PER_USER is reached.",
    )
    create_failed: str = (
        "sorry, i couldn't open a ticket right now. please try again in a minute, "
        "or ask in #community-help."
    )
    not_your_ticket: str = (
        "only the person who opened this ticket or staff can do that."
    )
    already_picked: str = "that's already been chosen!"
    option_gone: str = "that option isn't available anymore — please ask staff."
    closed: str = Field(
        default="this ticket has been closed by {user}. thanks for reaching out!",
        description="Posted when a ticket is closed (button or /resolve).",
    )
    csat_prompt: str = "how did we do? rate your support from 1 (bad) to 5 (great):"
    csat_thanks: str = "thanks for the feedback — you rated us {rating}/5 ⭐"
    csat_not_opener: str = "only the person who opened this ticket can rate it."
    csat_already: str = "you've already rated this ticket — thanks!"
    reopened: str = "this ticket has been re-opened by {user}."
    alert_ping: str = Field(
        default="{roles} new **{priority}** ticket",
        description="Text above the staff alert card; {roles} are the pinged roles.",
    )
    priority_low: str = "🟢 Low"
    priority_medium: str = "🟡 Medium"
    priority_high: str = "🔴 High"
    escalated_public: str = Field(
        default=(
            "this has been moved to a private ticket ({link}) so staff can help "
            "{user} privately. 🔒"
        ),
        description="Posted in the #community-help thread by /escalate before locking it.",
    )
    escalated_quote: str = Field(
        default="**Original message:**\n{quote}",
        description="Copy of the public question, when /escalate deletes the original.",
    )
    escalate_done: str = "escalated to {link}"
    already_escalated: str = "this was already escalated: {link}"
    not_community_thread: str = "use this inside a #community-help ticket thread."
    tickets_disabled: str = "#tickets isn't set up (TICKETS_CHANNEL_ID)."


HEX_COLOUR = re.compile(r"^#?[0-9a-fA-F]{6}$")


class EmbedStyle(Section):
    """Presentation of one kind of embed. Empty strings mean "leave it out"."""

    # Fields that aren't text templates (shown differently in the generated file)
    NON_TEMPLATE: ClassVar[set[str]] = {"enabled", "thumbnail_url"}

    title: str = ""
    description: str = ""
    color: str = Field(
        default="", description="Hex colour like #5865F2 (empty = none)."
    )
    footer: str = ""
    thumbnail_url: str = Field(
        default="", description="Optional image URL (https://...)."
    )

    @model_validator(mode="after")
    def check_style(self) -> "EmbedStyle":
        for name in type(self).model_fields:
            value = getattr(self, name)
            if name.startswith("color") and value and not HEX_COLOUR.match(value):
                raise ValueError(
                    f"{name}: must be a hex colour like #5865F2, got {value!r}"
                )
        if self.thumbnail_url and not self.thumbnail_url.startswith(
            ("https://", "http://")
        ):
            raise ValueError("thumbnail_url: must start with https:// (or be empty)")
        return self


def colour_value(hex_colour: str) -> int | None:
    """'#5865F2' -> 0x5865F2; '' -> None."""
    return int(hex_colour.lstrip("#"), 16) if hex_colour else None


BLURPLE = "#5865F2"
GOLD = "#F1C40F"
GREEN = "#2ECC71"
ORANGE = "#E67E22"
DARK_TEAL = "#11806A"


class StickyEmbed(EmbedStyle):
    """The "how it works" sticky at the bottom of #community-help."""

    PLACEHOLDERS: ClassVar[dict[str, set[str]]] = {"description": {"faq_url"}}

    title: str = "How #community-help works"
    description: str = Field(
        default=(
            "**Post your question as a new message in this channel** and a thread will "
            "be opened for it. Anyone can help out, and staff will be along soon!\n\n"
            "• Keep all follow-ups inside your thread.\n"
            "• Once you're sorted, hit **Mark as resolved** in your thread.\n"
            "• Got a new question later? Just post a new message.\n\n"
            "Before posting, check the FAQ: {faq_url}"
        ),
        description="Used when FAQ_URL is set.",
    )
    description_no_faq: str = Field(
        default=(
            "**Post your question as a new message in this channel** and a thread will "
            "be opened for it. Anyone can help out, and staff will be along soon!\n\n"
            "• Keep all follow-ups inside your thread.\n"
            "• Once you're sorted, hit **Mark as resolved** in your thread.\n"
            "• Got a new question later? Just post a new message."
        ),
        description="Used when FAQ_URL is unset.",
    )
    color: str = BLURPLE


class TicketsPanelEmbed(EmbedStyle):
    """The sticky "Need help?" panel in #tickets."""

    PLACEHOLDERS: ClassVar[dict[str, set[str]]] = {"description": {"community_channel"}}

    title: str = "Need help?"
    description: str = (
        "First, check the known issues and see if {community_channel} can sort it. "
        "If not, hit the button below to open a private thread with the team."
    )
    color: str = BLURPLE


class TicketOpenerEmbed(EmbedStyle):
    """The #tickets opener. Off by default (plain text, as before); set enabled = true
    to send it as an embed. {body} is the welcome, picks and blurb."""

    PLACEHOLDERS: ClassVar[dict[str, set[str]]] = {
        "title": {"number"},
        "description": {"body"},
        "footer": {"number"},
    }

    enabled: bool = False
    title: str = "Ticket T-{number}"
    description: str = "{body}"
    color: str = BLURPLE


class TicketClosedEmbed(EmbedStyle):
    """The #tickets closing message. Off by default (plain text); enabled = true sends
    it as an embed. {message} is [tickets] closed."""

    PLACEHOLDERS: ClassVar[dict[str, set[str]]] = {"description": {"message"}}

    enabled: bool = False
    description: str = "{message}"
    color: str = GREEN


class StaffCardEmbed(EmbedStyle):
    """Staff ticket cards (both channels). The colour follows the ticket status."""

    PLACEHOLDERS: ClassVar[dict[str, set[str]]] = {
        "title": {"id", "title"},
        "private_title": {"number", "category"},
        "description": {"question"},
        "footer": {"id", "ref"},
    }
    NON_TEMPLATE: ClassVar[set[str]] = {"thumbnail_url"}

    title: str = Field(default="#{id} · {title}", description="#community-help cards.")
    private_title: str = Field(
        default="T-{number} · {category}", description="#tickets cards."
    )
    description: str = "{question}"
    color_waiting: str = GOLD
    color_claimed: str = BLURPLE
    color_resolved: str = GREEN
    footer: str = "Ticket #{id}"


class DailySummaryEmbed(EmbedStyle):
    """The daily summary (one embed per channel)."""

    PLACEHOLDERS: ClassVar[dict[str, set[str]]] = {
        "title": {"surface"},
        "description": {"since", "until"},
    }

    title: str = "📊 {surface} daily summary"
    description: str = "{since} → {until}"
    color: str = BLURPLE


class StatsEmbed(EmbedStyle):
    """/stats (one embed per channel)."""

    PLACEHOLDERS: ClassVar[dict[str, set[str]]] = {
        "title": {"surface", "days"},
        "title_one_day": {"surface"},
        "description": {"surface", "days"},
    }

    title: str = "{surface} — last {days} days"
    title_one_day: str = "{surface} — last 1 day"
    color: str = BLURPLE


class WaitingEmbed(EmbedStyle):
    """/waiting. {lines} is the list of tickets."""

    PLACEHOLDERS: ClassVar[dict[str, set[str]]] = {"description": {"lines"}}

    title: str = "Waiting for staff"
    description: str = "{lines}"
    color: str = ORANGE


class AIShadowEmbed(EmbedStyle):
    """AI suggestions posted to staff (shadow mode)."""

    PLACEHOLDERS: ClassVar[dict[str, set[str]]] = {
        "title": {"id", "title"},
        "description": {"answer"},
    }

    title: str = "🤖 AI suggestion (shadow) · #{id} {title}"
    description: str = "{answer}"
    color: str = DARK_TEAL
    footer: str = "Not shown to the player. Would this have been a good answer?"


class AIPublicEmbed(EmbedStyle):
    """AI answers posted to players (public mode)."""

    PLACEHOLDERS: ClassVar[dict[str, set[str]]] = {
        "description": {"answer"},
        "footer": {"sources"},
    }

    title: str = "🤖 Automated answer"
    description: str = "{answer}"
    color: str = DARK_TEAL
    footer: str = (
        "I'm a bot answering from the Cloudsail knowledge base, and I can be wrong. "
        "Sources: {sources}"
    )


class EmbedsCopy(BaseModel):
    """Presentation of every embed: title, description, color, footer, thumbnail."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sticky: StickyEmbed = StickyEmbed()
    tickets_panel: TicketsPanelEmbed = TicketsPanelEmbed()
    ticket_opener: TicketOpenerEmbed = TicketOpenerEmbed()
    ticket_closed: TicketClosedEmbed = TicketClosedEmbed()
    staff_card: StaffCardEmbed = StaffCardEmbed()
    daily_summary: DailySummaryEmbed = DailySummaryEmbed()
    stats: StatsEmbed = StatsEmbed()
    waiting: WaitingEmbed = WaitingEmbed()
    ai_shadow: AIShadowEmbed = AIShadowEmbed()
    ai_public: AIPublicEmbed = AIPublicEmbed()


class Copy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    community: CommunityCopy = CommunityCopy()
    errors: ErrorsCopy = ErrorsCopy()
    commands: CommandsCopy = CommandsCopy()
    card: CardCopy = CardCopy()
    stats: StatsCopy = StatsCopy()
    summary: SummaryCopy = SummaryCopy()
    ai: AICopy = AICopy()
    tickets: TicketsCopy = TicketsCopy()
    embeds: EmbedsCopy = EmbedsCopy()


DEFAULT_COPY = Copy()


class CopyError(ValueError):
    pass


# Keys that moved into [embeds.*]; old files get a pointer instead of a vague
# "extra inputs are not permitted"
MOVED_KEYS: dict[tuple[str, str], str] = {
    ("sticky", "title"): "embeds.sticky.title",
    ("sticky", "body"): "embeds.sticky.description",
    ("sticky", "body_no_faq"): "embeds.sticky.description_no_faq",
    ("tickets", "panel_title"): "embeds.tickets_panel.title",
    ("tickets", "panel_body"): "embeds.tickets_panel.description",
    ("card", "title"): "embeds.staff_card.title",
    ("card", "private_title"): "embeds.staff_card.private_title",
    ("card", "footer"): "embeds.staff_card.footer",
    ("summary", "title"): "embeds.daily_summary.title",
    ("summary", "period"): "embeds.daily_summary.description",
    ("stats", "stats_title"): "embeds.stats.title",
    ("stats", "stats_title_one_day"): "embeds.stats.title_one_day",
    ("commands", "waiting_title"): "embeds.waiting.title",
    ("ai", "shadow_title"): "embeds.ai_shadow.title",
    ("ai", "shadow_footer"): "embeds.ai_shadow.footer",
    ("ai", "public_title"): "embeds.ai_public.title",
    ("ai", "public_footer"): "embeds.ai_public.footer",
}


def _load_toml(text: str, source: str) -> dict[str, Any]:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise CopyError(f"{source}: invalid TOML: {e}") from e
    moved = []
    for (section, key), new in MOVED_KEYS.items():
        if isinstance(data.get(section), dict) and key in data[section]:
            new_section, new_key = new.rsplit(".", 1)
            moved.append(f"  - [{section}] {key} moved to [{new_section}] {new_key}")
    if moved:
        raise CopyError(f"{source}: some keys have moved:\n" + "\n".join(moved))
    if isinstance(data.get("sticky"), dict) and not data["sticky"]:
        data.pop("sticky")  # an empty leftover [sticky] header is harmless
    return data


def _validate(data: dict[str, Any], source: str) -> Copy:
    try:
        return Copy.model_validate(data)
    except ValidationError as e:
        problems = "\n".join(
            f"  - [{'.'.join(str(p) for p in err['loc'])}] {err['msg']}"
            for err in e.errors()
        )
        raise CopyError(f"{source}: invalid copy:\n{problems}") from e


def merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge two parsed copy files; keys in `override` win."""
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = merge(out[key], value)
        else:
            out[key] = value
    return out


def parse_copy(text: str, source: str = "copy.toml") -> Copy:
    """Parse TOML text. Missing keys fall back to the defaults."""
    return _validate(_load_toml(text, source), source)


def load_copy_file(path: Path) -> Copy:
    return parse_copy(path.read_text(encoding="utf-8"), str(path))


def load_layers(paths: list[Path]) -> Copy:
    """Defaults <- each existing file in order. Every file is validated on its
    own first, so errors name the file they're in."""
    merged: dict[str, Any] = {}
    for path in paths:
        if not path.is_file():
            continue
        data = _load_toml(path.read_text(encoding="utf-8"), str(path))
        _validate(data, str(path))
        merged = merge(merged, data)
    return _validate(merged, " + ".join(str(p) for p in paths))


class CopyStore:
    """Holds the live copy (defaults <- copy.toml <- copy.local.toml) and
    reloads it when either file changes."""

    def __init__(self, path: Path | None = None, override: Path | None = None):
        self.path = path
        self.override = override
        self.paths = [p for p in (path, override) if p is not None]
        self._copy = DEFAULT_COPY
        self._mtimes: tuple[float | None, ...] = ()
        self._checked_at = 0.0
        self._lock = threading.Lock()

    def _current_mtimes(self) -> tuple[float | None, ...]:
        out: list[float | None] = []
        for p in self.paths:
            try:
                out.append(p.stat().st_mtime)
            except OSError:
                out.append(None)
        return tuple(out)

    def load(self, *, required: bool, override_required: bool = False) -> Copy:
        """Initial load. Raises `CopyError` for an invalid file, or a missing one
        when it's required (i.e. set explicitly)."""
        if not self.paths:
            return self._copy
        if self.path is not None and not self.path.is_file():
            if required:
                raise CopyError(f"COPY_FILE {self.path} doesn't exist")
            log.warning(f"{self.path} not found; using the built-in default text")
        if self.override is not None and not self.override.is_file():
            if override_required:
                raise CopyError(f"COPY_OVERRIDE_FILE {self.override} doesn't exist")
            log.info(f"No copy override at {self.override}")
        self._copy = load_layers(self.paths)
        self._mtimes = self._current_mtimes()
        self._checked_at = time.monotonic()
        loaded = [str(p) for p in self.paths if p.is_file()]
        if loaded:
            log.info(f"Loaded copy from {' + '.join(loaded)}")
        return self._copy

    def get(self) -> Copy:
        if self.paths:
            now = time.monotonic()
            if now - self._checked_at >= RELOAD_CHECK_SECONDS:
                with self._lock:
                    self._checked_at = now
                    self._maybe_reload()
        return self._copy

    def _maybe_reload(self) -> None:
        mtimes = self._current_mtimes()
        if mtimes == self._mtimes:
            return
        # The base file vanishing is usually a mid-save: wait for it to come back
        base_was_there = bool(self._mtimes) and self._mtimes[0] is not None
        if self.path is not None and mtimes[0] is None and base_was_there:
            return
        self._mtimes = mtimes
        try:
            self._copy = load_layers(self.paths)
        except (CopyError, OSError) as e:
            log.error(f"Ignoring invalid copy edit (keeping the previous text): {e}")
            return
        log.info(f"Reloaded copy from {' + '.join(str(p) for p in self.paths)}")


_store = CopyStore()


def configure_copy(
    path: str | Path,
    *,
    required: bool,
    override: str | Path | None = None,
    override_required: bool = False,
) -> Copy:
    """Point the bot at copy.toml (+ an optional override file) and load it."""
    global _store
    _store = CopyStore(Path(path), Path(override) if override else None)
    return _store.load(required=required, override_required=override_required)


def get_copy() -> Copy:
    return _store.get()


def use_copy(copy: Copy) -> None:
    """Replace the live copy without a file (tests)."""
    global _store
    _store = CopyStore()
    _store._copy = copy


# ---------------------------------------------------------------------------
# Generating copy/copy.toml and copy/copy.local.toml.example from the defaults
# ---------------------------------------------------------------------------

TRIPLE_QUOTE = "'" * 3


def _toml_string(value: str) -> str:
    if "\n" in value and TRIPLE_QUOTE not in value:
        return TRIPLE_QUOTE + "\n" + value + TRIPLE_QUOTE
    out = ['"']
    for ch in value:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return _toml_string(str(value))


HEADER = """\
# Cloudsail Support: every message the bot sends to players and staff.
#
# Edit the text, save, and it applies within a few seconds (no restart).
# If an edit is invalid, the bot logs an error and keeps the previous text.
# Delete a key to fall back to the built-in default.
#
# {placeholders} are filled in by the bot; each key lists the ones it accepts.
# Use {{ and }} for literal braces. Discord markdown works (**bold**, [link](url)).
# [embeds.*] sections set the title, description, color (hex), footer and
# thumbnail of each kind of embed.
#
# To change only a few strings, prefer copy/copy.local.toml (see
# copy/copy.local.toml.example): it overrides this file key by key.
#
# Regenerate the defaults with:  python -m nephthys.core.copytext > copy/copy.toml
"""

EXAMPLE_HEADER = """\
# Cloudsail Support: local copy overrides.
#
# Copy this file to copy/copy.local.toml (gitignored) and uncomment ONLY the keys
# you want to change. Every key in copy.local.toml overrides copy/copy.toml;
# anything left commented out keeps its default. It reloads live and is
# validated the same way: a bad edit is logged and ignored.
#
# Every key is listed below with its default value, grouped by section.
# {placeholders} are filled in by the bot; each key lists the ones it accepts.
# Use {{ and }} for literal braces. Colors are hex (#5865F2); "" means "none".
# Keep the [section] headers uncommented.
#
# Regenerate with:
#   python -m nephthys.core.copytext --example > copy/copy.local.toml.example
"""


def _sections(copy: Copy) -> list[tuple[str, Section]]:
    out: list[tuple[str, Section]] = []
    for name in Copy.model_fields:
        value = getattr(copy, name)
        if isinstance(value, EmbedsCopy):
            out.extend(
                (f"{name}.{sub}", getattr(value, sub))
                for sub in EmbedsCopy.model_fields
            )
        else:
            out.append((name, value))
    return out


def dump_toml(copy: Copy = DEFAULT_COPY, *, commented: bool = False) -> str:
    lines = [EXAMPLE_HEADER if commented else HEADER]
    for section_name, section in _sections(copy):
        section_cls = type(section)
        lines.append("")
        lines.append(f"[{section_name}]")
        if section_cls.__doc__:
            for doc_line in section_cls.__doc__.strip().splitlines():
                lines.append(f"# {doc_line.strip()}")
        for name, field in section_cls.model_fields.items():
            comment = []
            if field.description:
                comment.append(field.description)
            elif name.startswith("color"):
                comment.append('Hex colour, e.g. #5865F2 ("" = none).')
            elif name == "enabled":
                comment.append("true = send as an embed; false = plain text.")
            elif name == "thumbnail_url":
                comment.append("Optional image URL (https://...).")
            allowed = section_cls.PLACEHOLDERS.get(name)
            if allowed:
                comment.append(
                    "Placeholders: " + ", ".join(f"{{{p}}}" for p in sorted(allowed))
                )
            for c in comment:
                lines.append(f"# {c}")
            entry = f"{name} = {_toml_value(getattr(section, name))}"
            if commented:
                entry = "\n".join(
                    f"# {line}" if line else "#" for line in entry.split("\n")
                )
            lines.append(entry)
    return "\n".join(lines) + "\n"


def _main(argv: list[str]) -> int:
    if len(argv) > 1 and argv[1] == "--check":
        paths = [Path(a) for a in argv[2:]] or [Path("copy/copy.toml")]
        try:
            for path in paths:
                load_copy_file(path)
            load_layers(paths)
        except (CopyError, OSError) as e:
            print(e, file=sys.stderr)
            return 1
        print("copy OK")
        return 0
    if len(argv) > 1 and argv[1] == "--example":
        sys.stdout.write(dump_toml(commented=True))
        return 0
    sys.stdout.write(dump_toml())
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))


__all__ = [
    "DEFAULT_COPY",
    "Copy",
    "CopyError",
    "configure_copy",
    "get_copy",
    "parse_copy",
    "use_copy",
]
