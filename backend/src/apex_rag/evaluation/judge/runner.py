"""Save paired samples before independent judgment, audit failures, enforce finite budgets."""

from __future__ import annotations

import hashlib
import json
import math
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import validate_verdict
from .prompts import SYSTEM, VERSION


def append_json(path: Path, data: dict[str, Any]) -> None:
    """Append one durable task record, without credentials or provider requests headers."""
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(data, ensure_ascii=False) + "\n")
        stream.flush()


def capture_usage(
    services: Any, directory: Path, sample_id: str, start: int, budget: Any = None
) -> None:
    """Persist the core Provider's real token usage and retry count when available."""
    calls = getattr(getattr(services, "http", None), "calls", [])
    for call in calls[start:]:
        tariffs = budget.config.get("tariffs", {}) if budget else {}
        pricing = tariffs.get(call.get("model"), {})
        usage = call.get("usage") or {}
        input_tokens = usage.get(
            "prompt_tokens", usage.get("input_tokens", usage.get("total_tokens"))
        )
        output_tokens = usage.get("completion_tokens", usage.get("output_tokens"))
        estimated_cost = None
        if pricing and isinstance(input_tokens, int | float):
            estimated_cost = input_tokens * pricing["input_per_million"] / 1_000_000
            if isinstance(output_tokens, int | float):
                estimated_cost += output_tokens * pricing.get("output_per_million", 0) / 1_000_000
        append_json(
            directory / "provider-usage.jsonl",
            {
                "sample_id": sample_id,
                **call,
                "actual_billed_cost": None,
                "estimated_cost_from_usage": estimated_cost,
                "cost_note": "Uses configured verified tariff; actual bill unavailable. Usage may omit failed retry attempts.",
            },
        )


def citation_validity(sample: dict[str, Any]) -> dict[str, Any]:
    evidence = {}
    for entry in sample["evidence"]:
        for chunk in entry.get("chunks", [entry]):
            evidence[(entry["doc_id"], chunk["chunk_id"])] = chunk["text"]
    citations = sample.get("citations", [])
    checks = [
        c.get("quote", "")
        and c.get("quote", "") in evidence.get((c.get("doc_id"), c.get("chunk_id")), "")
        for c in citations
    ]
    return {
        "count": len(citations),
        "valid": sum(bool(c) for c in checks),
        "rate": sum(bool(c) for c in checks) / len(checks) if checks else None,
        "meaning": "Identifier and exact-quote validity; not entailment.",
    }


