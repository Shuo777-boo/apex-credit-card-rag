"""Non-paid algorithm and public contract regression tests."""

import math

import pytest
from pydantic import ValidationError

from apex_rag.core.config import Settings
from apex_rag.core.errors import ApexError
from apex_rag.evaluation.metrics import quantiles, retrieval_metrics, summarize
from apex_rag.generation.service import citation_ids
from apex_rag.ingestion.chunking import chunk_document, context_prefix
from apex_rag.ingestion.data import load_official
from apex_rag.retrieval.service import aggregate, rrf
from apex_rag.schemas import SearchRequest


def test_official_and_true_chunk_boundaries():
    docs, questions, hashes = load_official()
    assert len(docs) == 150 and len(questions) == 30
    assert hashes["kb_documents.json"] == "7d230e97cbf76d2ca64382e7c0278c3b24bbe27b8a8904abc9553b45a230057d"
    fixed = chunk_document(docs[0], "fixed_window")
    hierarchy = chunk_document(docs[0], "policy_hierarchy")
    assert len(fixed) > len(hierarchy) == 1
    assert hierarchy[0].start_char == docs[0].content.index("Details: ") + len("Details: ")
    assert fixed[0].text[-60:] == fixed[1].text[:60]
    for strategy in ["fixed_window", "policy_hierarchy"]:
        for doc in docs:
            chunks = chunk_document(doc, strategy)
            assert chunks
            assert chunks == chunk_document(doc, strategy)
            for chunk in chunks:
                assert chunk.text == doc.content[chunk.start_char:chunk.end_char]
                assert chunk.context_text == context_prefix(doc) + chunk.text
                assert chunk.doc_id == doc.doc_id
    assert fixed[0].chunk_id != hierarchy[0].chunk_id


def test_long_hierarchy_preserves_rule_spans():
    docs, _, _ = load_official()
    doc = docs[0].model_copy(update={"content": "Details: " + "Each rule includes conditions. " * 100})
    chunks = chunk_document(doc, "policy_hierarchy", hierarchy_max=100)
    assert len(chunks) > 1
    assert "".join(c.text for c in chunks) == doc.content[9:]
    assert all(len(c.text) <= 100 for c in chunks)


def test_rrf_real_rank_formula_missing_route_and_stable_tie():
    a = {"chunk_id": "a", "doc_id": "D1", "score": 99}
    b = {"chunk_id": "b", "doc_id": "D2", "score": -20}
    c = {"chunk_id": "c", "doc_id": "D3", "score": 3}
    fused = rrf([[a, b], [b, c]])
    assert [r["chunk_id"] for r in fused] == ["b", "a", "c"]
    assert fused[0]["score"] == pytest.approx(1 / 62 + 1 / 61)
    assert fused[1]["score"] == pytest.approx(1 / 61)
    assert [x["chunk_id"] for x in rrf([[a], [c]])] == ["a", "c"]
    assert rrf([[a, a]])[0]["score"] == pytest.approx(1 / 61)


def test_document_aggregation_retains_best_and_extra_chunks():
    def row(chunk, doc, score):
        return dict(chunk_id=chunk, doc_id=doc, score=score, title=doc, category="Fees",
                    card_tier="Apex Platinum", effective_year=2024, text=chunk)
    result = aggregate([row("c2", "D1", .9), row("c3", "D2", .8), row("c1", "D1", .7)], 5)
    assert [r.doc_id for r in result] == ["D1", "D2"]
    assert result[0].score == .9
    assert [e.chunk_id for e in result[0].evidence] == ["c2", "c1"]


def test_document_metrics_hand_calculated_shortfall_and_mrr20():
    metric = retrieval_metrics(["A", "A", "X", "B"], ["A", "B", "C"])
    assert metric["recall@3"] == pytest.approx(2 / 3)
    assert metric["precision@5"] == .4
    assert metric["ndcg@3"] == pytest.approx((1 + 1 / math.log2(4)) / (1 + 1 / math.log2(3) + 1 / math.log2(4)))
    assert retrieval_metrics(["X"] * 22 + ["B"], ["B"])["mrr@20"] == .5
    assert retrieval_metrics([str(i) for i in range(20)] + ["B"], ["B"])["mrr@20"] == 0
    assert retrieval_metrics(["A"], ["A"])["precision@5"] == .2


def test_failures_remain_quality_denominator_and_latency_separate():
    good = {"metrics": retrieval_metrics(["A"], ["A"]), "error": None,
            "timings_ms": {"dense": 2, "sparse": 3, "retrieval_total": 200}}
    failed = {"metrics": retrieval_metrics([], ["A"]), "error": {"code": "TIMEOUT"}, "timings_ms": {}}
    summary = summarize([good, failed])
    assert summary["recall@1"] == .5
    assert summary["counts"] == {"total": 2, "succeeded": 1, "failed": 1}
    assert summary["latency_ms"]["database"] == {"p50": 5, "p99": 5, "samples": 1}
    assert quantiles([]) == {"p50": None, "p99": None, "samples": 0}


def test_dimension_rejection_and_schema_constraints():
    with pytest.raises(ValidationError):
        Settings(embedding_dimension=2048)
    with pytest.raises(ValidationError):
        SearchRequest(query="   ")
    with pytest.raises(ValidationError):
        SearchRequest(query="q", card_tier="invented card")
    with pytest.raises(ValidationError):
        SearchRequest(query="q", top_k=21)
    assert SearchRequest(query=" q ").query == "q"


def test_citation_identifier_validation_is_not_semantic_proof():
    assert citation_ids("Fee [1], condition [2]", {1, 2}, require=True) == {1, 2}
    with pytest.raises(ApexError, match="citation"):
        citation_ids("Wrong [3]", {1, 2}, require=True)
    with pytest.raises(ApexError):
        citation_ids("Unsupported assertion", {1}, require=True)
    assert citation_ids("证据不足", {1}, require=False) == set()
