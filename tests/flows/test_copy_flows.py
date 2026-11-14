"""Copy flows through every message; FAQ handling; link embeds always suppressed."""

from datetime import datetime
from datetime import timedelta
from datetime import UTC
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from typing import cast

import pytest

from nephthys.cogs.community_help import CommunityHelp
from nephthys.cogs.staff import Staff
from nephthys.cogs.ticket_commands import TicketCommands
from nephthys.core import tickets
from nephthys.core.copytext import Copy
from nephthys.core.copytext import DEFAULT_COPY
from nephthys.core.copytext import EmbedsCopy
from nephthys.core.copytext import parse_copy
from nephthys.core.copytext import use_copy
from nephthys.database.tables import Ticket
from nephthys.discord_ops import staff_card
from nephthys.discord_ops.sticky import build_sticky_embed
from nephthys.discord_ops.threads import auto_reply_text
from nephthys.tasks.close_stale import close_stale_tickets
from nephthys.tasks.daily_summary import build_daily_summary
from nephthys.ui.buttons import ReopenButton
from nephthys.ui.buttons import ResolveButton
from nephthys.ui.buttons import RetiredAIButton
from tests.ai.fake_jev import FakeJev
from tests.fakes import FakeBot
from tests.fakes import make_interaction
from tests.fakes import make_message
from tests.fakes import make_text_channel
from tests.fakes import make_user
from tests.helpers import make_settings

pytestmark = pytest.mark.usefixtures("db")


def _every_placeholder(section: Any, embeds_enabled: bool) -> Any:
    values: dict[str, Any] = {}
    for name in type(section).model_fields:
        value = getattr(section, name)
        if name == "enabled":
            values[name] = embeds_enabled
        elif (
            not isinstance(value, str)
            or name.startswith("color")
            or name == "thumbnail_url"
            or name.endswith("_emoji")
        ):
            values[name] = value  # not a text template
        else:
            allowed = sorted(type(section).PLACEHOLDERS.get(name, ()))
            values[name] = f"{name}:" + "|".join("{" + p + "}" for p in allowed)
    return type(section)(**values)


def every_placeholder_copy(embeds_enabled: bool = False) -> Copy:
    """Each template = its field name followed by *every* placeholder it allows.
    `embeds_enabled` also switches the opt-in embeds (ticket opener/closing) on."""
    sections: dict[str, Any] = {}
    for section_name in Copy.model_fields:
        section = getattr(DEFAULT_COPY, section_name)
        if isinstance(section, EmbedsCopy):
            sections[section_name] = EmbedsCopy(
                **{
                    sub: _every_placeholder(getattr(section, sub), embeds_enabled)
                    for sub in EmbedsCopy.model_fields
                }
            )
        else:
            sections[section_name] = _every_placeholder(section, embeds_enabled)
    return Copy(**sections)


