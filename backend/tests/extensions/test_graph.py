"""Provenance and traversal limits on the actual source corpus, no model requests."""

import json
from pathlib import Path

import pytest

from apex_rag.graph.engine import audit, build_graph, expand

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def graph_data():
    docs = json.loads((ROOT / "data" / "kb_documents.json").read_text(encoding="utf-8"))
    return build_graph(docs), docs


def test_all_edges_have_verbatim_provenance(graph_data):
    graph, docs = graph_data
    result = audit(graph, docs)
    assert result["passed"] and result["covered_documents"] == 150
    assert {"CardProduct", "Policy", "Benefit", "FeeRule", "SourceDocument"} <= set(
        result["node_types"]
    )


def test_cross_product_path_and_source(graph_data):
    graph, docs = graph_data
    result = expand(
        graph, ["DOC-0046"], "Compare Apex Platinum and Apex Preferred Travel lounge access"
    )
    assert "DOC-0047" in result["expanded_doc_ids"]
    assert all(p["hops"] <= 2 for p in result["paths"])
    sources = {d["doc_id"]: d["content"] for d in docs}
    assert all(e["evidence"] in sources[e["doc_id"]] for p in result["paths"] for e in p["edges"])


def test_limits_and_filter(graph_data):
    graph, _ = graph_data
    result = expand(
        graph,
        ["DOC-0001"],
        "Apex Platinum balance transfer",
        card_tier="Apex Platinum",
        max_documents=2,
        max_nodes=6,
    )
    assert len(result["expanded_doc_ids"]) <= 2 and result["visited_nodes"] <= 6
    assert all(
        graph.nodes["policy:" + d]["card_tier"] == "Apex Platinum"
        for d in result["expanded_doc_ids"]
    )
    with pytest.raises(ValueError):
        expand(graph, [], "", max_hops=3)


def test_challenges_quotes_and_categories():
    docs = {
        d["doc_id"]: d for d in json.loads((ROOT / "data" / "kb_documents.json").read_text(encoding="utf-8"))
    }
    queries = json.loads((ROOT / "data/challenge_queries.json").read_text(encoding="utf-8"))
    assert {"cross_product", "same_product_multiple_rules", "conditions_and_operations"} <= {
        q["kind"] for q in queries
    }
    for query in queries:
        assert all(s["quote"] in docs[s["doc_id"]]["content"] for s in query["support"])


def test_late_number_seeds_survive_core_rerank_cutoff(graph_data):
    from types import SimpleNamespace

    from apex_rag.core.config import Settings
    from apex_rag.graph.engine import GraphSearch
    from apex_rag.retrieval.service import SearchService, aggregate, trace
    from apex_rag.schemas import SearchRequest, SearchResponse, Timings, Trace

    graph, docs = graph_data
    chunks = []
    for doc in docs:
        for ordinal in range(4):
            chunks.append(
                {
                    **doc,
                    "effective_year": int(doc["effective_year"]),
                    "chunk_id": f"{doc['doc_id']}:{ordinal}",
                    "text": doc["content"],
                    "context_text": doc["content"],
                    "parent_id": None,
                }
            )
    seed_ids = ["DOC-0140", "DOC-0141", "DOC-0142", "DOC-0143", "DOC-0136"]
    ordered = [c for doc_id in seed_ids for c in chunks if c["doc_id"] == doc_id]
    base = SearchResponse(
        request_id="test",
        build_id="a" * 24,
        mode="hybrid",
        results=aggregate(ordered, 5),
        timings_ms=Timings(retrieval_total=1),
        trace=Trace(fusion=trace(ordered)),
    )
    received = []

    def rerank(query, texts, *, top_n):
        received.extend(texts)
        return [(i, 1.0) for i in range(len(texts))]

    settings = Settings()
    core = SearchService(
        settings, SimpleNamespace(), SimpleNamespace(), SimpleNamespace(rerank=rerank)
    )
    # Exercise the actual core rerank_candidates, replacing only paid seed acquisition.
    core.search = lambda *a, **k: base
    services = SimpleNamespace(
        settings=settings,
        search=core,
        repository=SimpleNamespace(list_documents=lambda: docs, list_chunks=lambda *a: chunks),
    )
    result = GraphSearch(services)(
        SearchRequest(
            query="Compare billing statements and minimum payments across Apex cards.",
            mode="graph_enhanced",
        )
    )
    assert len(received) <= settings.rerank_depth
    assert all(
        any(d["doc_id"] == seed and d["content"] == text for d in docs)
        for seed, text in zip(seed_ids, received[:5], strict=False)
    )
    delivered = {t.doc_id for t in result.trace.rerank}
    assert set(seed_ids) <= delivered
    assert all(p["nodes"][-1]["doc_id"] in delivered for p in result.graph_paths)


def test_initial_seeds_are_bounded(graph_data):
    graph, docs = graph_data
    result = expand(graph, [d["doc_id"] for d in docs], "", max_nodes=3, max_documents=2)
    assert (
        len(result["expanded_doc_ids"]) <= 2
        and result["visited_nodes"] <= 3
        and result["truncated"]
    )


def test_freeze_and_autopay_rules_are_source_linked(graph_data):
    graph, _ = graph_data
    result = expand(
        graph, ["DOC-0061"], "For Apex Platinum explain replacement, freezing and AutoPay choices."
    )
    assert {"DOC-0061", "DOC-0069", "DOC-0144"} <= set(result["expanded_doc_ids"])
