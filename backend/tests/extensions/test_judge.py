"""Judge rejects invented references, handles N/A, enforces budgets and isolates controls."""

import json
from types import SimpleNamespace

import pytest

from apex_rag.evaluation.judge.models import validate_verdict
from apex_rag.evaluation.judge.runner import Budget, judge_sample, summarize


def sample():
    return {
        "sample_id": "x:dense",
        "mode": "dense",
        "question": "How long?",
        "answer": "It lasts 15 cycles.",
        "citations": [],
        "evidence": [
            {
                "evidence_id": "a",
                "doc_id": "DOC-1",
                "chunk_id": "c",
                "text": "The offer lasts 15 cycles.",
            }
        ],
    }


def verdict():
    return {
        "context_relevance": [{"evidence_id": "a", "score": 2, "reason": "Direct answer"}],
        "claims": [
            {
                "claim": "15 cycles",
                "answer_quote": "15 cycles",
                "verdict": "supported",
                "evidence_ids": ["a"],
                "support_quote": "15 cycles",
                "reason": "Exact term",
            }
        ],
        "answers_question": "yes",
        "answers_question_reason": "Gives the duration",
        "refusal": False,
        "no_claims_reason": None,
    }


def budget(tmp_path, tasks=3):
    return Budget(
        tmp_path,
        {
            "tariffs_verified": True,
            "tariffs": {"test": {"input_per_million": 1, "output_per_million": 1}},
            "max_cost": 2,
            "max_tasks": tasks,
            "cost_ceiling_per_task": {"judge": 0.5},
        },
    )


def test_fabricated_references_and_quotes_rejected():
    value = verdict()
    validate_verdict(json.dumps(value), sample())
    value["claims"][0]["support_quote"] = "99 cycles"
    with pytest.raises(ValueError):
        validate_verdict(json.dumps(value), sample())
    value = verdict()
    value["context_relevance"] = []
    with pytest.raises(ValueError):
        validate_verdict(json.dumps(value), sample())


def test_no_claims_is_na_and_refusal_not_perfect(tmp_path):
    value = verdict()
    value.update(claims=[], no_claims_reason="No factual claims", refusal=True)
    llm = SimpleNamespace(model="test", complete=lambda *a, **k: json.dumps(value))
    result = judge_sample(sample(), llm, tmp_path, budget(tmp_path))
    assert result["faithfulness"] is None
    metrics = summarize([result], expected_per_mode=1)["dense"]
    assert metrics["faithfulness"]["na"] == 1 and metrics["refusal_rate"] == 1


def test_retry_raw_output_and_budget_bound(tmp_path):
    calls = []

    def complete(*a, **k):
        calls.append(1)
        return "{bad"

    result = judge_sample(
        sample(),
        SimpleNamespace(model="test", complete=complete),
        tmp_path,
        budget(tmp_path),
        max_attempts=2,
    )
    assert result["status"] == "failed" and len(calls) == 2
    assert len((tmp_path / "raw-judge.jsonl").read_text().splitlines()) == 2
    bounded = budget(tmp_path, tasks=1)
    bounded.reserve("judge", "a")
    with pytest.raises(RuntimeError):
        bounded.reserve("judge", "b")


def test_controlled_excluded(tmp_path):
    result = judge_sample(
        sample(),
        SimpleNamespace(model="test", complete=lambda *a, **k: json.dumps(verdict())),
        tmp_path,
        budget(tmp_path),
    )
    result["controlled"] = True
    assert summarize([result])["dense"]["completed"] == 0


def test_tariffs_must_be_verified(tmp_path):
    with pytest.raises(ValueError):
        Budget(tmp_path, {"max_cost": 1, "max_tasks": 1})


def test_document_relevance_counts_doc_once_and_citations_keep_chunks():
    from apex_rag.evaluation.judge.runner import citation_validity, make_sample

    response = {
        "answer": "It lasts 15 cycles.",
        "citations": [{"doc_id": "a", "chunk_id": "a1", "quote": "15 cycles"}],
        "retrieval": {
            "build_id": "test",
            "results": [
                {
                    "doc_id": "a",
                    "title": "A",
                    "card_tier": "P",
                    "effective_year": 2024,
                    "evidence": [
                        {"chunk_id": "a1", "text": "15 cycles"},
                        {"chunk_id": "a2", "text": "A fee applies"},
                    ],
                }
            ],
        },
    }
    result = make_sample(
        {"query_id": "q", "query": "How long?"},
        "dense",
        SimpleNamespace(model_dump=lambda **k: response),
        "test",
    )
    assert len(result["evidence"]) == 1 and len(result["evidence"][0]["chunks"]) == 2
    assert citation_validity(result)["valid"] == 1


def test_usage_estimate_is_separate_from_bill(tmp_path):
    from apex_rag.evaluation.judge.runner import capture_usage

    services = SimpleNamespace(
        http=SimpleNamespace(
            calls=[{"model": "test", "usage": {"prompt_tokens": 1000, "completion_tokens": 100}}]
        )
    )
    capture_usage(services, tmp_path, "s", 0, budget(tmp_path))
    item = json.loads((tmp_path / "provider-usage.jsonl").read_text())
    assert item["estimated_cost_from_usage"] == pytest.approx(0.0011)
    assert item["actual_billed_cost"] is None