async def run_everything(bot: FakeBot, kb_dir: Path) -> list[Any]:
    """Drive every message-producing path once. Returns all interactions."""
    interactions: list[Any] = []

    def interaction(user: Any, **kwargs: Any) -> Any:
        i = make_interaction(bot, user, **kwargs)
        interactions.append(i)
        return i

    help_cog = CommunityHelp(cast(Any, bot))
    staff_cog = Staff(cast(Any, bot))
    commands = TicketCommands(cast(Any, bot))
    op, mod, rando = (
        make_user(name="op"),
        make_user(staff=True, name="mod"),
        make_user(),
    )

    # New question (with and without text), staff reply (claim)
    q = make_message(bot, op, "how do i claim land?")
    await help_cog.on_message(q)
    await help_cog.on_message(make_message(bot, op, ""))
    await bot.drain()
    ticket = await tickets.get_ticket_by_message(q.id)
    assert ticket
    thread = bot.world.threads[ticket.thread_id]
    await help_cog.on_message(make_message(bot, mod, "use /claim", channel=thread))

    # Staff commands, including refusals and the unconfigured paths
    for command in (TicketCommands.faq, TicketCommands.ip):
        await cast(Any, command).callback(commands, interaction(mod, channel=thread))
    await cast(Any, TicketCommands.faq).callback(
        commands, interaction(rando, channel=thread)
    )
    await cast(Any, TicketCommands.faq).callback(
        commands, interaction(mod, channel=SimpleNamespace(id=1))
    )

    # Resolve / refusals / reopen / auto-reopen
    await ResolveButton(ticket.id).callback(interaction(rando))
    await ResolveButton(ticket.id).callback(interaction(op))
    await ResolveButton(ticket.id).callback(interaction(op))
    await cast(Any, TicketCommands.resolve).callback(
        commands, interaction(mod, channel=thread)
    )
    await ReopenButton(ticket.id).callback(interaction(mod))
    await cast(Any, TicketCommands.reopen).callback(
        commands, interaction(mod, channel=thread)
    )
    await cast(Any, TicketCommands.resolve).callback(
        commands, interaction(mod, channel=thread)
    )
    await help_cog.on_message(make_message(bot, op, "it broke again", channel=thread))

    # FAQ matcher. Shadow: the new ticket above was reported to the staff
    # channel. A button left over from the old assistant gets a private reply.
    assert any(
        (m.embed.title or "").startswith(("title:", "FAQ match"))
        for m in bot.sends(bot.settings.staff_channel_id or 0)
        if getattr(m, "embed", None) is not None
    ), "the shadow report should have been posted"
    await RetiredAIButton("cs:aifb:1:up").callback(interaction(mod))
    # Cards posted for community tickets before they were #tickets-only still
    # render (with the Jev-picked category)
    await staff_card.render(cast(Any, bot), await tickets.require_ticket(ticket.id))

    # Watched channel, shadow then public: an answer, then a pointer
    from nephthys.discord_ops import faq_flow

    general = bot.get_partial_messageable(700)
    for mode in ("shadow", "public"):
        object.__setattr__(
            bot,
            "settings",
            make_settings(
                AI_API_KEY="k",
                KB_DIR=str(kb_dir),
                FAQ_MODE=mode,
                FAQ_WATCH_CHANNEL_IDS="700",
                FAQ_WATCH_POINTER="true",
                STALE_TICKET_DAYS="3",
                TICKETS_CHANNEL_ID="600",
            ),
        )
        for faq_choice in ("claim", "other"):
            bot.jev.set(faq=(faq_choice, 0.99), kind=("support_question", 0.99))
            outcome = await faq_flow.handle_watched_message(
                cast(Any, bot),
                make_message(bot, make_user(), "how do i claim land?", channel=general),
                faq_flow.Cooldowns(0, 0),
            )
            assert outcome.endswith(("answer", "pointer")), outcome
    # Public answer in a new ticket's thread
    bot.jev.set(faq=("claim", 0.99), category=("Claims", 0.9))
    await help_cog.on_message(make_message(bot, op, "how do claims work?"))
    await bot.drain()

    # Staff overviews
    await cast(Any, Staff.waiting).callback(staff_cog, interaction(mod))
    await cast(Any, Staff.stats).callback(staff_cog, interaction(mod), 7)
    await cast(Any, Staff.stats).callback(staff_cog, interaction(mod), 1)
    await cast(Any, Staff.stats).callback(staff_cog, interaction(rando), 7)

    # Stale close
    await Ticket.update(
        {Ticket.last_msg_at: datetime.now(UTC) - timedelta(days=9)}, force=True
    )
    await close_stale_tickets(cast(Any, bot), delay=0)
    return interactions


@pytest.fixture
def kb_dir(tmp_path: Path) -> Path:
    (tmp_path / "claim.md").write_text(
        "---\nid: claim\ndescription: How to claim land.\n---\n"
        "Use /claim to claim land: https://example.com/claims"
    )
    return tmp_path


def ai_bot(kb_dir: Path, **env: str) -> FakeBot:
    bot = FakeBot(
        make_settings(
            AI_API_KEY="k",
            KB_DIR=str(kb_dir),
            TICKETS_CHANNEL_ID="600",
            **env,
        )
    )
    bot.jev = FakeJev(faq=("claim", 0.99), category=("Claims", 0.9))
    make_text_channel(bot, 600)
    return bot


