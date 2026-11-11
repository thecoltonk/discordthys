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

    # --- Staff slash commands (/faq, /ip, /resolve, /reopen) ----------
    faq_reply: str = "check out the FAQ, it might have your answer: {faq_url}"
    faq_unconfigured: str = "the FAQ link hasn't been set up yet (FAQ_URL)."
    ip_reply: str = "the server address is **{server_address}** — see you in game!"
    ip_unconfigured: str = "the server address hasn't been set up yet (SERVER_ADDRESS)."
    staff_only: str = "this command is for staff only."
    not_a_ticket_thread: str = "use this inside a #community-help ticket thread."
    command_resolved: str = "resolved ✅"
    command_reopened: str = "re-opened 🔄"

    # --- AI answers ------------------------------------------------------
    ai_public_title: str = "🤖 Automated answer"
    ai_public_footer: str = (
        "I'm a bot answering from the Cloudsail knowledge base, and I can be wrong. "
        "Sources: {sources}"
    )
    ai_fixed_button: str = "This fixed it"
    ai_need_human_button: str = "I still need a human"
    ai_need_human_reply: str = (
        "no worries — a human will be along to help soon! feel free to add any "
        "extra details while you wait."
    )
    ai_already_answered: str = "this has already been answered, thanks!"

    # --- Staff -----------------------------------------------------------
    category_set: str = "category set to **{category}**"

    def faq_text(self, faq_url: str) -> str:
        return f"[FAQ]({faq_url})" if faq_url else "FAQ"


TRANSCRIPT = Transcript()
