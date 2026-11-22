"""Text -> vector. Same interface for Gemini (free tier), a local model on CPU, and a
fake hashing embedder used in tests."""

import asyncio
import hashlib
import math
import re
from functools import lru_cache
from typing import Literal, Protocol

from app.config import Settings, get_settings

Task = Literal["document", "query"]


class Embedder(Protocol):
    dims: int

    async def embed(self, texts: list[str], task: Task) -> list[list[float]]: ...


def _normalize(v: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / norm for x in v]


class GeminiEmbedder:
    BATCH = 100

    def __init__(self, settings: Settings):
        from google import genai

        if not settings.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY is not set (or use EMBEDDING_PROVIDER=local/fake)")
        self._client = genai.Client(api_key=settings.gemini_api_key)
        self._model = settings.embedding_model
        self.dims = settings.embedding_dims

    async def embed(self, texts: list[str], task: Task) -> list[list[float]]:
        from google.genai import types

        config = types.EmbedContentConfig(
            task_type="RETRIEVAL_DOCUMENT" if task == "document" else "RETRIEVAL_QUERY",
            output_dimensionality=self.dims,
        )
        vectors: list[list[float]] = []
        for i in range(0, len(texts), self.BATCH):
            result = await self._client.aio.models.embed_content(
                model=self._model, contents=texts[i : i + self.BATCH], config=config
            )
            # Truncated Gemini embeddings must be re-normalized for cosine similarity.
            vectors.extend(_normalize(list(e.values)) for e in result.embeddings)
        return vectors


class LocalEmbedder:
    """sentence-transformers on CPU (pip install -r requirements-local-models.txt)."""

    def __init__(self, settings: Settings):
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(settings.local_embedding_model)
        self.dims = self._model.get_sentence_embedding_dimension()
        # bge models expect this prefix on queries.
        self._query_prefix = "Represent this sentence for searching relevant passages: " if "bge" in settings.local_embedding_model else ""

    async def embed(self, texts: list[str], task: Task) -> list[list[float]]:
        if task == "query":
            texts = [self._query_prefix + t for t in texts]
        vectors = await asyncio.to_thread(self._model.encode, texts, normalize_embeddings=True, batch_size=32)
        return [v.tolist() for v in vectors]


class FakeEmbedder:
    """Feature hashing over words: no model, deterministic, and texts that share words
    get similar vectors, so kNN results are meaningful enough for tests and demos."""

    def __init__(self, dims: int = 256):
        self.dims = dims

    async def embed(self, texts: list[str], task: Task) -> list[list[float]]:
        out = []
        for text in texts:
            v = [0.0] * self.dims
            for word in re.findall(r"\w+", text.lower()):
                h = int(hashlib.md5(word.encode()).hexdigest(), 16)
                v[h % self.dims] += 1.0 if (h >> 8) % 2 else -1.0
            out.append(_normalize(v))
        return out


@lru_cache
def get_embedder() -> Embedder:
    settings = get_settings()
    if settings.embedding_provider == "fake":
        return FakeEmbedder()
    if settings.embedding_provider == "local":
        return LocalEmbedder(settings)
    return GeminiEmbedder(settings)
