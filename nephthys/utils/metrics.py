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
