"""LLM clients. Every client exposes the same two methods, so the rest of the app
doesn't care which model is behind it (this becomes the model gateway in Phase 5)."""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

from app.config import Settings, get_settings


@dataclass
class ChatTurn:
    role: str  # "user" | "assistant"
    content: str


class LLMClient(Protocol):
    def stream(self, system: str, turns: list[ChatTurn]) -> AsyncIterator[str]: ...

    async def complete(self, prompt: str) -> str: ...


class GeminiClient:
    def __init__(self, settings: Settings):
        from google import genai  # imported lazily so tests don't need the SDK configured

        if not settings.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY is not set (or use LLM_PROVIDER=fake)")
        self._client = genai.Client(api_key=settings.gemini_api_key)
        self._model = settings.gemini_model

    async def stream(self, system: str, turns: list[ChatTurn]) -> AsyncIterator[str]:
        from google.genai import types

        contents = [
            types.Content(
                role="user" if t.role == "user" else "model",
                parts=[types.Part(text=t.content)],
            )
            for t in turns
        ]
        response = await self._client.aio.models.generate_content_stream(
            model=self._model,
            contents=contents,
            config=types.GenerateContentConfig(system_instruction=system),
        )
        async for chunk in response:
            if chunk.text:
                yield chunk.text

    async def complete(self, prompt: str) -> str:
        response = await self._client.aio.models.generate_content(model=self._model, contents=prompt)
        return response.text or ""


class FakeClient:
    """Streams a deterministic reply word by word. No API key, no network."""

    def __init__(self, delay: float = 0.02):
        self._delay = delay

    async def stream(self, system: str, turns: list[ChatTurn]) -> AsyncIterator[str]:
        last = turns[-1].content if turns else ""
        reply = f"You said: {last}. (This is the fake model; set LLM_PROVIDER=gemini for real answers.)"
        for word in reply.split(" "):
            await asyncio.sleep(self._delay)
            yield word + " "

    async def complete(self, prompt: str) -> str:
        return "Fake chat title"


@lru_cache
def get_llm() -> LLMClient:
    settings = get_settings()
    if settings.llm_provider == "fake":
        return FakeClient()
    return GeminiClient(settings)
