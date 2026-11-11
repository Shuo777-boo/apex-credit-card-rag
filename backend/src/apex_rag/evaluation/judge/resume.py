"""One bounded repair pass over saved failures, never rerun completed paid tasks."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .runner import (
    Budget,
    append_json,
    capture_usage,
    judge_sample,
    make_sample,
    validate_paid_bounds,
    write_report,
)


def records(path: Path) -> list[dict]:
    return (
        [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        if path.exists()
        else []
    )


class GenerationRecorder:
    """Delegate to the sole core Provider, preserving the run's exact system prompt."""

    def __init__(self, provider: Any, directory: Path, sample_id: str, prompt: str):
        self.provider, self.directory, self.sample_id, self.prompt = (
            provider,
            directory,
            sample_id,
            prompt,
        )
        self.model = provider.model

    def complete(self, messages, **kwargs):
        messages = [dict(message) for message in messages]
        if messages[0]["role"] != "system":
            raise ValueError("Expected the core generation system message.")
        messages[0]["content"] = self.prompt
        raw = self.provider.complete(messages, **kwargs)
        append_json(
            self.directory / "raw-generation.jsonl",
            {
                "sample_id": self.sample_id,
                "raw": raw,
                "model": self.model,
                "generation_prompt_sha256": hashlib.sha256(self.prompt.encode()).hexdigest(),
            },
        )
        return raw


