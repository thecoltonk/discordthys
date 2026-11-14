"""Client for Jev, TypeSafe's decision model (served by Hack Club AI).

Jev doesn't write text. It is given some state and a set of questions, and
for each question it returns which of the offered options fits, with a
confidence. That's the whole reason it's used here: whatever a player types,
the most the model can do is pick one of our own pre-written options.

    POST {JEV_BASE_URL}/systemone
    {"model": "jev-latest",
     "state": {...},
     "questions": {"<name>": {"type": "choice", "instructions": "...",
                              "criteria": {"<option>": "<what it means>", ...}}}}

    -> {"answers": {"<name>": {"type": "choice", "choice": "<option>",
                               "confidence": 0.97, "probabilities": {...}}}}

All the questions about one message go in one request.
"""

import logging
from dataclasses import dataclass
from typing import Any
from typing import Protocol

import aiohttp

from nephthys.config import Settings

log = logging.getLogger(__name__)

ENDPOINT = "/systemone"
ERROR_BODY_MAX = 300


class JevError(Exception):
    """The request failed or the response wasn't usable. Callers treat this as
    "no decision", which always means: say nothing."""


@dataclass(frozen=True)
class ChoiceQuestion:
    instructions: str
    # option -> what it means (None = the option's name says it all)
    criteria: dict[str, str | None]

    def payload(self) -> dict[str, Any]:
        return {
            "type": "choice",
            "instructions": self.instructions,
            "criteria": dict(self.criteria),
        }


@dataclass(frozen=True)
class Choice:
    """One answer: the option picked, and how sure the model is (0 to 1)."""

    choice: str
    confidence: float


def as_confidence(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if 0.0 <= number <= 1.0 else None


def parse_answers(
    data: object, questions: dict[str, ChoiceQuestion]
) -> dict[str, Choice]:
    """Keep only answers that are usable: a `choice` answer to a question we
    asked, naming an option we offered, with a confidence between 0 and 1.

    Anything else is dropped, so a malformed or unexpected response can only
    ever lead to "no decision". An answer without a confidence falls back to
    the probability the response gives for the chosen option, if it has one.
    """
    answers = data.get("answers") if isinstance(data, dict) else None
    if not isinstance(answers, dict):
        raise JevError("the response has no `answers` object")
    out: dict[str, Choice] = {}
    for name, question in questions.items():
        answer = answers.get(name)
        if not isinstance(answer, dict) or answer.get("type") != "choice":
            continue
        choice = answer.get("choice")
        if not isinstance(choice, str) or choice not in question.criteria:
            log.warning(f"Jev answered {name!r} with an option that wasn't offered")
            continue
        confidence = as_confidence(answer.get("confidence"))
        if confidence is None:
            probabilities = answer.get("probabilities")
            if isinstance(probabilities, dict):
                confidence = as_confidence(probabilities.get(choice))
        if confidence is None:
            continue
        out[name] = Choice(choice=choice, confidence=confidence)
    return out


class DecisionClient(Protocol):
    model: str

    async def decide(
        self, state: dict[str, Any], questions: dict[str, ChoiceQuestion]
    ) -> dict[str, Choice]:
        """Ask every question about `state` in one request. Returns the usable
        answers by question name (a question may be missing). Raises `JevError`."""
        ...

    async def close(self) -> None: ...


class JevClient:
    def __init__(self, *, api_key: str, base_url: str, model: str, timeout: float):
        self.model = model
        self.url = base_url.rstrip("/") + ENDPOINT
        self._api_key = api_key
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: aiohttp.ClientSession | None = None
        self.requests = 0

    def session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self._session

    async def decide(
        self, state: dict[str, Any], questions: dict[str, ChoiceQuestion]
    ) -> dict[str, Choice]:
        if not questions:
            return {}
        body = {
            "model": self.model,
            "state": state,
            "questions": {name: q.payload() for name, q in questions.items()},
        }
        self.requests += 1
        try:
            async with self.session().post(
                self.url,
                json=body,
                headers={"Authorization": f"Bearer {self._api_key}"},
            ) as response:
                if response.status >= 400:
                    text = (await response.text())[:ERROR_BODY_MAX]
                    raise JevError(f"HTTP {response.status}: {text}")
                try:
                    data = await response.json(content_type=None)
                except ValueError as e:
                    raise JevError(f"the response isn't JSON: {e}") from e
        except (aiohttp.ClientError, TimeoutError) as e:
            raise JevError(f"{type(e).__name__}: {e}") from e
        return parse_answers(data, questions)

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()


def build_client(settings: Settings) -> DecisionClient | None:
    """None while AI_API_KEY is unset: every caller treats that as "off"."""
    if not settings.ai_api_key:
        return None
    return JevClient(
        api_key=settings.ai_api_key,
        base_url=settings.jev_base_url,
        model=settings.jev_model,
        timeout=settings.ai_timeout_seconds,
    )
