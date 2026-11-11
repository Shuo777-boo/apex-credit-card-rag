"""Build a conservative directed policy graph from source documents, never gold answers."""

from __future__ import annotations

import hashlib
import re
import time
from collections import deque
from typing import Any

import networkx as nx

VERSION = "source-rules-v1"
TOPICS = {
    "lounge": r"\blounge\b",
    "phone protection": r"\b(cell phone|wireless|phone protection)\b",
    "travel credit": r"\b(travel credit|hotel credit)\b",
    "balance transfer": r"\bbalance transfer",
    "annual fee": r"\bannual fee\b",
    "foreign transaction": r"\bforeign transaction",
    "late payment": r"\b(late fee|late payment)\b",
    "fraud": r"\b(fraud|unauthorized|zero liability)\b",
    "dispute": r"\b(dispute|chargeback|provisional credit)\b",
    "replacement": r"\b(replacement|replace|rush delivery|emergency cash)\b",
    "points": r"\b(points|reward|redemption)\b",
    "autopay": r"\b(auto.?pay|automatic payment)\b",
    "card freeze": r"\b(freez\w*|lock|recurring subscriptions|automated monthly payments)\b",
    "authorized user": r"\bauthorized user",
    "rental insurance": r"\b(rental|collision damage)\b",
    "trip insurance": r"\b(trip|baggage|evacuation)\b",
    "credit limit": r"\b(credit limit|credit line|credit increase)\b",
    "billing": r"\b(statement|billing cycle|grace period|minimum payment)\b",
}
FEE = re.compile(r"\b(fee|APR|interest|deductible)\b", re.I)
BENEFIT = re.compile(
    r"\b(benefit|coverage|protection|credit|lounge|reward|insurance|waiver)\b", re.I
)


def build_graph(documents: list[dict[str, Any]]) -> nx.DiGraph:
    """Every edge stores a verbatim source span and immutable extraction provenance.

    Numerical values and conditions are kept in the original sentence; no normalized
    amount, inference of absence, or cross-year override is asserted by these rules.
    """
    graph = nx.DiGraph(version=VERSION)
    for document in documents:
        doc = dict(document)
        text = doc["content"]
        doc_id = doc["doc_id"]
        policy = f"policy:{doc_id}"
        source = f"source:{doc_id}"
        product = f"product:{doc['card_tier']}"
        graph.add_node(
            policy,
            type="Policy",
            label=doc["title"],
            doc_id=doc_id,
            card_tier=doc["card_tier"],
            effective_year=int(doc["effective_year"]),
        )
        graph.add_node(source, type="SourceDocument", label=doc_id, doc_id=doc_id)
        graph.add_node(product, type="CardProduct", label=doc["card_tier"])
        provenance = {
            "doc_id": doc_id,
            "evidence": text,
            "extraction_method": "metadata+verbatim-regex",
            "version": VERSION,
            "source_sha256": hashlib.sha256(text.encode()).hexdigest(),
        }
        graph.add_edge(policy, source, relation="SUPPORTED_BY", **provenance)
        graph.add_edge(policy, product, relation="APPLIES_TO", **provenance)
        graph.add_edge(product, policy, relation="HAS_POLICY", **provenance)
        details = text.split("Details:", 1)[-1].strip()
        for index, sentence in enumerate(re.split(r"(?<=[.!?])\s+(?=[A-Z])", details)):
            # Sentence extraction never synthesizes values; validate it is literally present.
            if not sentence or sentence not in text:
                continue
            kind = (
                "FeeRule"
                if FEE.search(sentence)
                else ("Benefit" if BENEFIT.search(sentence) else None)
            )
            if kind:
                fact = f"fact:{doc_id}:{index}"
                graph.add_node(
                    fact,
                    type=kind,
                    label=sentence,
                    doc_id=doc_id,
                    numbers=re.findall(r"\$?\d+(?:[.,]\d+)?%?", sentence),
                    conditions_preserved=True,
                )
                graph.add_edge(
                    policy,
                    fact,
                    relation="HAS_FEE" if kind == "FeeRule" else "PROVIDES",
                    **{**provenance, "evidence": sentence},
                )
                graph.add_edge(
                    fact, source, relation="SUPPORTED_BY", **{**provenance, "evidence": sentence}
                )
        for topic, pattern in TOPICS.items():
            if re.search(pattern, doc["title"] + " " + details, re.I):
                node = f"topic:{topic}"
                graph.add_node(node, type="PolicyTopic", label=topic)
                graph.add_edge(policy, node, relation="ABOUT", **provenance)
                graph.add_edge(node, policy, relation="DESCRIBED_BY", **provenance)
    return graph


