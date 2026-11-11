"""All user-facing copy in one place.

Strings use `str.format` placeholders. Copy for #community-help follows
PRODUCT.md (lowercase and friendly, like Nephthys).
"""

from pydantic import BaseModel
from pydantic import ConfigDict


class Transcript(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    # --- #community-help -------------------------------------------------
    auto_reply: str = (
        "someone should be along to help you soon! in the meantime check out the "
        "{faq}. once your question is answered, hit the button below to mark it "
        "as resolved :D"
    )
    resolve_button: str = "Mark as resolved"
    reopen_button: str = "Re-open"
    resolved: str = (
        "marked as resolved by {user} — got another question? make a new post!"
    )
    resolved_stale: str = (
        "closing this one since it's been quiet for {days} days — got another "
        "question? make a new post!"
    )
    reopened: str = "re-opened by {user}! someone will be along to help soon."
    thread_name_fallback: str = "Question from {name}"

    # --- Errors (ephemeral / self-deleting) ------------------------------
    not_allowed: str = "only the person who asked or a staff member can do that, sorry!"
    already_resolved: str = "this ticket is already resolved!"
    not_resolved: str = "this ticket isn't resolved, so there's nothing to re-open."
    ticket_missing: str = "i couldn't find a ticket for this thread."

    # --- Macros ----------------------------------------------------------
    faq_macro: str = "check out the FAQ, it might have your answer: {faq_url}"
    faq_macro_unconfigured: str = "the FAQ link hasn't been set up yet (FAQ_URL)."
    ip_macro: str = "the server address is **{server_address}** — see you in game!"
    ip_macro_unconfigured: str = (
        "the server address hasn't been set up yet (SERVER_ADDRESS)."
    )
    unknown_macro: str = "`?{name}` isn't a macro. available: {available}"
    macro_wrong_state: str = "`?{name}` can't be used on a resolved ticket."

    # --- Staff -----------------------------------------------------------
    staff_feed: str = "🆕 **#{id}** {title} — {user} · {link}"

    def faq_text(self, faq_url: str) -> str:
        return f"[FAQ]({faq_url})" if faq_url else "FAQ"


TRANSCRIPT = Transcript()
