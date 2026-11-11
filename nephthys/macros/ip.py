from nephthys.core.transcript import TRANSCRIPT
from nephthys.macros.types import Macro
from nephthys.macros.types import MacroContext


class IP(Macro):
    """Post the Minecraft server address."""

    name = "ip"
    aliases = ("address",)
    description = "post the server address"
    counts_as_reply = True

    async def run(self, ctx: MacroContext) -> None:
        address = ctx.bot.settings.server_address
        if not address:
            await ctx.error(TRANSCRIPT.ip_macro_unconfigured)
            return
        await ctx.say(TRANSCRIPT.ip_macro.format(server_address=address))