def test_pending_human_cannot_finalize(tmp_path):
    from apex_rag.evaluation.judge.runner import finalize_review

    (tmp_path / "human-review.json").write_text("[]")
    (tmp_path / "samples.jsonl").write_text("")
    (tmp_path / "verdicts.jsonl").write_text("")
    with pytest.raises(ValueError):
        finalize_review(tmp_path)


def test_complete_orchestration_is_paired_and_never_fakes_human_review(tmp_path):
    from pathlib import Path

    from apex_rag.evaluation.judge.runner import run_judge

    root = Path(__file__).resolve().parents[3]
    docs = {
        d["doc_id"]: d for d in json.loads((root / "data" / "kb_documents.json").read_text(encoding="utf-8"))
    }
    requests = []

    def answer(request):
        requests.append(request)
        response = {
            "answer": "Insufficient evidence.",
            "citations": [],
            "retrieval": {
                "build_id": "a" * 24,
                "results": [
                    {
                        "doc_id": "DOC-0001",
                        "title": "Fixture",
                        "card_tier": "Apex Platinum",
                        "effective_year": 2024,
                        "evidence": [{"chunk_id": "c", "text": docs["DOC-0001"]["content"]}],
                    }
                ],
            },
        }
        return SimpleNamespace(model_dump=lambda **k: response)

    def complete(messages, **kwargs):
        payload = json.loads(messages[1]["content"])
        assert "gold_response" not in payload and messages[0]["role"] == "system"
        return json.dumps(
            {
                "context_relevance": [
                    {"evidence_id": e["evidence_id"], "score": 0, "reason": "Test-only fixture"}
                    for e in payload["evidence"]
                ],
                "claims": [],
                "answers_question": "no",
                "answers_question_reason": "Refusal test fixture",
                "refusal": True,
                "no_claims_reason": "Test-only no-claims verdict",
            }
        )

    settings = SimpleNamespace(model_region="beijing", rerank_depth=20, api_max_retries=2)
    service = SimpleNamespace(
        settings=settings,
        answer=SimpleNamespace(answer=answer),
        embedding=SimpleNamespace(model="embedding-fixture"),
        reranker=SimpleNamespace(model="rerank-fixture"),
        llm=SimpleNamespace(model="llm-fixture", complete=complete),
        http=SimpleNamespace(calls=[]),
        repository=SimpleNamespace(
            get_build=lambda: {"build_id": "a" * 24},
            get_document=lambda i: docs[i],
            list_chunks=lambda *a: [{"text": "fixture", "context_text": "fixture"}],
        ),
    )
    config = {
        "queries": str(root / "data" / "eval_queries.json"),
        "max_attempts": 2,
        "budget": {
            "tariffs_verified": True,
            "region": "beijing",
            "max_cost": 100,
            "max_tasks": 186,
            "tariffs": {
                model: {"input_per_million": 1, "output_per_million": 1}
                for model in ("embedding-fixture", "rerank-fixture", "llm-fixture")
            },
            "cost_ceiling_per_task": {
                "answer_dense": 0.5,
                "answer_hybrid_rerank": 0.5,
                "judge": 0.5,
            },
        },
    }
    service.answer.llm = service.llm
    report = run_judge(service, config, tmp_path / "run")
    assert len(requests) == 60 and all(r.top_k == 5 for r in requests)
    assert (
        sum(r.mode == "dense" for r in requests) == 30
        and sum(r.mode == "hybrid_rerank" for r in requests) == 30
    )
    assert report["status"] == "completed_pending_human" and report["human_review_completed"] == 0
    assert len(report["human_review"]) == 10 and len(report["controlled_diagnostics"]) == 3
    assert all(report["metrics"][m]["faithfulness"]["mean"] is None for m in report["metrics"])


def test_resume_budget_is_cumulative_and_third_judge_attempt_is_bounded(tmp_path):
    first = budget(tmp_path)
    first.reserve("judge", "x:dense")
    resumed = Budget(tmp_path, first.config, resume=True)
    assert resumed.calls == 1 and resumed.reserved == 0.5
    resumed.reserve("judge", "x:dense")
    seen = []

    def complete(messages, **kwargs):
        seen.append(json.loads(messages[1]["content"])["validation_feedback"])
        return json.dumps(verdict())

    result = judge_sample(
        sample(),
        SimpleNamespace(model="test", complete=complete),
        tmp_path,
        resumed,
        max_attempts=1,
        attempt_offset=2,
        repair_feedback="Exact quotes required",
    )
    assert result["status"] == "completed" and seen == ["Exact quotes required"]
    raw = json.loads((tmp_path / "raw-judge.jsonl").read_text().splitlines()[-1])
    assert raw["attempt"] == 3
    with pytest.raises(ValueError):
        judge_sample(sample(), None, tmp_path, resumed, max_attempts=1, attempt_offset=3)
    with pytest.raises(RuntimeError):
        resumed.reserve("judge", "extra")


