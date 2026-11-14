"""A scripted stand-in for the Jev client: records every request, answers
with whatever the test set up."""

from typing import Any

from nephthys.ai.jev import Choice
from nephthys.ai.jev import ChoiceQuestion
from nephthys.ai.jev import JevError


class FakeJev:
    model = "jev-test"

    def __init__(self, **answers: tuple[str, float] | None):
        # question name -> (choice, confidence); a missing name = no usable answer
        self.answers: dict[str, tuple[str, float] | None] = dict(answers)
        self.fail = False
        self.requests: list[tuple[dict[str, Any], dict[str, ChoiceQuestion]]] = []
        self.closed = False

    def set(self, **answers: tuple[str, float] | None) -> None:
        self.answers = dict(answers)

    async def decide(
        self, state: dict[str, Any], questions: dict[str, ChoiceQuestion]
    ) -> dict[str, Choice]:
        self.requests.append((state, dict(questions)))
        if self.fail:
            raise JevError("jev is down")
        out: dict[str, Choice] = {}
        for name in questions:
            answer = self.answers.get(name)
            if answer is not None:
                out[name] = Choice(choice=answer[0], confidence=answer[1])
        return out

    async def close(self) -> None:
        self.closed = True
