import json

from nephthys.ai import answer as A
from nephthys.ai.answer import build_answer_messages
from nephthys.ai.answer import evaluate_answer
from nephthys.ai.answer import suggest_answer
from nephthys.ai.categorize import build_category_messages
from nephthys.ai.categorize import parse_category
from nephthys.ai.categorize import suggest_category
from nephthys.ai.kb import KBDoc
from tests.ai.fake_client import answer_json
from tests.ai.fake_client import FakeChatClient

SOURCES = {"faq.md", "ip.md", "rules/chat.md"}
CATS = ("Gameplay", "Bug", "Claims", "Economy", "Store", "Other")


def ev(raw: str, min_conf: float = 0.8):
    return evaluate_answer(raw, SOURCES, min_conf)


def test_valid_answer_is_posted():
    d = ev(answer_json("type /claim", 0.92, ["faq.md", "kb/ip.md", "./faq.md"]))
    assert d.post and d.skip_reason is None
    assert d.answer == "type /claim"
    assert d.kb_sources == ["faq.md", "ip.md"]


def test_code_fence_tolerated():
    assert ev("```json\n" + answer_json() + "\n```").post


def test_gates():
    assert ev(answer_json(None)).skip_reason == A.NO_ANSWER
    assert ev(answer_json("   ")).skip_reason == A.NO_ANSWER
    assert ev(answer_json(confidence=0.79)).skip_reason == A.LOW_CONFIDENCE
    assert ev(answer_json(confidence=0.8)).post  # threshold is inclusive
    assert ev(answer_json(sources=[])).skip_reason == A.NO_SOURCES
    assert (
        ev(answer_json(sources=["faq.md", "made-up.md"])).skip_reason
        == A.UNKNOWN_SOURCES
    )
    assert ev(answer_json("x" * 1501)).skip_reason == A.TOO_LONG


def test_strict_json_shape():
    for raw in (
        "not json",
        "[1, 2]",
        json.dumps({"answer": "a", "confidence": 0.9}),  # missing key
        json.dumps(
            {"answer": "a", "confidence": 0.9, "kb_sources": ["faq.md"], "extra": 1}
        ),
        json.dumps({"answer": 5, "confidence": 0.9, "kb_sources": ["faq.md"]}),
        json.dumps({"answer": "a", "confidence": True, "kb_sources": ["faq.md"]}),
        json.dumps({"answer": "a", "confidence": "high", "kb_sources": ["faq.md"]}),
        json.dumps({"answer": "a", "confidence": 1.2, "kb_sources": ["faq.md"]}),
        json.dumps({"answer": "a", "confidence": 0.9, "kb_sources": "faq.md"}),
        json.dumps({"answer": "a", "confidence": 0.9, "kb_sources": [1]}),
    ):
        assert ev(raw).skip_reason == A.INVALID_JSON, raw


def test_skips_keep_details_for_logging():
    d = ev(answer_json("maybe", 0.3))
    assert not d.post and d.answer == "maybe" and d.confidence == 0.3


def test_prompt_contains_only_kb_and_question():
    docs = [KBDoc("faq.md", "Use /claim to claim land.")]
    messages = build_answer_messages(docs, "claims?", "how do i claim")
    assert '<doc path="faq.md">' in messages[0]["content"]
    assert "ONLY using the knowledge base" in messages[0]["content"]
    assert "how do i claim" in messages[1]["content"]
    assert "how do i claim" not in messages[0]["content"]


async def test_suggest_answer_paths():
    docs = [KBDoc("faq.md", "Use /claim.")]
    ok = await suggest_answer(
        FakeChatClient(), docs, title="t", question="q", min_confidence=0.8
    )
    assert ok.post
    none = await suggest_answer(
        FakeChatClient(), [], title="t", question="q", min_confidence=0.8
    )
    assert none.skip_reason == A.NO_KB
    err = await suggest_answer(
        FakeChatClient(fail=True), docs, title="t", question="q", min_confidence=0.8
    )
    assert err.skip_reason == A.ERROR


def test_parse_category():
    assert parse_category('{"category": "bug"}', CATS) == "Bug"
    assert parse_category('{"category": " Claims "}', CATS) == "Claims"
    assert parse_category('{"category": "Minigames"}', CATS) is None
    assert parse_category('{"category": null}', CATS) is None
    assert parse_category("Bug", CATS) is None
    msgs = build_category_messages(CATS, "t", "q")
    assert "Gameplay, Bug, Claims" in msgs[0]["content"]


async def test_suggest_category_handles_failure():
    assert (
        await suggest_category(FakeChatClient(), CATS, title="t", question="q")
        == "Claims"
    )
    assert (
        await suggest_category(FakeChatClient(fail=True), CATS, title="t", question="q")
        is None
    )