def usage_summary(directory: Path) -> dict[str, Any]:
    """Never treat unknown failed-attempt usage as a free call."""
    path = directory / "provider-usage.jsonl"
    calls = (
        [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        if path.exists()
        else []
    )
    known = [
        c["estimated_cost_from_usage"]
        for c in calls
        if c.get("estimated_cost_from_usage") is not None
    ]
    return {
        "provider_logical_calls": len(calls),
        "http_attempts": sum(c.get("attempts", 0) for c in calls),
        "estimated_cost_known_usage": sum(known) if known else None,
        "unknown_cost_call_count": len(calls) - len(known),
        "actual_billed_cost": None,
        "note": "Known usage estimate excludes unknown failed attempts; reservation cap is separate.",
    }


def validate_paid_bounds(
    services: Any, config: dict[str, Any], queries: list[dict], build_id: str
) -> None:
    """Check the known corpus fits tariff and conservative reservation assumptions before calls."""

    from apex_rag.generation.service import SYSTEM_PROMPT

    budget = config["budget"]
    if services.settings.model_region != budget.get("region"):
        raise ValueError("Configured tariff region differs from the actual provider region.")
    models = (services.llm.model, services.embedding.model, services.reranker.model)
    if not set(models) <= set(budget.get("tariffs", {})):
        raise ValueError("Verified tariff is required for each actual model.")
    for price in budget["tariffs"].values():
        if any(
            not isinstance(v, int | float) or not math.isfinite(v) or v < 0 for v in price.values()
        ):
            raise ValueError("Tariffs must be finite nonnegative values.")
    chunks = services.repository.list_chunks(build_id)
    depth = services.settings.rerank_depth
    largest = sorted(
        (len(c["text"].encode()) + len(c.get("context_text", c["text"]).encode()) for c in chunks),
        reverse=True,
    )[:depth]
    max_query = max(len(q.get("query", "").encode()) for q in queries)
    # Count UTF-8 bytes conservatively as tokens; JSON/system overhead has a generous allowance.
    if sum(largest) + max_query + len(SYSTEM_PROMPT.encode()) + 10000 > 70000 or max_query > 4000:
        raise ValueError("Corpus/query exceeds the verified per-task token cost bound.")
    if services.settings.api_max_retries > 3:
        raise ValueError("Reservation assumes at most four provider attempts.")


class Budget:
    """Reserve conservative per-operation cost ceilings before calls, persist on every step.

    Prices are operator-supplied currency units. No API usage is invented: actual billed
    cost stays null unless the core provider supplies it. A zero/unknown ceiling disallows
    paid execution. Retries inside the core Provider must fit its configured attempt cap.
    """

    def __init__(self, directory: Path, config: dict[str, Any], *, resume: bool = False):
        self.path = directory / "budget.json"
        self.config = config
        self.records: list[dict[str, Any]] = []
        self.reserved = 0.0
        self.calls = 0
        if (
            type(config.get("max_tasks")) is not int
            or not 1 <= config["max_tasks"] <= 1000
            or not isinstance(config.get("max_cost"), int | float)
            or not math.isfinite(config["max_cost"])
        ):
            raise ValueError("Budget needs a finite cost limit and 1..1000 integer tasks.")
        if config.get("max_cost", 0) <= 0 or config.get("max_tasks", 0) <= 0:
            raise ValueError("Explicit positive cost and task budgets are required.")
        if not config.get("tariffs_verified"):
            raise ValueError(
                "Verify provider tariffs and per-task conservative ceilings before paid execution."
            )
        if not config.get("tariffs"):
            raise ValueError("Provide verified per-model tariffs; do not invent billed costs.")

        if resume and self.path.exists():
            previous = json.loads(self.path.read_text(encoding="utf-8"))
            self.records = previous["records"]
            self.calls = previous["tasks"]
            self.reserved = previous["reserved_cost_ceiling"]
            if (
                self.calls != len(self.records)
                or self.calls > config["max_tasks"]
                or self.reserved > config["max_cost"]
                or abs(sum(r["reserved_cost_ceiling"] for r in self.records) - self.reserved) > 1e-8
            ):
                raise ValueError("Existing cumulative budget is inconsistent or exceeds limits.")

    def reserve(self, kind: str, sample_id: str) -> None:
        ceiling = float(self.config.get("cost_ceiling_per_task", {}).get(kind, 0))
        if not math.isfinite(ceiling) or ceiling <= 0:
            raise ValueError(f"Missing conservative cost ceiling for {kind}.")
        if (
            self.calls + 1 > self.config["max_tasks"]
            or self.reserved + ceiling > self.config["max_cost"]
        ):
            raise RuntimeError("Task/cost budget exhausted before making the next call.")
        self.calls += 1
        self.reserved += ceiling
        self.records.append(
            {
                "kind": kind,
                "sample_id": sample_id,
                "reserved_cost_ceiling": ceiling,
                "actual_billed_cost": None,
                "at": datetime.now(UTC).isoformat(),
            }
        )
        self.path.write_text(
            json.dumps(
                {
                    "currency": self.config.get("currency", "CNY"),
                    "reserved_cost_ceiling": self.reserved,
                    "actual_billed_cost": None,
                    "tasks": self.calls,
                    "max_cost": self.config["max_cost"],
                    "records": self.records,
                    "note": "Ceilings include embedding/rerank/generation and bounded provider retries; not a bill.",
                },
                indent=2,
            ),
            encoding="utf-8",
        )


def make_sample(query: dict, mode: str, answer: Any, model: str) -> dict[str, Any]:
    response = answer.model_dump(mode="json")
    evidence = []
    for result in response["retrieval"]["results"]:
        evidence.append(
            {
                "evidence_id": result["doc_id"],
                "doc_id": result["doc_id"],
                "chunk_ids": [e["chunk_id"] for e in result["evidence"]],
                "chunks": [
                    {"chunk_id": e["chunk_id"], "text": e["text"]} for e in result["evidence"]
                ],
                "title": result["title"],
                "card_tier": result["card_tier"],
                "effective_year": result["effective_year"],
                "text": "\n".join(e["text"] for e in result["evidence"]),
            }
        )
    # Do not pass gold_response or relevance labels to generation/Judge.
    return {
        "sample_id": f"{query['query_id']}:{mode}",
        "query_id": query["query_id"],
        "question": query["query"],
        "mode": mode,
        "build_id": response["retrieval"]["build_id"],
        "model": model,
        "prompt_version": VERSION,
        "answer": response["answer"],
        "citations": response["citations"],
        "evidence": evidence,
        "response": response,
        "created_at": datetime.now(UTC).isoformat(),
    }


def judge_sample(
    sample: dict[str, Any],
    llm: Any,
    directory: Path,
    budget: Budget,
    *,
    max_attempts: int = 2,
    max_payload_bytes: int = 70000,
    attempt_offset: int = 0,
    repair_feedback: str | None = None,
) -> dict[str, Any]:
    """Separate Qwen calls with strict parsing; preserve every raw response and failed task."""
    if (
        not 1 <= max_attempts <= 3
        or not 0 <= attempt_offset < 3
        or max_attempts + attempt_offset > 3
    ):
        raise ValueError("Judge attempts must be 1..3.")
    payload = {k: sample[k] for k in ("question", "answer", "evidence", "citations")}
    if repair_feedback:
        payload["validation_feedback"] = repair_feedback
    serialized = json.dumps(payload, ensure_ascii=False)
    if len(serialized.encode()) > max_payload_bytes:
        raise ValueError(
            "Judge payload exceeds budget; sample is unscored, never silently truncated."
        )
    start = time.perf_counter()
    for attempt in range(1, max_attempts + 1):
        budget.reserve("judge", sample["sample_id"])
        try:
            raw = llm.complete(
                [{"role": "system", "content": SYSTEM}, {"role": "user", "content": serialized}],
                json_mode=True,
            )
            append_json(
                directory / "raw-judge.jsonl",
                {
                    "sample_id": sample["sample_id"],
                    "attempt": attempt + attempt_offset,
                    "raw": raw,
                    "model": llm.model,
                    "prompt_version": VERSION,
                },
            )
            result = validate_verdict(raw, sample)
            claims = result.claims
            return {
                "sample_id": sample["sample_id"],
                "mode": sample["mode"],
                "status": "completed",
                "controlled": sample.get("controlled", False),
                "verdict": result.model_dump(),
                "context_relevance": sum(v.score for v in result.context_relevance)
                / (2 * len(result.context_relevance))
                if result.context_relevance
                else None,
                "faithfulness": sum(c.verdict == "supported" for c in claims) / len(claims)
                if claims
                else None,
                "claim_count": len(claims),
                "citation_validity": citation_validity(sample),
                "attempts": attempt + attempt_offset,
                "latency_ms": (time.perf_counter() - start) * 1000,
            }
        except Exception as exc:
            # Never log exception str: upstream errors may contain request or secret material.
            append_json(
                directory / "errors.jsonl",
                {
                    "sample_id": sample["sample_id"],
                    "attempt": attempt + attempt_offset,
                    "stage": "judge",
                    "error_type": type(exc).__name__,
                    "error_code": getattr(exc, "code", None),
                },
            )
            if attempt == max_attempts:
                return {
                    "sample_id": sample["sample_id"],
                    "mode": sample["mode"],
                    "status": "failed",
                    "controlled": sample.get("controlled", False),
                    "error_type": type(exc).__name__,
                    "error_code": getattr(exc, "code", None),
                    "context_relevance": None,
                    "faithfulness": None,
                }
    raise AssertionError("Unreachable bounded retry state.")


def summarize(results: list[dict[str, Any]], expected_per_mode: int = 30) -> dict[str, Any]:
    """Report failures and N/A denominators; controlled contradictions never enter means."""
    output = {}
    for mode in ("dense", "hybrid_rerank"):
        formal = [r for r in results if r["mode"] == mode and not r.get("controlled")]
        done = [r for r in formal if r["status"] == "completed"]
        metrics = {}
        for name in ("context_relevance", "faithfulness"):
            values = [r[name] for r in done if r.get(name) is not None]
            metrics[name] = {
                "mean": sum(values) / len(values) if values else None,
                "scored": len(values),
                "na": len(done) - len(values),
            }
        metrics.update(
            {
                "expected": expected_per_mode,
                "attempted": len(formal),
                "completed": len(done),
                "failed_or_missing": expected_per_mode - len(done),
                "refusal_rate": sum(r["verdict"]["refusal"] for r in done) / len(done)
                if done
                else None,
                "answers_question": {
                    k: sum(r["verdict"]["answers_question"] == k for r in done)
                    for k in ("yes", "partial", "no")
                },
                "citation_count": sum(r["citation_validity"]["count"] for r in done),
                "valid_citation_count": sum(r["citation_validity"]["valid"] for r in done),
            }
        )
        output[mode] = metrics
    return output


def run_judge(services: Any, config: dict[str, Any], directory: Path) -> dict[str, Any]:
    """Generate both modes for all 30 queries, then judge saved samples with same model."""
    from apex_rag.generation.service import SYSTEM_PROMPT
    from apex_rag.schemas import SearchRequest

    directory.mkdir(parents=True, exist_ok=False)
    budget = Budget(directory, config["budget"])
    query_path = Path(config.get("queries", "data/eval_queries.json"))
    queries = json.loads(query_path.read_text(encoding="utf-8"))
    if len(queries) != 30 or len({q["query_id"] for q in queries}) != 30:
        raise ValueError("Formal Judge requires the same complete 30-query set for both modes.")
    build = services.repository.get_build()
    validate_paid_bounds(services, config, queries, build["build_id"])
    captured = {
        "prompt_version": VERSION,
        "prompt_sha256": hashlib.sha256(SYSTEM.encode()).hexdigest(),
        "query_sha256": hashlib.sha256(query_path.read_bytes()).hexdigest(),
        "model": services.llm.model,
        "config": config,
        "build_id": build["build_id"],
        "generation_prompt_sha256": hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest(),
        "generation_parameters": {"temperature": 0, "max_tokens": 2400, "top_k_documents": 5},
        "bias": "Same Qwen model generates and independently judges; correlated self-evaluation bias. Not ground truth.",
    }
    (directory / "config.json").write_text(
        json.dumps(captured, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (directory / "generation_prompt.txt").write_text(SYSTEM_PROMPT, encoding="utf-8")
    (directory / "judge_prompt.txt").write_text(SYSTEM, encoding="utf-8")
    from .resume import GenerationRecorder

    results = []
    for query in queries:
        for mode in ("dense", "hybrid_rerank"):
            sample_id = f"{query['query_id']}:{mode}"
            usage_start = len(getattr(getattr(services, "http", None), "calls", []))
            original = services.answer.llm
            try:
                budget.reserve("answer_" + mode, sample_id)
                services.answer.llm = GenerationRecorder(
                    original, directory, sample_id, SYSTEM_PROMPT
                )
                answer = services.answer.answer(
                    SearchRequest(
                        query=query["query"], mode=mode, top_k=5, build_id=build["build_id"]
                    )
                )
                sample = make_sample(query, mode, answer, services.llm.model)
                append_json(directory / "samples.jsonl", sample)
            except Exception as exc:
                failure = {
                    "sample_id": sample_id,
                    "mode": mode,
                    "status": "failed",
                    "stage": "generation",
                    "error_type": type(exc).__name__,
                    "error_code": getattr(exc, "code", None),
                }
                append_json(directory / "errors.jsonl", failure)
                results.append(failure)
                if isinstance(exc, RuntimeError) and "budget" in str(exc).lower():
                    break
            finally:
                services.answer.llm = original
                capture_usage(services, directory, sample_id, usage_start, budget)
    samples = (
        [
            json.loads(line)
            for line in (directory / "samples.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        if (directory / "samples.jsonl").exists()
        else []
    )
    for sample in samples:
        usage_start = len(getattr(getattr(services, "http", None), "calls", []))
        try:
            result = judge_sample(
                sample, services.llm, directory, budget, max_attempts=config.get("max_attempts", 2)
            )
        except Exception as exc:
            result = {
                "sample_id": sample["sample_id"],
                "mode": sample["mode"],
                "status": "failed",
                "stage": "judge",
                "error_type": type(exc).__name__,
                "error_code": getattr(exc, "code", None),
            }
        append_json(directory / "verdicts.jsonl", result)
        capture_usage(services, directory, sample["sample_id"], usage_start, budget)
        results.append(result)
    controlled = []
    # Inject an explicit opposite numerical assertion against a verbatim source statement.
    # These diagnostics are excluded from summarize() and formal task counts.
    controls = [
        (
            "DOC-0001",
            "How many introductory billing cycles does Apex Platinum balance transfer provide?",
            "Apex Platinum provides 99 introductory billing cycles.",
        ),
        (
            "DOC-0046",
            "What is the charge for additional lounge guests on Apex Platinum?",
            "Additional lounge guests on Apex Platinum are charged $999 per visit.",
        ),
        (
            "DOC-0054",
            "Does Apex Platinum cell phone protection cover lost phones?",
            "Apex Platinum cell phone protection covers lost phones.",
        ),
    ]
    for doc_id, question, answer in controls:
        source = services.repository.get_document(doc_id)
        altered = {
            "sample_id": "CONTROLLED:" + doc_id,
            "controlled": True,
            "mode": "controlled",
            "question": question,
            "answer": answer,
            "citations": [],
            "evidence": [
                {
                    "evidence_id": doc_id + ":source",
                    "doc_id": doc_id,
                    "chunk_id": "source-document",
                    "text": source["content"],
                }
            ],
        }
        append_json(directory / "controlled-samples.jsonl", altered)
        usage_start = len(getattr(getattr(services, "http", None), "calls", []))
        try:
            controlled.append(
                judge_sample(
                    altered,
                    services.llm,
                    directory,
                    budget,
                    max_attempts=config.get("max_attempts", 2),
                )
            )
        except Exception as exc:
            controlled.append(
                {
                    "sample_id": altered["sample_id"],
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "error_code": getattr(exc, "code", None),
                }
            )
        capture_usage(services, directory, altered["sample_id"], usage_start, budget)
    _control_path = directory / "controlled-verdicts.json"
    _control_path.write_text(json.dumps(controlled, ensure_ascii=False, indent=2), encoding="utf-8")
    return write_report(directory, samples, results, controlled, budget, captured)


def review_categories(sample):
    """Suggest source-grounded review angles without pretending a risk is an observed error."""
    evidence = sample["evidence"]
    categories = {
        "normal": "Verify concrete fees, deadlines and qualifiers against the original evidence."
    }
    if len(evidence) > 1:
        categories["irrelevant"] = (
            "Judge must inspect each retrieved document; topical similarity alone may not answer this question."
        )
    products = sorted({e.get("card_tier", "") for e in evidence})
    if len(products) > 1:
        categories["product_confusion"] = (
            "Retrieved evidence spans products: "
            + ", ".join(products)
            + "; verify no cross-product transfer."
        )
    if sample.get("response", {}).get("evidence_insufficient") or "无法确认" in sample["answer"]:
        categories["insufficient"] = (
            "Answer explicitly signals uncertainty; verify whether evidence actually requires refusal and whether added claims remain supported."
        )
    years = sorted(
        {e.get("effective_year") for e in evidence if e.get("effective_year") is not None}
    )
    if len(years) > 1:
        categories["year_conflict"] = (
            "Evidence has years "
            + str(years)
            + "; this is a year-difference risk, not proof of conflicting policy. Human must establish actual conflict or mark coverage absent."
        )
    return categories


def write_report(directory, samples, results, controlled, budget, captured):
    """Rebuild review materials from saved tasks without generating or inventing decisions."""
    summary = summarize(results)
    # Ten is the minimum review set, the reviewer must fill identities and decisions.
    # Spread review across question IDs and both systems; human must verify category coverage.
    completed_ids = {r["sample_id"] for r in results if r.get("status") == "completed"}
    preferred = [
        s
        for s in samples
        if s["sample_id"].split(":")[0] in {"Q-001", "Q-007", "Q-015", "Q-022", "Q-029"}
        and s["sample_id"] in completed_ids
    ]
    preferred_ids = {s["sample_id"] for s in preferred}
    chosen = (
        preferred
        + [
            s
            for s in samples
            if s["sample_id"] in completed_ids and s["sample_id"] not in preferred_ids
        ]
    )[:10]
    review = [
        {
            "sample_id": s["sample_id"],
            "reviewer": None,
            "reviewed_at": None,
            "category": None,
            "candidate_categories": review_categories(s),
            "review_reason": "AI source-based candidates only: human must verify actual category; never relabel an inapplicable sample to satisfy coverage. If a category is absent, record the gap rather than claim coverage.",
            "human_context_scores": None,
            "human_claim_verdicts": None,
            "missed_claims": None,
            "agreement": None,
            "notes": None,
            "status": "pending_human",
        }
        for s in chosen
    ]
    (directory / "human-review.json").write_text(
        json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    packet = [
        "# Judge 人工复核材料",
        "",
        "由具名人类逐项核对，填写同目录 human-review.json。这里的待填字段不算人工复核。",
        "至少10个不同正式样本，覆盖 normal/irrelevant/product_confusion/insufficient/year_conflict；",
        "逐证据0/1/2，逐主张supported/unsupported/contradicted，并记录Judge漏掉的主张。",
        "",
    ]
    lookup = {r["sample_id"]: r for r in results if r.get("status") == "completed"}
    for selected in chosen:
        packet.extend(
            [
                "## " + selected["sample_id"],
                "",
                "问题：" + selected["question"],
                "",
                "复核角度候选（不是已确认分类）："
                + json.dumps(review_categories(selected), ensure_ascii=False),
                "",
                "### 回答",
                "",
                selected["answer"],
                "",
                "### 原始证据",
                "",
            ]
        )
        for evidence in selected["evidence"]:
            packet.extend(
                [
                    "- "
                    + evidence["evidence_id"]
                    + " / "
                    + evidence["title"]
                    + " / "
                    + evidence["card_tier"],
                    "",
                    evidence["text"],
                    "",
                ]
            )
        packet.extend(
            [
                "### Judge判断（需人类独立核对）",
                "",
                "```json",
                json.dumps(lookup.get(selected["sample_id"], {}), ensure_ascii=False, indent=2),
                "```",
                "",
            ]
        )
    (directory / "human-review-packet.md").write_text("\n".join(packet), encoding="utf-8")
    status = (
        "completed_pending_human"
        if all(summary[m]["completed"] == 30 for m in summary)
        and len(controlled) == 3
        and all(c.get("status") == "completed" for c in controlled)
        else "incomplete"
    )
    report = {
        "status": status,
        "metrics": summary,
        "human_review_completed": 0,
        "human_review_required": 10,
        "self_evaluation_bias": captured["bias"],
        "raw_samples": len(samples),
        "budget": json.loads(budget.path.read_text()) if budget.path.exists() else None,
    }
    report["controlled_diagnostics"] = controlled
    report["samples"] = samples
    report["verdicts"] = results
    report["human_review"] = review
    report["provider_cost_summary"] = usage_summary(directory)
    error_path = directory / "errors.jsonl"
    errors = (
        [json.loads(line) for line in error_path.read_text(encoding="utf-8").splitlines()]
        if error_path.exists()
        else []
    )
    report["execution_audit"] = {
        "generation_failure_records": sum(e.get("stage") == "generation" for e in errors),
        "judge_validation_failure_records": sum(e.get("stage") == "judge" for e in errors),
        "repair_policy": captured.get("repair_policy"),
        "initial_execution_summary": captured.get("initial_execution_summary"),
        "note": "Metrics use final validated results; all original failures remain in errors/raw/provider logs. Human review remains required.",
    }
    (directory / "summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def finalize_review(directory: Path) -> dict[str, Any]:
    """Validate human-entered reviews, compute disagreements and update the read-only report."""
    reviews = json.loads((directory / "human-review.json").read_text(encoding="utf-8"))
    samples = {
        s["sample_id"]: s
        for s in (
            json.loads(line)
            for line in (directory / "samples.jsonl").read_text(encoding="utf-8").splitlines()
        )
    }
    verdicts = {
        v["sample_id"]: v
        for v in (
            json.loads(line)
            for line in (directory / "verdicts.jsonl").read_text(encoding="utf-8").splitlines()
        )
        if v["status"] == "completed"
    }
    if len(reviews) < 10 or len({r["sample_id"] for r in reviews}) != len(reviews):
        raise ValueError("At least ten distinct human reviews are required.")
    categories = {"normal", "irrelevant", "product_confusion", "insufficient", "year_conflict"}
    if not categories <= {r.get("category") for r in reviews}:
        raise ValueError("Review set must cover all five representative categories.")
    for review in reviews:
        sample_id = review["sample_id"]
        if sample_id not in samples or sample_id not in verdicts:
            raise ValueError("Review must refer to a completed formal sample.")
        if (
            not review.get("reviewer")
            or review.get("status") != "completed"
            or not review.get("notes")
        ):
            raise ValueError("A named human reviewer, notes and completed status are required.")
        moment = datetime.fromisoformat(review["reviewed_at"])
        if moment.tzinfo is None:
            raise ValueError("Human review time must include timezone.")
        human_context = review["human_context_scores"]
        evidence_ids = {e["evidence_id"] for e in samples[sample_id]["evidence"]}
        if (
            not isinstance(human_context, dict)
            or set(human_context) != evidence_ids
            or any(v not in {0, 1, 2} for v in human_context.values())
        ):
            raise ValueError("Human context scores must cover each evidence ID with 0,1,2.")
        human_claims = review["human_claim_verdicts"]
        judged = verdicts[sample_id]["verdict"]
        if (
            not isinstance(human_claims, list)
            or len(human_claims) != len(judged["claims"])
            or any(v not in {"supported", "unsupported", "contradicted"} for v in human_claims)
        ):
            raise ValueError("Human claim verdicts must align with every Judge claim.")
        if "missed_claims" not in review or not isinstance(review["missed_claims"], list):
            raise ValueError("Reviewer must explicitly record any claims omitted by Judge, or [].")
        review["disagreements"] = {
            "context": [
                v["evidence_id"]
                for v in judged["context_relevance"]
                if human_context[v["evidence_id"]] != v["score"]
            ],
            "claims": [
                i
                for i, (h, j) in enumerate(zip(human_claims, judged["claims"], strict=False))
                if h != j["verdict"]
            ],
            "missed_claims": review["missed_claims"],
        }
    report = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
    report["human_review"] = reviews
    report["human_review_completed"] = len(reviews)
    controls = report.get("controlled_diagnostics", [])
    controls_completed = len(controls) == 3 and all(
        c.get("status") == "completed" for c in controls
    )
    report["controlled_contradictions_detected"] = sum(
        any(claim["verdict"] == "contradicted" for claim in c.get("verdict", {}).get("claims", []))
        for c in controls
    )
    if (
        all(report["metrics"][m]["completed"] == 30 for m in ("dense", "hybrid_rerank"))
        and controls_completed
    ):
        report["status"] = "completed"
    (directory / "summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report
