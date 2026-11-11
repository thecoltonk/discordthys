"""OpenAI-compatible chat client (pointed at Hack Club AI by default).

Everything AI is optional: `build_client()` returns None when AI_API_KEY is
unset, and every caller treats None as "AI off".
"""

import logging
from typing import Any
from typing import Protocol

import openai
from openai import AsyncOpenAI

from nephthys.config import Settings

log = logging.getLogger(__name__)

type Message = dict[str, str]


class ChatClient(Protocol):
    model: str

    async def complete(self, messages: list[Message]) -> str:
        """Return the model's raw text response (expected to be JSON)."""
        ...


class OpenAIChatClient:
    def __init__(self, *, api_key: str, base_url: str, model: str, timeout: float):
        self.model = model
        self._client = AsyncOpenAI(
            api_key=api_key, base_url=base_url, timeout=timeout, max_retries=1
        )
        # Some providers/models reject response_format; remember and stop sending it
        self._json_mode = True

    async def complete(self, messages: list[Message]) -> str:
        if self._json_mode:
            try:
                return await self._create(messages, json_mode=True)
            except openai.BadRequestError as e:
                if "response_format" not in str(e):
                    raise
                log.warning(
                    "Model rejected response_format=json_object; retrying without"
                )
                self._json_mode = False
        return await self._create(messages, json_mode=False)

    async def _create(self, messages: list[Message], *, json_mode: bool) -> str:
        payload: Any = messages
        if json_mode:
            response = await self._client.chat.completions.create(
                model=self.model,
                messages=payload,
                temperature=0,
                response_format={"type": "json_object"},
            )
        else:
            response = await self._client.chat.completions.create(
                model=self.model, messages=payload, temperature=0
            )
        if not response.choices:
            return ""
        return response.choices[0].message.content or ""


def build_client(settings: Settings) -> ChatClient | None:
    if not settings.ai_enabled or not settings.ai_api_key or not settings.ai_model:
        return None
    return OpenAIChatClient(
        api_key=settings.ai_api_key,
        base_url=settings.ai_base_url,
        model=settings.ai_model,
        timeout=settings.ai_timeout_seconds,
    )