def expand(
    graph: nx.DiGraph,
    seed_doc_ids: list[str],
    query: str,
    *,
    card_tier: str | None = None,
    max_hops: int = 2,
    max_nodes: int = 160,
    max_documents: int = 30,
    max_paths: int = 40,
) -> dict[str, Any]:
    """Traverse directed, evidence-backed edges with hard limits and a visited set."""
    if not 1 <= max_hops <= 2 or min(max_nodes, max_documents, max_paths) < 1:
        raise ValueError("Traversal requires 1..2 hops and positive finite limits.")
    products = [
        a["label"]
        for _, a in graph.nodes(data=True)
        if a["type"] == "CardProduct" and a["label"].lower() in query.lower()
    ]
    if card_tier:
        products = [card_tier]
    topics = {f"topic:{name}" for name, regex in TOPICS.items() if re.search(regex, query, re.I)}
    all_seeds = list(dict.fromkeys(f"policy:{d}" for d in seed_doc_ids if f"policy:{d}" in graph))
    seeds = all_seeds[: min(max_nodes, max_documents)]
    queue = deque((node, [node]) for node in seeds)
    visited = set(seeds)
    found = [graph.nodes[n]["doc_id"] for n in seeds]
    paths: list[dict[str, Any]] = []
    truncated = len(all_seeds) > len(seeds)
    while queue:
        node, path = queue.popleft()
        if len(path) - 1 >= max_hops:
            continue
        for neighbor in sorted(graph.successors(node)):
            if neighbor in path:
                continue
            attrs = graph.nodes[neighbor]
            if attrs["type"] in {"SourceDocument", "Benefit", "FeeRule"}:
                continue
            if attrs["type"] == "PolicyTopic" and topics and neighbor not in topics:
                continue
            if attrs["type"] == "CardProduct" and products and attrs["label"] not in products:
                continue
            if attrs["type"] == "Policy" and products and attrs["card_tier"] not in products:
                continue
            # Product fan-out is broad; when query has a known topic require that topic.
            if (
                attrs["type"] == "Policy"
                and topics
                and not (set(graph.successors(neighbor)) & topics)
            ):
                continue
            new_path = [*path, neighbor]
            if neighbor not in visited:
                if len(visited) >= max_nodes:
                    truncated = True
                    continue
                visited.add(neighbor)
                queue.append((neighbor, new_path))
            if attrs["type"] == "Policy" and attrs["doc_id"] not in found:
                if len(found) >= max_documents or len(paths) >= max_paths:
                    truncated = True
                    continue
                found.append(attrs["doc_id"])
                edges = [
                    {"source": a, "target": b, **graph.edges[a, b]}
                    for a, b in zip(new_path, new_path[1:], strict=False)
                ]
                paths.append(
                    {
                        "nodes": [{"id": n, **graph.nodes[n]} for n in new_path],
                        "edges": edges,
                        "doc_ids": list(dict.fromkeys(e["doc_id"] for e in edges)),
                        "hops": len(edges),
                    }
                )
    return {
        "seed_doc_ids": seed_doc_ids,
        "expanded_doc_ids": found,
        "paths": paths,
        "visited_nodes": len(visited),
        "truncated": truncated,
        "version": VERSION,
    }


