from prometheus_client import Counter

TICKETS_OPENED = Counter(
    "cloudsail_tickets_opened_total", "Tickets created", ["surface"]
)
TICKETS_RESOLVED = Counter(
    "cloudsail_tickets_resolved_total",
    "Tickets resolved",
    ["source"],  # button | macro | stale
)
TICKETS_REOPENED = Counter(
    "cloudsail_tickets_reopened_total",
    "Tickets re-opened",
    ["source"],  # button | macro | auto
)
TICKETS_CLAIMED = Counter("cloudsail_tickets_claimed_total", "Tickets claimed")
MACROS_RUN = Counter("cloudsail_macros_run_total", "Macros run", ["name"])
DISCORD_ERRORS = Counter(
    "cloudsail_discord_errors_total", "Failed Discord API side effects", ["action"]
)
AI_CATEGORIES = Counter(
    "cloudsail_ai_categorisations_total",
    "Ticket categorisations by Jev",
    ["result"],  # set | none | error
)
FAQ_DECISIONS = Counter(
    "cloudsail_faq_decisions_total",
    "What the FAQ matcher did with a message",
    # surface: community | watch
    # result: answered | answer | pointer | shadow_answer | shadow_pointer |
    #         no_match | not_a_question | ticket_closed | send_failed | error
    ["surface", "result"],
)
ZAMMAD_WEBHOOKS = Counter(
    "cloudsail_zammad_webhooks_total",
    "Zammad webhook calls received",
    ["result"],  # ok | bad_signature | bad_payload | error
)
