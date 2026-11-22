"""Where chunks live and how they're searched: BM25 (keyword) and kNN (vector).

`scope` isolates chunks: "kb" is the shared knowledge base; a conversation id means a
file attached to that chat. Every search filters by scope, so attachments never leak
into other chats (Phase 6 adds tenant/role filters the same way)."""

import asyncio
import json
import math
import re
from collections import Counter
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from app.config import Settings, get_settings


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    doc_name: str
    scope: str
    page: int | None
    position: int
    text: str
    embedding: list[float]


@dataclass
class Hit:
    chunk_id: str
    doc_id: str
    doc_name: str
    page: int | None
    text: str
    score: float


class SearchStore(Protocol):
    async def ensure_index(self, dims: int) -> None: ...
    async def index(self, chunks: list[Chunk]) -> None: ...
    async def bm25(self, query: str, scopes: list[str], k: int) -> list[Hit]: ...
    async def knn(self, vector: list[float], scopes: list[str], k: int) -> list[Hit]: ...
    async def delete_document(self, doc_id: str) -> None: ...
    async def close(self) -> None: ...


# --------------------------------------------------------------------------- Elasticsearch


class ElasticsearchStore:
    def __init__(self, settings: Settings):
        from elasticsearch import AsyncElasticsearch

        self._es = AsyncElasticsearch(settings.elasticsearch_url, request_timeout=30)
        self._index = settings.elasticsearch_index

    async def ensure_index(self, dims: int) -> None:
        if await self._es.indices.exists(index=self._index):
            return
        await self._es.indices.create(
            index=self._index,
            mappings={
                "properties": {
                    "chunk_id": {"type": "keyword"},
                    "doc_id": {"type": "keyword"},
                    "doc_name": {"type": "keyword"},
                    "scope": {"type": "keyword"},
                    "page": {"type": "integer"},
                    "position": {"type": "integer"},
                    "text": {"type": "text", "analyzer": "english"},
                    "embedding": {"type": "dense_vector", "dims": dims, "index": True, "similarity": "cosine"},
                }
            },
        )

    async def index(self, chunks: list[Chunk]) -> None:
        from elasticsearch.helpers import async_bulk

        actions = [{"_index": self._index, "_id": c.chunk_id, "_source": asdict(c)} for c in chunks]
        await async_bulk(self._es, actions, refresh="wait_for")

    @staticmethod
    def _hits(response) -> list[Hit]:
        return [
            Hit(
                chunk_id=h["_source"]["chunk_id"],
                doc_id=h["_source"]["doc_id"],
                doc_name=h["_source"]["doc_name"],
                page=h["_source"].get("page"),
                text=h["_source"]["text"],
                score=h["_score"] or 0.0,
            )
            for h in response["hits"]["hits"]
        ]

    async def bm25(self, query: str, scopes: list[str], k: int) -> list[Hit]:
        response = await self._es.search(
            index=self._index,
            size=k,
            query={"bool": {"must": {"match": {"text": query}}, "filter": {"terms": {"scope": scopes}}}},
            source_excludes=["embedding"],
        )
        return self._hits(response)

    async def knn(self, vector: list[float], scopes: list[str], k: int) -> list[Hit]:
        response = await self._es.search(
            index=self._index,
            knn={
                "field": "embedding",
                "query_vector": vector,
                "k": k,
                "num_candidates": max(100, k * 4),
                "filter": {"terms": {"scope": scopes}},
            },
            size=k,
            source_excludes=["embedding"],
        )
        return self._hits(response)

    async def delete_document(self, doc_id: str) -> None:
        await self._es.delete_by_query(
            index=self._index, query={"term": {"doc_id": doc_id}}, refresh=True, ignore_unavailable=True
        )

    async def close(self) -> None:
        await self._es.close()


# --------------------------------------------------------------------------- In-memory


def tokenize(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower())


class MemoryStore:
    """Pure-Python BM25 + cosine kNN, persisted to a JSON file. Fine for a few thousand
    chunks; use Elasticsearch beyond that."""

    K1, B = 1.5, 0.75

    def __init__(self, path: str | None):
        self._path = Path(path) if path else None
        self._chunks: dict[str, Chunk] = {}
        self._lock = asyncio.Lock()
        if self._path and self._path.exists():
            for raw in json.loads(self._path.read_text()):
                self._chunks[raw["chunk_id"]] = Chunk(**raw)

    def _save(self) -> None:
        if self._path:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps([asdict(c) for c in self._chunks.values()]))

    async def ensure_index(self, dims: int) -> None:
        return None

    async def index(self, chunks: list[Chunk]) -> None:
        async with self._lock:
            for c in chunks:
                self._chunks[c.chunk_id] = c
            await asyncio.to_thread(self._save)

    def _in_scope(self, scopes: list[str]) -> list[Chunk]:
        return [c for c in self._chunks.values() if c.scope in scopes]

    @staticmethod
    def _hit(c: Chunk, score: float) -> Hit:
        return Hit(c.chunk_id, c.doc_id, c.doc_name, c.page, c.text, score)

    async def bm25(self, query: str, scopes: list[str], k: int) -> list[Hit]:
        docs = self._in_scope(scopes)
        if not docs:
            return []
        terms = set(tokenize(query))
        tokenized = [tokenize(c.text) for c in docs]
        avg_len = sum(map(len, tokenized)) / len(tokenized)
        df = Counter(t for toks in tokenized for t in set(toks) if t in terms)
        n = len(docs)
        scored = []
        for chunk, toks in zip(docs, tokenized):
            tf = Counter(toks)
            score = 0.0
            for t in terms:
                if tf[t]:
                    idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
                    score += idf * tf[t] * (self.K1 + 1) / (tf[t] + self.K1 * (1 - self.B + self.B * len(toks) / avg_len))
            if score > 0:
                scored.append((score, chunk))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [self._hit(c, s) for s, c in scored[:k]]

    async def knn(self, vector: list[float], scopes: list[str], k: int) -> list[Hit]:
        scored = [(sum(a * b for a, b in zip(vector, c.embedding)), c) for c in self._in_scope(scopes)]
        scored.sort(key=lambda x: x[0], reverse=True)
        return [self._hit(c, s) for s, c in scored[:k]]

    async def delete_document(self, doc_id: str) -> None:
        async with self._lock:
            self._chunks = {k: c for k, c in self._chunks.items() if c.doc_id != doc_id}
            await asyncio.to_thread(self._save)

    async def close(self) -> None:
        return None


@lru_cache
def get_store() -> SearchStore:
    settings = get_settings()
    if settings.search_backend == "memory":
        return MemoryStore(settings.memory_store_path)
    return ElasticsearchStore(settings)
