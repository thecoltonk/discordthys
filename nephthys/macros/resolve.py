from nephthys.discord_ops import flows
from nephthys.errors.errors import PermissionDenied
from nephthys.errors.errors import TicketAlreadyClosedError
from nephthys.errors.errors import TicketNotFoundError
from nephthys.macros.types import Macro
from nephthys.macros.types import MacroContext


class Resolve(Macro):
    name = "resolve"
    aliases = ("close", "resolved")
    description = "mark as resolved"
    # Let it run so staff get the "already resolved" message
    can_run_on_closed = True

    async def run(self, ctx: MacroContext) -> None:
        try:
            await flows.resolve_ticket(
                ctx.bot, ctx.ticket.id, ctx.actor, ctx.message.author, source="macro"
            )
        except (PermissionDenied, TicketAlreadyClosedError, TicketNotFoundError) as e:
            await ctx.error(flows.refusal_message(e) or str(e))