async def run_tickets_everything(bot: FakeBot, interactions: list[Any]) -> None:
    """Every #tickets message path: panel, create, picks, alert, first message,
    claim, close, an old CSAT button, a refused /reopen, limit, escalate
    (+quote, already escalated)."""
    from nephthys.cogs.tickets import Tickets
    from nephthys.core import private_tickets as pt
    from nephthys.ui.ticket_buttons import CategoryButton
    from nephthys.ui.ticket_buttons import CloseTicketButton
    from nephthys.ui.ticket_buttons import CreateTicketButton
    from nephthys.ui.ticket_buttons import RetiredCSATButton
    from nephthys.ui.ticket_buttons import SubcategoryButton

    def interaction(user: Any, **kwargs: Any) -> Any:
        i = make_interaction(bot, user, **kwargs)
        interactions.append(i)
        return i

    cog = Tickets(cast(Any, bot))
    help_cog = CommunityHelp(cast(Any, bot))
    commands = TicketCommands(cast(Any, bot))
    assert cog.sticky is not None
    await cog.sticky.ensure()
    player, mod, rando = make_user(name="P"), make_user(staff=True), make_user()

    await CreateTicketButton().callback(interaction(player))
    channel = bot.world.text_channels[600]
    thread, _ = channel.created_threads[-1]
    ticket = await tickets.get_ticket_by_thread(thread.id)
    assert ticket
    await CategoryButton(ticket.id, "server_issue").callback(interaction(rando))
    await CategoryButton(ticket.id, "server_issue").callback(interaction(player))
    await CategoryButton(ticket.id, "store").callback(interaction(player))
    await CategoryButton(ticket.id, "nope").callback(interaction(player))
    await SubcategoryButton(ticket.id, "server_issue", "urgent").callback(
        interaction(player)
    )
    await cog.on_message(make_message(bot, player, "it broke", channel=thread))
    await cog.on_message(make_message(bot, mod, "looking", channel=thread))
    await CloseTicketButton(ticket.id).callback(interaction(rando))
    await CloseTicketButton(ticket.id).callback(interaction(player))
    await CloseTicketButton(ticket.id).callback(interaction(player))
    await RetiredCSATButton(ticket.id, 5).callback(interaction(player))
    await cast(Any, TicketCommands.reopen).callback(
        commands, interaction(mod, channel=thread)
    )
    await cast(Any, TicketCommands.faq).callback(
        commands, interaction(mod, channel=thread)
    )
    await cast(Any, TicketCommands.resolve).callback(
        commands, interaction(mod, channel=thread)
    )

    # Open-ticket limit
    for _ in range(3):
        await CreateTicketButton().callback(interaction(rando))

    # Escalation, with and without copying the original
    for delete_original in (False, True):
        asker = make_user(name="Q")
        q = make_message(bot, asker, "secret stuff")
        await help_cog.on_message(q)
        community = await tickets.get_ticket_by_message(q.id)
        assert community
        public = bot.world.threads[community.thread_id]
        await cast(Any, Tickets.escalate).callback(
            cog, interaction(mod, channel=public), delete_original
        )
        await cast(Any, Tickets.escalate).callback(
            cog, interaction(mod, channel=public), False
        )
        private = await pt.escalation_of(community.id)
        assert private
        # A legacy community card shows "Escalated to"
        await staff_card.render(
            cast(Any, bot), await tickets.require_ticket(community.id)
        )
        await CategoryButton(private.id, "other").callback(interaction(asker))


@pytest.mark.parametrize("embeds_enabled", [False, True])
async def test_code_supplies_every_declared_placeholder(
    kb_dir: Path, embeds_enabled: bool
):
    """If code forgot to pass a placeholder that copy may use, .format() would
    raise KeyError somewhere in here. Runs with the opt-in ticket opener/closing
    embeds off (text) and on."""
    use_copy(every_placeholder_copy(embeds_enabled))
    bot = ai_bot(kb_dir)
    await run_everything(bot, kb_dir)
    await run_tickets_everything(bot, [])

    texts = [
        m.content
        for msgs in bot.world.channel_sends.values()
        for m in msgs
        if m.content
    ]
    assert any(t.startswith("auto_reply:") for t in texts)
    assert any(t.startswith("resolved:") for t in texts)
    assert any(t.startswith("reopened:") for t in texts)
    assert any(t.startswith("resolved_stale:") for t in texts)
    # Watched-channel replies use their templates; the answer inside is the
    # entry's text, untouched
    answer = "Use /claim to claim land: https://example.com/claims"
    assert f"watch_reply:{answer}|<#200>" in texts
    assert "watch_pointer:<#200>" in texts
    assert answer in texts  # in a ticket thread: the answer alone
    if embeds_enabled:
        embed_texts = [
            f"{m.embed.title or ''} {m.embed.description or ''} {m.embed.footer.text or ''}"
            for msgs in bot.world.channel_sends.values()
            for m in msgs
            if getattr(m, "embed", None) is not None
        ]
        for key in ("title:", "description:", "footer:"):
            assert any(key in t for t in embed_texts), key
        # The opener/closing text is inside the embeds now
        assert any("welcome:" in t for t in embed_texts)
        assert any("closed:" in t for t in embed_texts)
    for key in (
        *(() if embeds_enabled else ("opener_header:", "closed:")),
        "escalated_public:",
        "escalated_quote:",
    ):
        assert any(key in t for t in texts), key
    alerts = [
        m
        for m in bot.sends(bot.settings.staff_channel_id or 0)
        if (m.embed.title or "").startswith("private_title:")
    ]
    # Staff alerts are the card alone: no ping line above it
    assert alerts and all(m.content is None for m in alerts)

    now = datetime.now(UTC)
    embed, *_ = await build_daily_summary(now=now + timedelta(minutes=1), guild_id=1)
    assert (embed.title or "").startswith("title:")
    sticky = build_sticky_embed("https://faq")
    assert sticky.description and sticky.description.startswith("description:")