def test_generation_recorder_delegates_and_preserves_exact_prompt(tmp_path):
    from apex_rag.evaluation.judge.resume import GenerationRecorder

    messages = [{"role": "system", "content": "changed"}, {"role": "user", "content": "data"}]
    seen = []
    provider = SimpleNamespace(
        model="test", complete=lambda m, **k: seen.append(m) or '{"answer":"raw"}'
    )
    recorder = GenerationRecorder(provider, tmp_path, "Q-001:dense", "original")
    assert recorder.complete(messages, json_mode=True) == '{"answer":"raw"}'
    assert seen[0][0]["content"] == "original" and messages[0]["content"] == "changed"
    assert json.loads((tmp_path / "raw-generation.jsonl").read_text())["sample_id"] == "Q-001:dense"


def test_resume_repairs_only_missing_task_and_second_execution_is_free(tmp_path):
    import hashlib
    from pathlib import Path

    from apex_rag.evaluation.judge.resume import resume_failed
    from apex_rag.evaluation.judge.runner import append_json

    queries = [{"query_id": f"Q-{i:03d}", "query": "How long?"} for i in range(1, 31)]
    query_path = tmp_path / "queries.json"
    query_path.write_text(json.dumps(queries), encoding="utf-8")
    prompt_path = tmp_path / "generation_prompt.txt"
    prompt_path.write_text("Original prompt", encoding="utf-8")
    config = {
        "queries": str(query_path),
        "max_attempts": 2,
        "budget": {
            "tariffs_verified": True,
            "tariffs": {"test": {"input_per_million": 1, "output_per_million": 1}},
            "max_tasks": 20,
            "max_cost": 10,
            "region": "beijing",
            "cost_ceiling_per_task": {
                "judge": 0.5,
                "answer_dense": 0.5,
                "answer_hybrid_rerank": 0.5,
            },
        },
    }
    captured = {
        "config": config,
        "model": "test",
        "build_id": "a" * 24,
        "bias": "Test only",
        "generation_prompt_sha256": hashlib.sha256(prompt_path.read_bytes()).hexdigest(),
        "query_sha256": hashlib.sha256(query_path.read_bytes()).hexdigest(),
    }
    (tmp_path / "config.json").write_text(json.dumps(captured), encoding="utf-8")
    for query in queries:
        for mode in ("dense", "hybrid_rerank"):
            if query["query_id"] == "Q-001" and mode == "hybrid_rerank":
                continue
            item = {**sample(), "sample_id": query["query_id"] + ":" + mode, "mode": mode}
            item["evidence"][0].update(title="Fixture", card_tier="Test")
            append_json(tmp_path / "samples.jsonl", item)
            append_json(
                tmp_path / "verdicts.jsonl",
                {
                    "sample_id": item["sample_id"],
                    "mode": mode,
                    "status": "completed",
                    "context_relevance": 1,
                    "faithfulness": 1,
                    "verdict": verdict(),
                    "citation_validity": {"count": 0, "valid": 0},
                },
            )
    (tmp_path / "controlled-verdicts.json").write_text(
        json.dumps([{"sample_id": f"CONTROLLED:{i}", "status": "completed"} for i in range(3)]),
        encoding="utf-8",
    )
    Budget(tmp_path, config["budget"]).reserve("answer_hybrid_rerank", "Q-001:hybrid_rerank")
    calls = []

    def answer(request):
        calls.append("answer")
        response = {
            "answer": sample()["answer"],
            "citations": [],
            "retrieval": {
                "build_id": "a" * 24,
                "results": [
                    {
                        "doc_id": "DOC-1",
                        "title": "Fixture",
                        "card_tier": "Test",
                        "effective_year": 2026,
                        "evidence": [{"chunk_id": "c", "text": "The offer lasts 15 cycles."}],
                    }
                ],
            },
        }
        return SimpleNamespace(model_dump=lambda **k: response)

    def complete(messages, **kwargs):
        calls.append("judge")
        value = verdict()
        value["context_relevance"][0]["evidence_id"] = "DOC-1"
        value["claims"][0]["evidence_ids"] = ["DOC-1"]
        return json.dumps(value)

    llm = SimpleNamespace(model="test", complete=complete)
    services = SimpleNamespace(
        llm=llm,
        answer=SimpleNamespace(llm=llm, answer=answer),
        http=SimpleNamespace(calls=[]),
        settings=SimpleNamespace(
            path=Path, model_region="beijing", rerank_depth=20, api_max_retries=2
        ),
        embedding=llm,
        reranker=llm,
        repository=SimpleNamespace(
            get_build=lambda *a: {"build_id": "a" * 24},
            validate_ready=lambda b: b,
            list_chunks=lambda *a: [{"text": "fixture"}],
        ),
    )
    report = resume_failed(services, tmp_path, prompt_path)
    assert report["status"] == "completed_pending_human" and report["raw_samples"] == 60
    assert calls == ["answer", "judge"]
    again = resume_failed(services, tmp_path, prompt_path)
    assert again["raw_samples"] == 60 and calls == ["answer", "judge"]
    assert json.loads((tmp_path / "budget.json").read_text())["tasks"] == 3
