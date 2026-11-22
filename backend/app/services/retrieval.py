"""Hybrid retrieval: BM25 + kNN, fused with Reciprocal Rank Fusion, optionally reranked
by a cross-encoder."""

import asyncio
from dataclasses import dataclass, field
from functools import lru_cache

from app.config import Settings, get_settings
from app.services.embeddings import Embedder, get_embedder
from app.services.search_store import Hit, SearchStore, get_store


@dataclass
class RankedHit:
    hit: Hit
    rrf_score: float
    bm25_rank: int | None = None
    knn_rank: int | None = None
    rerank_score: float | None = None


@dataclass
class RetrievalResult:
    hits: list[RankedHit]
    debug: dict = field(default_factory=dict)


def rrf_merge(result_lists: dict[str, list[Hit]], k: int = 60) -> list[RankedHit]:
    """score(doc) = sum over lists of 1 / (k + rank). Uses ranks only, so BM25 scores and
    cosine similarities never have to be normalized against each other."""
    merged: dict[str, RankedHit] = {}
    for name, hits in result_lists.items():
        for rank, hit in enumerate(hits, start=1):
            entry = merged.setdefault(hit.chunk_id, RankedHit(hit=hit, rrf_score=0.0))
            entry.rrf_score += 1.0 / (k + rank)
            setattr(entry, f"{name}_rank", rank)
    return sorted(merged.values(), key=lambda r: r.rrf_score, reverse=True)


class CrossEncoderReranker:
    def __init__(self, model_name: str):
        from sentence_transformers import CrossEncoder

        self._model = CrossEncoder(model_name)

    async def rerank(self, query: str, hits: list[RankedHit]) -> list[RankedHit]:
        if not hits:
            return hits
        scores = await asyncio.to_thread(self._model.predict, [(query, h.hit.text) for h in hits])
        for h, s in zip(hits, scores):
            h.rerank_score = float(s)
        return sorted(hits, key=lambda h: h.rerank_score, reverse=True)


@lru_cache
def get_reranker() -> CrossEncoderReranker | None:
    settings = get_settings()
    return CrossEncoderReranker(settings.reranker_model) if settings.reranker == "local" else None


class Retriever:
    def __init__(self, store: SearchStore, embedder: Embedder, settings: Settings, reranker=None):
        self.store = store
        self.embedder = embedder
        self.settings = settings
        self.reranker = reranker

    async def search(self, query: str, scopes: list[str], top_k: int | None = None) -> RetrievalResult:
        s = self.settings
        top_k = top_k or s.retrieval_top_k
        [vector] = await self.embedder.embed([query], task="query")
        bm25_hits, knn_hits = await asyncio.gather(
            self.store.bm25(query, scopes, s.retrieval_candidates),
            self.store.knn(vector, scopes, s.retrieval_candidates),
        )
        fused = rrf_merge({"bm25": bm25_hits, "knn": knn_hits}, k=s.rrf_k)
        if self.reranker:
            fused = await self.reranker.rerank(query, fused[: s.rerank_candidates])
        return RetrievalResult(
            hits=fused[:top_k],
            debug={"bm25_count": len(bm25_hits), "knn_count": len(knn_hits), "fused_count": len(fused)},
        )


def get_retriever() -> Retriever:
    return Retriever(get_store(), get_embedder(), get_settings(), get_reranker())
