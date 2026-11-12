from pathlib import Path
from types import SimpleNamespace
from typing import Any
from typing import cast

import pytest

from nephthys.cogs.community_help import CommunityHelp
from nephthys.core import ai_store
from nephthys.core import tickets
from nephthys.core.copytext import DEFAULT_COPY as C
from nephthys.core.queries import get_unanswered_tickets
from nephthys.database.enums import TicketStatus
from nephthys.database.enums import UserType
from nephthys.database.tables import AISuggestion
from nephthys.ui.buttons import AIFeedbackButton
from nephthys.ui.buttons import AIFixedButton
from nephthys.ui.buttons import AINeedHumanButton
from tests.ai.fake_client import answer_json
from tests.ai.fake_client import FakeChatClient
from tests.fakes import FakeBot
from tests.fakes import make_interaction
from tests.fakes import make_message
from tests.fakes import make_user
from tests.helpers import make_settings

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
def kb_dir(tmp_path: Path) -> Path:
    (tmp_path / "faq.md").write_text("To claim land, stand in it and type /claim.")
    return tmp_path


def ai_bot(kb_dir: Path, client: FakeChatClient | None = None, **env: str) -> FakeBot:
    settings = make_settings(
        AI_API_KEY="k", AI_MODEL="fake-model", KB_DIR=str(kb_dir), **env
    )
    bot = FakeBot(settings)
    bot.ai_client = client or FakeChatClient()
    return bot


async def ask(bot: FakeBot, author=None):
    cog = CommunityHelp(cast(Any, bot))
    author = author or make_user(name="op")
    msg = make_message(bot, author, "how do i claim land?")
    await cog.on_message(msg)
    await bot.drain()
    ticket = await tickets.get_ticket_by_message(msg.id)
    assert ticket
    return cog, ticket, author


def fields(embed: Any) -> dict[str, str]:
    return {f.name: f.value for f in embed.fields}


def staff_sends(bot: FakeBot) -> list[Any]:
    return bot.sends(bot.settings.staff_channel_id or 0)


async def test_ai_off_does_nothing(kb_dir: Path):
    bot = FakeBot(make_settings(KB_DIR=str(kb_dir)))
    _, ticket, _ = await ask(bot)
    assert bot.tasks == []
    assert await AISuggestion.count() == 0
    assert ticket.category is None


async def test_shadow_mode_categorises_and_posts_to_staff_only(kb_dir: Path):
    bot = ai_bot(kb_dir)
    _, ticket, _ = await ask(bot)
    ticket = await tickets.require_ticket(ticket.id)

    assert (ticket.category, ticket.category_source) == ("Claims", "ai")
    card, suggestion_msg = staff_sends(bot)
    # The card was edited to show the AI category and the suggestion
    f = fields(card.embed)
    assert f["Category"] == "Claims (AI)"
    assert f["🤖 AI"] == "shadow answer (90%) — awaiting feedback"

    # Suggestion is a reply to the card, with 👍/👎, and never in the thread
    assert suggestion_msg.reference.message_id == ticket.staff_card_message_id
    assert suggestion_msg.embed.description == "use /claim"
    assert [c.custom_id for c in suggestion_msg.view.children] == [
        "cs:aifb:1:up",
        "cs:aifb:1:down",
    ]
    assert len(bot.sends(ticket.thread_id)) == 1  # only the auto-reply

    s = await ai_store.get_suggestion(1)
    assert s and s.posted and s.mode == "shadow" and s.message_id == suggestion_msg.id

    # AI never claims or clears waiting
    assert ticket.assigned_to is None and ticket.last_msg_by == UserType.AUTHOR
    assert [t.id for t in await get_unanswered_tickets()] == [ticket.id]


async def test_shadow_feedback(kb_dir: Path):
    bot = ai_bot(kb_dir)
    _, ticket, _ = await ask(bot)
    msg = staff_sends(bot)[-1]
    message = SimpleNamespace(embeds=[msg.embed])

    player = make_interaction(bot, make_user(), message=message)
    await AIFeedbackButton(1, "up").callback(player)
    assert player.response.send_message.call_args.args[0] == C.errors.staff_only

    mod = make_user(staff=True)
    i = make_interaction(bot, mod, message=message)
    await AIFeedbackButton(1, "up").callback(i)
    kwargs = i.response.edit_message.call_args.kwargs
    assert kwargs["view"] is None
    assert fields(kwargs["embed"])["Outcome"] == f"👍 helpful — rated by {mod.mention}"
    assert (await ai_store.get_suggestion(1)).outcome == ai_store.HELPFUL  # type: ignore[union-attr]

    again = make_interaction(bot, make_user(staff=True), message=message)
    await AIFeedbackButton(1, "down").callback(again)
    assert "already rated" in again.response.send_message.call_args.args[0]

    card = bot.message(
        bot.settings.staff_channel_id or 0,
        (await tickets.require_ticket(ticket.id)).staff_card_message_id or 0,
    )
    assert fields(card.embed)["🤖 AI"].endswith("👍 rated helpful")