class GraphSearch:
    """Reuse core hybrid search and the core candidate reranking adapter."""

    def __init__(self, services: Any):
        self.services = services
        self.graph: nx.DiGraph | None = None
        self.fingerprint: str | None = None

    def get_graph(self) -> nx.DiGraph:
        docs = self.services.repository.list_documents()
        fingerprint = hashlib.sha256(repr(docs).encode()).hexdigest()
        if self.graph is None or self.fingerprint != fingerprint:
            self.graph = build_graph(docs)
            self.fingerprint = fingerprint
        return self.graph

    def __call__(self, request: Any, *, request_id: str | None = None) -> Any:
        start = time.perf_counter()
        seeds = self.services.search.search(
            request.model_copy(update={"mode": "hybrid", "top_k": 5}), request_id=request_id
        )
        expansion = expand(
            self.get_graph(),
            [r.doc_id for r in seeds.results],
            request.query,
            card_tier=request.card_tier,
        )
        doc_ids = set(expansion["expanded_doc_ids"])
        chunks = [
            c
            for c in self.services.repository.list_chunks(seeds.build_id)
            if c["doc_id"] in doc_ids
            and (not request.card_tier or c["card_tier"] == request.card_tier)
        ]
        # Protect one representative per ranked seed before allocating graph slots.
        # Multiple chunks of a seed cannot consume all candidate slots.
        by_chunk = {c["chunk_id"]: c for c in chunks}
        representatives = [
            by_chunk[r.evidence[0].chunk_id]
            for r in seeds.results
            if r.evidence and r.evidence[0].chunk_id in by_chunk
        ]
        seed_chunks = [by_chunk[t.chunk_id] for t in seeds.trace.fusion if t.chunk_id in by_chunk]
        representative_ids = {c["chunk_id"] for c in representatives}
        seed_doc_ids = {r.doc_id for r in seeds.results}
        expanded = [c for c in chunks if c["doc_id"] not in seed_doc_ids]
        first_per_doc = list({c["doc_id"]: c for c in reversed(expanded)}.values())[::-1]
        first_ids = {c["chunk_id"] for c in first_per_doc}
        tail = [c for c in seed_chunks if c["chunk_id"] not in representative_ids]
        tail += [c for c in expanded if c["chunk_id"] not in first_ids]
        depth = self.services.settings.rerank_depth
        chunks = (representatives + first_per_doc + tail)[:depth]
        result = self.services.search.rerank_candidates(
            request, chunks, base_response=seeds, request_id=request_id
        )
        result.mode = "graph_enhanced"
        actual_docs = {c["doc_id"] for c in chunks}
        result.graph_paths = [
            p for p in expansion["paths"] if p["nodes"][-1].get("doc_id") in actual_docs
        ]
        result.graph_expansion = {
            "seed_doc_ids": expansion["seed_doc_ids"],
            "expanded_doc_ids": [d for d in expansion["expanded_doc_ids"] if d not in seed_doc_ids],
            "candidate_doc_ids": list(dict.fromkeys(c["doc_id"] for c in chunks)),
            "truncated": expansion["truncated"]
            or len(representatives) + len(first_per_doc) + len(tail) > depth,
            "visited_nodes": expansion["visited_nodes"],
            "max_hops": 2,
            "version": VERSION,
        }
        result.timings_ms.retrieval_total = (time.perf_counter() - start) * 1000
        if expansion["truncated"] or len(representatives) + len(first_per_doc) + len(tail) > depth:
            result.warnings.append("Graph traversal reached a configured bound.")
        return result


def audit(graph: nx.DiGraph, documents: list[dict[str, Any]]) -> dict[str, Any]:
    """Validate literal provenance for every factual edge."""
    sources = {d["doc_id"]: d["content"] for d in documents}
    failures = []
    for left, right, edge in graph.edges(data=True):
        if edge.get("doc_id") not in sources or edge.get("evidence", "") not in sources.get(
            edge.get("doc_id"), ""
        ):
            failures.append([left, right])
        if not all(edge.get(k) for k in ("doc_id", "evidence", "extraction_method", "version")):
            failures.append([left, right])
    return {
        "version": VERSION,
        "documents": len(documents),
        "covered_documents": len([n for n, a in graph.nodes(data=True) if a["type"] == "Policy"]),
        "documents_with_extracted_fact_sentences": len(
            {a["doc_id"] for _, a in graph.nodes(data=True) if a["type"] in {"Benefit", "FeeRule"}}
        ),
        "nodes": graph.number_of_nodes(),
        "edges": graph.number_of_edges(),
        "node_types": {
            kind: sum(a["type"] == kind for _, a in graph.nodes(data=True))
            for kind in sorted({a["type"] for _, a in graph.nodes(data=True)})
        },
        "invalid_edges": failures,
        "passed": not failures,
    }
