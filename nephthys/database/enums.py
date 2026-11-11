from enum import StrEnum

from nephthys.database.postgres_enum import create_postgres_enum_type


class TicketStatus(StrEnum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    CLOSED = "CLOSED"


class UserType(StrEnum):
    """Who sent the most recent message in a ticket thread."""

    AUTHOR = "AUTHOR"
    HELPER = "HELPER"
    OTHER = "OTHER"


class TicketSurface(StrEnum):
    """Where a ticket lives. Only COMMUNITY exists in v1; PRIVATE is #tickets (v2)."""

    COMMUNITY = "COMMUNITY"
    PRIVATE = "PRIVATE"


class BotMessageKind(StrEnum):
    AUTO_REPLY = "AUTO_REPLY"
    RESOLVED = "RESOLVED"
    REOPENED = "REOPENED"
    MACRO = "MACRO"


TicketStatusColumn = create_postgres_enum_type("ticket_status", TicketStatus)
UserTypeColumn = create_postgres_enum_type("user_type", UserType)
TicketSurfaceColumn = create_postgres_enum_type("ticket_surface", TicketSurface)
BotMessageKindColumn = create_postgres_enum_type("bot_message_kind", BotMessageKind)
