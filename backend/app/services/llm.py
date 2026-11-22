"""LLM clients. Every client exposes the same methods, so the rest of the app doesn't
care which model is behind it (this becomes the model gateway in Phase 5)."""

import asyncio
import json
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Protocol

from app.config import Settings, get_settings


@dataclass
class ChatTurn:
    role: str  # "user" | "assistant"
    content: str


@dataclass
class ImageInput:
    """An image attached to the chat, sent to the model for vision (Phase 3)."""

    mime_type: str
    data: bytes
    name: str = ""


class LLMClient(Protocol):
    def stream(
        self, system: str, turns: list[ChatTurn], images: list[ImageInput] | None = None
    ) -> AsyncIterator[str]: ...

    async def complete(self, prompt: str, *, json_mode: bool = False) -> str: ...


class GeminiClient:
    def __init__(self, settings: Settings):
        from google import genai  # imported lazily so tests don't need the SDK configured

        if not settings.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY is not set (or use LLM_PROVIDER=fake)")
        self._client = genai.Client(api_key=settings.gemini_api_key)
        self._model = settings.gemini_model
        self._fast_model = settings.gemini_fast_model

    async def stream(
        self, system: str, turns: list[ChatTurn], images: list[ImageInput] | None = None
    ) -> AsyncIterator[str]:
        from google.genai import types

        contents = [
            types.Content(
                role="user" if t.role == "user" else "model",
                parts=[types.Part(text=t.content)],
            )
            for t in turns
        ]
        if images and contents:
            # Images go with the latest user message.
            contents[-1].parts = [
                *[types.Part.from_bytes(data=i.data, mime_type=i.mime_type) for i in images],
                *contents[-1].parts,
            ]
        response = await self._client.aio.models.generate_content_stream(
            model=self._model,
            contents=contents,
            config=types.GenerateContentConfig(system_instruction=system),
        )
        async for chunk in response:
            if chunk.text:
                yield chunk.text

    async def complete(self, prompt: str, *, json_mode: bool = False) -> str:
        from google.genai import types

        config = types.GenerateContentConfig(response_mime_type="application/json") if json_mode else None
        response = await self._client.aio.models.generate_content(
            model=self._fast_model, contents=prompt, config=config
        )
        return response.text or ""


@dataclass
class FakeClient:
    """Deterministic model for tests and for trying the app without an API key.
    Streams a reply that quotes the sources it was given, so citations show up in the UI."""

    delay: float = 0.02
    calls: list[str] = field(default_factory=list)

    async def stream(
        self, system: str, turns: list[ChatTurn], images: list[ImageInput] | None = None
    ) -> AsyncIterator[str]:
        last = turns[-1].content if turns else ""
        sources = re.findall(r"^\[(\d+)\]", system, flags=re.M)
        if sources:
            reply = f"Here is what I found about: {last}. " + " ".join(f"See source [{n}]." for n in sources)
        else:
            reply = f"You said: {last}. (This is the fake model; set LLM_PROVIDER=gemini for real answers.)"
        if images:
            reply += f" I received {len(images)} image(s)."
        for word in reply.split(" "):
            await asyncio.sleep(self.delay)
            yield word + " "

    async def complete(self, prompt: str, *, json_mode: bool = False) -> str:
        self.calls.append(prompt)
        # Returning "" for JSON prompts makes the agent fall back to its keyword planner,
        # so the fake model still exercises docs/web/tool routes.
        if json_mode:
            return ""
        if "standalone search query" in prompt:  # query rewriting: keep the message as is
            match = re.search(r"Latest message: (.*)\Z", prompt, flags=re.S)
            return match.group(1).strip() if match else ""
        return "Fake chat title"


def parse_json(text: str) -> dict | None:
    """Parse a JSON object from model output (tolerates ```json fences)."""
    if not text:
        return None
    match = re.search(r"\{.*\}", text, flags=re.S)
    if not match:
        return None
    try:
        value = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


@lru_cache
def get_llm() -> LLMClient:
    settings = get_settings()
    if settings.llm_provider == "fake":
        return FakeClient()
    return GeminiClient(settings)
