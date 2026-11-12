from piccolo.columns import Array
from piccolo.columns import BigInt
from piccolo.columns import Boolean
from piccolo.columns import DoublePrecision
from piccolo.columns import ForeignKey
from piccolo.columns import Integer
from piccolo.columns import OnDelete
from piccolo.columns import OnUpdate
from piccolo.columns import Serial
from piccolo.columns import SmallInt
from piccolo.columns import Text
from piccolo.columns import Timestamptz
from piccolo.columns.defaults.timestamptz import TimestamptzNow
from piccolo.table import Table

from nephthys.database.enums import BotMessageKind
from nephthys.database.enums import BotMessageKindColumn
from nephthys.database.enums import TicketStatus
from nephthys.database.enums import TicketStatusColumn
from nephthys.database.enums import TicketSurface
from nephthys.database.enums import TicketSurfaceColumn
from nephthys.database.enums import UserType
from nephthys.database.enums import UserTypeColumn

# NOTE: The schema is created by raw SQL in
# `piccolo_migrations/cloudsail_*.py`. Keep the two in sync.


class User(Table, tablename="app_user"):
    """A Discord user we've seen (question author, helper or staff)."""

    id = Serial(primary_key=True)
    discord_id = BigInt(unique=True)
    username = Text(null=True)
    # Cached from the Staff role on each upsert; the role is the source of truth
    helper = Boolean(default=False)
    admin = Boolean(default=False)
    created_at = Timestamptz(default=TimestamptzNow())


class Ticket(Table, tablename="ticket"):
    id = Serial(primary_key=True)
    surface = TicketSurfaceColumn(default=TicketSurface.COMMUNITY)

    channel_id = BigInt()
    # The question message (#community-help only; NULL for private tickets)
    message_id = BigInt(unique=True, null=True, default=None)
    thread_id = BigInt(unique=True)

    title = Text(null=True)
    description = Text(default="")
    status = TicketStatusColumn(default=TicketStatus.OPEN)

    last_msg_at = Timestamptz(default=TimestamptzNow())
    last_msg_by = UserTypeColumn(default=UserType.AUTHOR)

    opened_by = ForeignKey(
        references=User, on_delete=OnDelete.restrict, on_update=OnUpdate.cascade
    )
    # The claimer: the first staff member to reply. Also who gets credit.
    assigned_to = ForeignKey(
        references=User,
        null=True,
        on_delete=OnDelete.set_null,
        on_update=OnUpdate.cascade,
    )
    assigned_at = Timestamptz(null=True, default=None)
    # Who actually resolved it (NULL when resolved automatically)
    closed_by = ForeignKey(
        references=User,
        null=True,
        on_delete=OnDelete.set_null,
        on_update=OnUpdate.cascade,
    )
    closed_at = Timestamptz(null=True, default=None)
    reopened_by = ForeignKey(
        references=User,
        null=True,
        on_delete=OnDelete.set_null,
        on_update=OnUpdate.cascade,
    )
    reopened_at = Timestamptz(null=True, default=None)

    # One of TICKET_CATEGORIES (stored by name), and who set it: "ai" | "staff"
    category = Text(null=True, default=None)
    category_source = Text(null=True, default=None)

    # #tickets (private) only
    number = Integer(unique=True, null=True, default=None)  # shown as T-<number>
    subcategory = Text(null=True, default=None)
    priority = Text(null=True, default=None)  # low | medium | high
    opener_message_id = BigInt(null=True, default=None)
    escalated_from = Integer(null=True, default=None)  # FK ticket.id in SQL
    csat_rating = SmallInt(null=True, default=None)  # 1-5
    csat_at = Timestamptz(null=True, default=None)

    # The staff ticket card in STAFF_CHANNEL_ID
    staff_card_channel_id = BigInt(null=True, default=None)
    staff_card_message_id = BigInt(null=True, default=None)

    created_at = Timestamptz(default=TimestamptzNow())


class BotMessage(Table, tablename="bot_message"):
    """A message the bot sent in a ticket thread."""

    id = Serial(primary_key=True)
    ticket = ForeignKey(references=Ticket, on_delete=OnDelete.cascade)
    channel_id = BigInt()
    message_id = BigInt()
    kind = BotMessageKindColumn(default=BotMessageKind.AUTO_REPLY)
    created_at = Timestamptz(default=TimestamptzNow())


class StickyMessage(Table, tablename="sticky_message"):
    """The current sticky "how it works" message in a channel."""

    channel_id = BigInt(primary_key=True)
    message_id = BigInt()
    updated_at = Timestamptz(default=TimestamptzNow())


class AISuggestion(Table, tablename="ai_suggestion"):
    """Every AI answer attempt, whether or not it was posted, and its outcome."""

    id = Serial(primary_key=True)
    ticket = ForeignKey(references=Ticket, on_delete=OnDelete.cascade)
    mode = Text()  # shadow | public
    model = Text()
    answer = Text(null=True, default=None)
    confidence = DoublePrecision(null=True, default=None)
    kb_sources = Array(base_column=Text(), default=list)
    posted = Boolean(default=False)
    skip_reason = Text(null=True, default=None)
    outcome = Text(default="pending")
    channel_id = BigInt(null=True, default=None)
    message_id = BigInt(null=True, default=None)
    rated_by = ForeignKey(references=User, null=True, on_delete=OnDelete.set_null)
    rated_at = Timestamptz(null=True, default=None)
    created_at = Timestamptz(default=TimestamptzNow())
