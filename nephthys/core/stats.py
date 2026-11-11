from collections import Counter
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from datetime import timedelta
from datetime import UTC
from statistics import fmean
from statistics import median

from nephthys.core.queries import get_unanswered_tickets
from nephthys.core.tickets import fk_id_or_none
from nephthys.database.enums import TicketStatus
from nephthys.database.enums import TicketSurface
from nephthys.database.tables import Ticket
from nephthys.database.tables import User

WAITING_THRESHOLD = timedelta(days=3)
UNCATEGORISED = "Uncategorised"
UNSET_PRIORITY = "unset"
PRIORITY_ORDER = ("high", "medium", "low", UNSET_PRIORITY)


@dataclass(frozen=True)
class LeaderboardEntry:
    user: User
    count: int


@dataclass
class StatsSummary:
    since: datetime
    until: datetime
    opened: int = 0
    resolved: int = 0
    # Resolved in the window with no staff claim (no credit given)
    resolved_without_staff: int = 0
    open_now: int = 0
    in_progress_now: int = 0
    # Over tickets opened in the window that have been claimed
    median_first_response_minutes: float | None = None
    waiting_over_threshold: int = 0
    top_claimers: list[LeaderboardEntry] = field(default_factory=list)
    # Tickets opened in the window, by category ("Uncategorised" for none)
    by_category: dict[str, int] = field(default_factory=dict)
    # The waiting-over-threshold tickets themselves (oldest first)
    waiting_tickets: list[Ticket] = field(default_factory=list)
    # #tickets: opened in the window by priority (low/medium/high/unset)
    by_priority: dict[str, int] = field(default_factory=dict)
    # #tickets: CSAT ratings given in the window
    csat_average: float | None = None
    csat_count: int = 0


def minutes_between(start: datetime, end: datetime) -> float:
    return (end - start).total_seconds() / 60


def first_response_minutes(tickets: list[Ticket]) -> list[float]:
    return [
        minutes_between(t.created_at, t.assigned_at)
        for t in tickets
        if t.assigned_at is not None
    ]


async def calculate_stats(
    since: datetime,
    until: datetime | None = None,
    surface: TicketSurface = TicketSurface.COMMUNITY,
    leaderboard_size: int = 5,
) -> StatsSummary:
    until = until or datetime.now(UTC)
    summary = StatsSummary(since=since, until=until)

    opened = await Ticket.objects().where(
        (Ticket.surface == surface)
        & (Ticket.created_at >= since)
        & (Ticket.created_at < until)
    )
    summary.opened = len(opened)
    category_counts = Counter(t.category or UNCATEGORISED for t in opened)
    summary.by_category = dict(
        sorted(category_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    )
    priority_counts = Counter(t.priority or UNSET_PRIORITY for t in opened)
    summary.by_priority = {
        p: priority_counts[p] for p in PRIORITY_ORDER if priority_counts.get(p)
    }
    response_times = first_response_minutes(opened)
    summary.median_first_response_minutes = (
        median(response_times) if response_times else None
    )

    resolved = await Ticket.objects().where(
        (Ticket.surface == surface)
        & (Ticket.status == TicketStatus.CLOSED)
        & (Ticket.closed_at >= since)
        & (Ticket.closed_at < until)
    )
    summary.resolved = len(resolved)
    credits = Counter(
        claimer
        for t in resolved
        if (claimer := fk_id_or_none(t.assigned_to)) is not None
    )
    summary.resolved_without_staff = summary.resolved - sum(credits.values())

    if credits:
        top = credits.most_common(leaderboard_size)
        users = {
            u.id: u
            for u in await User.objects().where(User.id.is_in([uid for uid, _ in top]))
        }
        summary.top_claimers = [
            LeaderboardEntry(user=users[uid], count=count)
            for uid, count in top
            if uid in users
        ]

    rated = await Ticket.objects().where(
        (Ticket.surface == surface)
        & Ticket.csat_rating.is_not_null()
        & (Ticket.csat_at >= since)
        & (Ticket.csat_at < until)
    )
    ratings = [t.csat_rating for t in rated if t.csat_rating is not None]
    summary.csat_count = len(ratings)
    summary.csat_average = fmean(ratings) if ratings else None

    summary.open_now = await Ticket.count().where(
        (Ticket.surface == surface) & (Ticket.status == TicketStatus.OPEN)
    )
    summary.in_progress_now = await Ticket.count().where(
        (Ticket.surface == surface) & (Ticket.status == TicketStatus.IN_PROGRESS)
    )
    waiting = await get_unanswered_tickets(
        last_message_before=until - WAITING_THRESHOLD
    )
    summary.waiting_tickets = [t for t in waiting if t.surface == surface]
    summary.waiting_over_threshold = len(summary.waiting_tickets)
    return summary
