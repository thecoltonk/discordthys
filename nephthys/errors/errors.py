class TicketNotClosedError(Exception):
    def __init__(self, ticket_id: int):
        super().__init__("Cannot reopen a ticket that isn't resolved")
        self.ticket_id = ticket_id


class TicketAlreadyClosedError(Exception):
    def __init__(self, ticket_id: int):
        super().__init__("This ticket is already resolved")
        self.ticket_id = ticket_id


class TicketNotFoundError(Exception):
    def __init__(self, ticket_id: int):
        super().__init__(f"Ticket {ticket_id} does not exist")
        self.ticket_id = ticket_id


class PermissionDenied(Exception):
    def __init__(self, message: str, user_id: int):
        super().__init__(message)
        self.user_id = user_id
