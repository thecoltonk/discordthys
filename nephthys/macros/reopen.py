from nephthys.discord_ops import flows
from nephthys.errors.errors import PermissionDenied
from nephthys.errors.errors import TicketNotClosedError
from nephthys.errors.errors import TicketNotFoundError
from nephthys.macros.types import Macro
from nephthys.macros.types import MacroContext


class Reopen(Macro):
    name = "reopen"
    aliases = ("unresolve", "open")
    description = "re-open a resolved ticket"
    can_run_on_closed = True

    async def run(self, ctx: MacroContext) -> None:
        try:
            await flows.reopen_ticket(
                ctx.bot, ctx.ticket.id, ctx.actor, ctx.message.author, source="macro"
            )
        except (PermissionDenied, TicketNotClosedError, TicketNotFoundError) as e:
            await ctx.error(flows.refusal_message(e) or str(e))