@pytest.mark.parametrize(
    ("answer", "reason"),
    [
        (answer_json(None), "no_answer"),
        (answer_json(confidence=0.5), "low_confidence"),
        (answer_json(sources=[]), "no_sources"),
        (answer_json(sources=["secret.md"]), "unknown_sources"),
        ("lol", "invalid_json"),
    ],
)
async def test_gated_answers_are_logged_not_posted(
    kb_dir: Path, answer: str, reason: str
):
    bot = ai_bot(kb_dir, FakeChatClient(answer=answer))
    _, _ticket, _ = await ask(bot)
    assert len(staff_sends(bot)) == 1  # just the card
    [s] = await AISuggestion.objects()
    assert not s.posted and s.skip_reason == reason
    assert "🤖 AI" not in fields(staff_sends(bot)[0].embed)


async def test_ai_error_still_creates_ticket(kb_dir: Path):
    bot = ai_bot(kb_dir, FakeChatClient(fail=True))
    _, ticket, _ = await ask(bot)
    assert ticket.category is None
    [s] = await AISuggestion.objects()
    assert s.skip_reason == "error"


async def test_categorise_only(kb_dir: Path):
    bot = ai_bot(kb_dir, AI_ANSWER_MODE="off")
    _, ticket, _ = await ask(bot)
    assert (await tickets.require_ticket(ticket.id)).category == "Claims"
    assert await AISuggestion.count() == 0


async def test_public_mode_fixed_resolves(kb_dir: Path):
    bot = ai_bot(kb_dir, AI_ANSWER_MODE="public")
    _, ticket, op = await ask(bot)
    answer_msg = bot.sends(ticket.thread_id)[-1]
    assert answer_msg.embed.title == C.embeds.ai_public.title
    assert "faq.md" in answer_msg.embed.footer.text
    assert [c.custom_id for c in answer_msg.view.children] == [
        "cs:aifix:1",
        "cs:aihuman:1",
    ]
    assert len(staff_sends(bot)) == 1  # public mode doesn't post a shadow copy

    message = SimpleNamespace(embeds=[answer_msg.embed])
    rando = make_interaction(bot, make_user(), message=message)
    await AIFixedButton(1).callback(rando)
    assert rando.response.send_message.call_args.args[0] == C.errors.not_allowed

    i = make_interaction(bot, op, message=message)
    await AIFixedButton(1).callback(i)
    assert i.response.edit_message.call_args.kwargs["view"] is None
    t = await tickets.require_ticket(ticket.id)
    assert t.status == TicketStatus.CLOSED and t.assigned_to is None  # no credit
    assert (await ai_store.get_suggestion(1)).outcome == ai_store.FIXED  # type: ignore[union-attr]
    assert bot.world.threads[ticket.thread_id].archived

    again = make_interaction(bot, op, message=message)
    await AINeedHumanButton(1).callback(again)
    assert again.response.send_message.call_args.args[0] == C.errors.ai_already_answered


async def test_public_mode_needs_human_keeps_waiting(kb_dir: Path):
    bot = ai_bot(kb_dir, AI_ANSWER_MODE="public")
    _, ticket, op = await ask(bot)
    message = SimpleNamespace(embeds=[bot.sends(ticket.thread_id)[-1].embed])
    i = make_interaction(bot, op, message=message)
    await AINeedHumanButton(1).callback(i)

    assert bot.sends(ticket.thread_id)[-1].content == C.ai.need_human_reply
    t = await tickets.require_ticket(ticket.id)
    assert t.status == TicketStatus.OPEN and t.assigned_to is None
    assert [w.id for w in await get_unanswered_tickets()] == [ticket.id]
    assert (await ai_store.get_suggestion(1)).outcome == ai_store.NEEDS_HUMAN  # type: ignore[union-attr]


async def test_public_mode_skips_if_resolved_meanwhile(kb_dir: Path):
    bot = ai_bot(kb_dir, AI_ANSWER_MODE="public")
    cog = CommunityHelp(cast(Any, bot))
    op = make_user(name="op")
    msg = make_message(bot, op)
    await cog.on_message(msg)
    ticket = await tickets.get_ticket_by_message(msg.id)
    assert ticket
    await tickets.resolve(ticket.id, None)  # resolved before the AI task runs
    await bot.drain()
    [s] = await AISuggestion.objects()
    assert not s.posted and s.skip_reason == "ticket_closed"
