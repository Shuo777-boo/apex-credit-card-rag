"""Dense, PostgreSQL FTS/RRF, real reranking and unique-document evidence aggregation."""

import time
from collections.abc import Callable
from typing import Any
from uuid import uuid4

from apex_rag.core.config import Settings
from apex_rag.core.errors import ApexError
from apex_rag.providers.interfaces import EmbeddingProvider, RerankerProvider
from apex_rag.schemas import (
    Evidence,
    RankedChunk,
    SearchRequest,
    SearchResponse,
    SearchResult,
    Timings,
    Trace,
)
from apex_rag.storage.repository import Repository


def rrf(lists: list[list[dict[str, Any]]], constant: int = 60) -> list[dict[str, Any]]:
    """Fuse chunk ranks with sum(1/(constant+one-based rank)), never raw stage scores.

    Missing routes contribute zero. A duplicate chunk contributes once per route;
    equal totals sort by chunk_id for deterministic results. No I/O or model calls.
    """
    if constant < 1:
        raise ValueError("RRF constant must be positive")
    scores: dict[str, float] = {}
    values: dict[str, dict[str, Any]] = {}
    for route in lists:
        seen: set[str] = set()
        for rank, item in enumerate(route, 1):
            identity = item["chunk_id"]
            if identity in seen:
                continue
            seen.add(identity)
            scores[identity] = scores.get(identity, 0) + 1 / (constant + rank)
            values[identity] = item
    return [{**values[key], "score": scores[key]} for key in sorted(scores, key=lambda k: (-scores[k], k))]


def trace(rows: list[dict[str, Any]]) -> list[RankedChunk]:
    return [RankedChunk(chunk_id=r["chunk_id"], doc_id=r["doc_id"], rank=i,
                        score=r.get("score")) for i, r in enumerate(rows, 1)]


def aggregate(rows: list[dict[str, Any]], top_k: int) -> list[SearchResult]:
    """Take first top_k unique original documents from already ranked chunks.

    First/best chunk supplies document score; remaining retrieved chunks retain evidence
    and source offsets. Does not fabricate documents to fill a short result list.
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(row["doc_id"], []).append(row)
    results = []
    for rank, group in enumerate(list(groups.values())[:top_k], 1):
        best = group[0]
        results.append(SearchResult(**{k: best[k] for k in ["doc_id", "title", "category", "card_tier", "effective_year"]},
            rank=rank, score=best.get("score"), evidence=[Evidence(chunk_id=x["chunk_id"],
                text=x["text"], parent_id=x.get("parent_id"), start_char=x.get("start_char"),
                end_char=x.get("end_char")) for x in group]))
    return results


class SearchService:
    def __init__(self, settings: Settings, repository: Repository, embedding: EmbeddingProvider,
                 reranker: RerankerProvider) -> None:
        self.settings, self.repository = settings, repository
        self.embedding, self.reranker = embedding, reranker
        self.graph_search: Callable[..., SearchResponse] | None = None

    def search(self, request: SearchRequest, *, request_id: str | None = None) -> SearchResponse:
        """Run real paid query embedding, DB recalls, optional RRF and paid reranking.

        Returns unique documents and every executed chunk ranking with monotonic ms.
        Explicit card filters apply to both recalls. Rejects unready or model-mismatched
        builds before embedding. Graph requests delegate only to a registered hook.
        Provider/database failures propagate; no fixture or chat-based ranking fallback.
        """
        request_id = request_id or str(uuid4())
        if request.mode == "graph_enhanced":
            if not self.graph_search:
                raise ApexError("EXTENSION_UNAVAILABLE", "Graph search extension is not registered")
            return self.graph_search(request, request_id=request_id)
        started = time.perf_counter()
        build = self.repository.get_build(request.build_id)
        if build["status"] != "ready":
            raise ApexError("BUILD_NOT_READY", "Only complete indexed builds may be searched")
        if build["config"]["embedding_fingerprint"] != self.settings.embedding_fingerprint:
            raise ApexError("VECTOR_MISMATCH", "Active build model, region or dimension differs from query provider", 409)
        timings = Timings()
        at = time.perf_counter()
        vector = self.embedding.embed([request.query], input_type="query")[0]
        timings.embedding = (time.perf_counter() - at) * 1000
        recall_started = at = time.perf_counter()
        dense = self.repository.dense(build, vector, self.settings.candidate_depth, request.card_tier)
        timings.dense = (time.perf_counter() - at) * 1000
        stages = Trace(dense=trace(dense))
        rows = dense
        if request.mode != "dense":
            at = time.perf_counter()
            sparse = self.repository.sparse(build, request.query, self.settings.candidate_depth, request.card_tier)
            timings.sparse = (time.perf_counter() - at) * 1000
            stages.sparse = trace(sparse)
            at = time.perf_counter()
            rows = rrf([dense, sparse], self.settings.rrf_k)
            timings.fusion = (time.perf_counter() - at) * 1000
            stages.fusion = trace(rows)
        timings.recall_fusion = (time.perf_counter() - recall_started) * 1000
        if request.mode == "hybrid_rerank":
            rows, timings.rerank = self._rerank(request.query, rows[:self.settings.rerank_depth])
            stages.rerank = trace(rows)
        timings.retrieval_total = (time.perf_counter() - started) * 1000
        return SearchResponse(request_id=request_id, build_id=build["build_id"], mode=request.mode,
            results=aggregate(rows, request.top_k), timings_ms=timings, trace=stages)

    def _rerank(self, query: str, rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], float]:
        at = time.perf_counter()
        if not rows:
            return [], (time.perf_counter() - at) * 1000
        ranked = self.reranker.rerank(query, [x["context_text"] for x in rows], top_n=len(rows))
        return [{**rows[index], "score": score} for index, score in ranked], (time.perf_counter() - at) * 1000

    def rerank_candidates(self, request: SearchRequest, chunks: list[dict[str, Any]], *,
                          base_response: SearchResponse, request_id: str | None = None) -> SearchResponse:
        """Rerank extension-supplied ordered chunks through the same paid provider.

        Deduplicates/filter-caps candidates at rerank_depth; extensions must order seed
        and expansion evidence deliberately and track which paths enter this budget.
        Copies base traces, replaces final rank, aggregates original docs, and adds ms.
        """
        at = time.perf_counter()
        unique = {c["chunk_id"]: c for c in chunks if not request.card_tier or c["card_tier"] == request.card_tier}
        # Bounded candidate count is explicit and deterministic; extension orders seeds first.
        rows, elapsed = self._rerank(request.query, list(unique.values())[:self.settings.rerank_depth])
        result = base_response.model_copy(deep=True)
        result.request_id = request_id or base_response.request_id
        result.mode = "graph_enhanced"
        result.results = aggregate(rows, request.top_k)
        result.trace.rerank = trace(rows)
        result.timings_ms.rerank = elapsed
        result.timings_ms.retrieval_total = (base_response.timings_ms.retrieval_total or 0) + (time.perf_counter() - at) * 1000
        return result