async def test_link_embeds_are_always_suppressed(kb_dir: Path):
    bot = ai_bot(kb_dir)
    interactions = await run_everything(bot, kb_dir)
    await run_tickets_everything(bot, interactions)

    sent = [m for msgs in bot.world.channel_sends.values() for m in msgs]
    assert sent
    for m in sent:
        if getattr(m, "embed", None) is not None:
            # Rich embeds carry no linkable content (at most a player mention), so
            # nothing can unfurl; suppress_embeds would hide the embed itself
            assert m.content is None or "http" not in m.content
            assert not getattr(m, "suppress_embeds", False)
        elif m.content is not None:
            assert getattr(m, "suppress_embeds", False) is True, m.content

    calls = 0
    for i in interactions:
        for mock in (i.response.send_message, i.followup.send):
            for call in mock.call_args_list:
                content = call.args[0] if call.args else call.kwargs.get("content")
                if content is not None:
                    calls += 1
                    assert call.kwargs.get("suppress_embeds") is True, content
    assert calls > 10


async def test_custom_copy_is_used(kb_dir: Path):
    use_copy(
        parse_copy(
            '[community]\nresolved = "sorted by {user} ✨"\nresolve_button = "Done!"\n'
            '[card]\nstatus_claimed = "👀 {claimer} is on it{reopened}"\n'
        )
    )
    bot = FakeBot()
    cog = CommunityHelp(cast(Any, bot))
    op, mod = make_user(name="op"), make_user(staff=True)
    q = make_message(bot, op)
    await cog.on_message(q)
    ticket = await tickets.get_ticket_by_message(q.id)
    assert ticket
    auto_reply = bot.sends(ticket.thread_id)[0]
    assert auto_reply.view.children[0].item.label == "Done!"

    await cog.on_message(
        make_message(bot, mod, "hi", channel=bot.world.threads[ticket.thread_id])
    )
    ticket = await tickets.require_ticket(ticket.id)
    embed, _ = await staff_card.render(cast(Any, bot), ticket)
    assert {f.name: f.value for f in embed.fields}[
        "Status"
    ] == f"👀 <@{mod.id}> is on it"

    await ResolveButton(ticket.id).callback(make_interaction(bot, op))
    assert bot.sends(ticket.thread_id)[-1].content == f"sorted by {op.mention} ✨"


def test_faq_text_when_set_and_unset():
    with_faq = auto_reply_text("https://cloudsail.uk/faq")
    assert with_faq == DEFAULT_COPY.community.auto_reply.format(
        faq_url="https://cloudsail.uk/faq"
    )
    assert "[FAQ]({faq_url})" in DEFAULT_COPY.community.auto_reply
    without = auto_reply_text("")
    assert without == DEFAULT_COPY.community.auto_reply_no_faq
    assert "FAQ" not in without and "faq" not in without

    assert build_sticky_embed("https://cloudsail.uk/faq").description == (
        DEFAULT_COPY.embeds.sticky.description.format(
            faq_url="https://cloudsail.uk/faq"
        )
    )
    sticky = build_sticky_embed("")
    assert sticky.description == DEFAULT_COPY.embeds.sticky.description_no_faq
    assert "FAQ" not in (sticky.description or "")


async def test_no_faq_configured_everywhere():
    bot = FakeBot(
        make_settings(FAQ_URL="", STICKY_ENABLED="true", STICKY_DEBOUNCE_SECONDS="0")
    )
    cog = CommunityHelp(cast(Any, bot))
    commands = TicketCommands(cast(Any, bot))
    q = make_message(bot, make_user(name="op"))
    await cog.on_message(q)
    assert cog.sticky is not None
    await cog.sticky.wait_idle()
    ticket = await tickets.get_ticket_by_message(q.id)
    assert ticket

    auto_reply = bot.sends(ticket.thread_id)[0].content
    assert "FAQ" not in auto_reply
    [sticky] = [
        m
        for m in bot.sends(bot.settings.community_channel_id)
        if getattr(m, "embed", None)
    ]
    assert "FAQ" not in sticky.embed.description

    i = make_interaction(
        bot, make_user(staff=True), channel=bot.world.threads[ticket.thread_id]
    )
    await cast(Any, TicketCommands.faq).callback(commands, i)
    assert i.response.send_message.call_args.args[0] == "no FAQ configured."
    assert i.response.send_message.call_args.kwargs["ephemeral"] is True
    assert len(bot.sends(ticket.thread_id)) == 1  # nothing posted publicly
