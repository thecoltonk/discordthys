from nephthys.core.transcript import TRANSCRIPT
from nephthys.macros.types import Macro
from nephthys.macros.types import MacroContext


class FAQ(Macro):
    """Post a link to the FAQ. Doesn't resolve the ticket."""

    name = "faq"
    description = "link the FAQ"
    counts_as_reply = True

    async def run(self, ctx: MacroContext) -> None:
        url = ctx.bot.settings.faq_url
        if not url:
            await ctx.error(TRANSCRIPT.faq_macro_unconfigured)
            return
        await ctx.say(TRANSCRIPT.faq_macro.format(faq_url=url))