def resume_failed(services: Any, directory: Path, prompt_path: Path) -> dict:
    """At most one generation repair and one third validation attempt, within original budget."""
    from apex_rag.schemas import SearchRequest

    captured = json.loads((directory / "config.json").read_text(encoding="utf-8"))
    review_path = directory / "human-review.json"
    if review_path.exists() and any(
        r.get("status") == "completed" for r in json.loads(review_path.read_text(encoding="utf-8"))
    ):
        raise ValueError("Preserve human decisions: repair must precede completed human review.")
    config = captured["config"]
    prompt = prompt_path.read_text(encoding="utf-8")
    if hashlib.sha256(prompt.encode()).hexdigest() != captured["generation_prompt_sha256"]:
        raise ValueError("Repair requires the original exact generation prompt snapshot.")
    if services.llm.model != captured["model"]:
        raise ValueError("Repair model must match the original run.")
    initial = directory / "initial-summary.json"
    summary_path = directory / "summary.json"
    if summary_path.exists() and not initial.exists():
        initial.write_bytes(summary_path.read_bytes())
    if initial.exists():
        before = json.loads(initial.read_text(encoding="utf-8"))
        captured["initial_execution_summary"] = {
            "status": before["status"],
            "metrics": before["metrics"],
            "raw_samples": before["raw_samples"],
            "provider_cost_summary": before.get("provider_cost_summary"),
            "source": "initial-summary.json (preserved before any repair)",
        }
    queries = json.loads(services.settings.path(config["queries"]).read_text(encoding="utf-8"))
    if (
        hashlib.sha256(services.settings.path(config["queries"]).read_bytes()).hexdigest()
        != captured["query_sha256"]
    ):
        raise ValueError("Repair query data changed.")
    services.repository.validate_ready(services.repository.get_build(captured["build_id"]))
    validate_paid_bounds(services, config, queries, captured["build_id"])
    budget = Budget(directory, config["budget"], resume=True)
    samples = {s["sample_id"]: s for s in records(directory / "samples.jsonl")}
    results = {r["sample_id"]: r for r in records(directory / "verdicts.jsonl")}
    for query in queries:
        for mode in ("dense", "hybrid_rerank"):
            sample_id = f"{query['query_id']}:{mode}"
            if sample_id in samples:
                continue
            prior = sum(
                r["kind"] == "answer_" + mode and r["sample_id"] == sample_id
                for r in budget.records
            )
            if prior != 1:
                # No implicit fresh generation and no repeated repair if this command runs twice.
                continue
            start = len(services.http.calls)
            original = services.answer.llm
            try:
                budget.reserve("answer_" + mode, sample_id)
                services.answer.llm = GenerationRecorder(original, directory, sample_id, prompt)
                answer = services.answer.answer(
                    SearchRequest(
                        query=query["query"], mode=mode, top_k=5, build_id=captured["build_id"]
                    )
                )
                samples[sample_id] = make_sample(query, mode, answer, services.llm.model)
                append_json(directory / "samples.jsonl", samples[sample_id])
            except Exception as exc:
                failure = {
                    "sample_id": sample_id,
                    "mode": mode,
                    "status": "failed",
                    "stage": "generation",
                    "attempt": 2,
                    "error_type": type(exc).__name__,
                    "error_code": getattr(exc, "code", None),
                }
                append_json(directory / "errors.jsonl", failure)
                results[sample_id] = failure
            finally:
                services.answer.llm = original
                capture_usage(services, directory, sample_id, start, budget)
    for sample_id, sample in samples.items():
        if results.get(sample_id, {}).get("status") == "completed":
            continue
        prior = sum(r["kind"] == "judge" and r["sample_id"] == sample_id for r in budget.records)
        if prior >= 3:
            continue
        start = len(services.http.calls)
        try:
            result = judge_sample(
                sample,
                services.llm,
                directory,
                budget,
                max_attempts=1 if prior else config.get("max_attempts", 2),
                attempt_offset=prior,
                repair_feedback="Previous structured output failed validation. Copy exact verbatim answer_quote and support_quote substrings, preserving Markdown, spaces, punctuation and citation markers. Every evidence ID must appear once. Never paraphrase a quote."
                if prior
                else None,
            )
        except Exception as exc:
            result = {
                "sample_id": sample_id,
                "mode": sample["mode"],
                "status": "failed",
                "stage": "judge",
                "error_type": type(exc).__name__,
            }
            append_json(directory / "errors.jsonl", result)
        append_json(directory / "verdicts.jsonl", result)
        results[sample_id] = result
        capture_usage(services, directory, sample_id, start, budget)
    for query in queries:
        for mode in ("dense", "hybrid_rerank"):
            sample_id = f"{query['query_id']}:{mode}"
            results.setdefault(
                sample_id,
                {
                    "sample_id": sample_id,
                    "mode": mode,
                    "status": "failed",
                    "stage": "generation",
                    "error_type": "MissingSample",
                },
            )
    controls = json.loads((directory / "controlled-verdicts.json").read_text(encoding="utf-8"))
    controlled_samples = {
        s["sample_id"]: s for s in records(directory / "controlled-samples.jsonl")
    }
    for index, control in enumerate(controls):
        sample_id = control["sample_id"]
        prior = sum(r["kind"] == "judge" and r["sample_id"] == sample_id for r in budget.records)
        if control["status"] != "completed" and prior < 3:
            start = len(services.http.calls)
            try:
                controls[index] = judge_sample(
                    controlled_samples[sample_id],
                    services.llm,
                    directory,
                    budget,
                    max_attempts=1,
                    attempt_offset=prior,
                    repair_feedback="Copy exact verbatim answer/evidence substrings, preserving punctuation and spacing.",
                )
            except Exception as exc:
                controls[index] = {
                    "sample_id": sample_id,
                    "status": "failed",
                    "error_type": type(exc).__name__,
                }
                append_json(directory / "errors.jsonl", {**controls[index], "stage": "controlled"})
            capture_usage(services, directory, sample_id, start, budget)
    (directory / "controlled-verdicts.json").write_text(
        json.dumps(controls, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    captured["repair_policy"] = {
        "generation_total_attempts_max": 2,
        "judge_total_attempts_max": 3,
        "original_budget_unchanged": True,
        "initial_generation_raw_missing": True,
        "original_failures_retained": True,
    }
    (directory / "config.json").write_text(
        json.dumps(captured, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return write_report(
        directory, list(samples.values()), list(results.values()), controls, budget, captured
    )
