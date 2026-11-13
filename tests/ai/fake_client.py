import asyncio
import json
from typing import Any


def answer_json(
    answer: str | None = "use /claim", confidence: float = 0.9, sources=("faq.md",)
) -> str:
    return json.dumps(
        {"answer": answer, "confidence": confidence, "kb_sources": list(sources)}
    )


class FakeChatClient:
    """Scripted stand-in for the OpenAI-compatible client."""

    model = "fake-model"

    def __init__(
        self,
        *,
        category: str = '{"category": "Claims"}',
        answer: str | None = None,
        fail: bool = False,
    ):
        self.category = category
        self.answer = answer if answer is not None else answer_json()
        self.fail = fail
        self.calls: list[list[dict[str, Any]]] = []
        # When set, answers wait for it ("the model is still thinking")
        self.answer_gate: asyncio.Event | None = None

    async def complete(self, messages: list[dict[str, str]]) -> str:
        self.calls.append(messages)
        if self.fail:
            raise RuntimeError("provider down")
        if "sort support questions" in messages[0]["content"]:
            return self.category
        if self.answer_gate is not None:
            await self.answer_gate.wait()
        return self.answer
