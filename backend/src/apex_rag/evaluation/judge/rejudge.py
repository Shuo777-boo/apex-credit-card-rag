"""Independent v2 re-evaluation of immutable saved answers using the sole core LLM.

No generation, embedding, search, reranker, repository or old-run writes. A pilot and
continuation can share results only if all method/data/config hashes remain identical.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import anchored
from .runner import Budget, append_json, capture_usage, citation_validity, write_report


def read_lines(path: Path) -> list[dict]:
    return (
        [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        if path.exists()
        else []
    )


def sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def method_manifest() -> dict:
    schemas = {
        "inventory": anchored.Inventory.model_json_schema(),
        "assessment": anchored.Verdict.model_json_schema(),
    }
    code = {
        name: sha_file(Path(__file__).with_name(name))
        for name in ("anchored.py", "rejudge.py", "runner.py")
    }
    return {
        "version": anchored.VERSION,
        "inventory_prompt": anchored.INVENTORY_SYSTEM,
        "assessment_prompt": anchored.VERDICT_SYSTEM,
        "schemas": schemas,
        "prompt_sha256": {
            "inventory": anchored.digest(anchored.INVENTORY_SYSTEM),
            "assessment": anchored.digest(anchored.VERDICT_SYSTEM),
        },
        "schema_sha256": {
            name: anchored.digest(canonical(schema)) for name, schema in schemas.items()
        },
        "code_sha256": code,
    }


def provider_manifest(services: Any) -> dict:
    cls = type(services.llm)
    try:
        code_hash = sha_file(Path(inspect.getfile(cls)))
    except (TypeError, OSError):
        code_hash = None  # Explicit test doubles; production module is inspectable.
    return {
        "type": f"{cls.__module__}.{cls.__qualname__}",
        "code_sha256": code_hash,
        "max_output_tokens": anchored.OUTPUT_TOKENS,
        "temperature": 0,
        "enable_thinking": False,
        "api_max_retries": services.settings.api_max_retries,
    }


def saved_answers(source: Path) -> tuple[list[dict], list[dict]]:
    samples = read_lines(source / "samples.jsonl")
    expected = {(f"Q-{i:03d}", mode) for i in range(1, 31) for mode in ("dense", "hybrid_rerank")}
    actual = [(s["query_id"], s["mode"]) for s in samples]
    if len(actual) != 60 or set(actual) != expected:
        raise ValueError(
            "Rejudge requires exactly the same 30 questions in both modes, without duplicates."
        )
    if len({s["build_id"] for s in samples}) != 1 or len({s["model"] for s in samples}) != 1:
        raise ValueError("Original answer model and build must be uniform.")
    for qid, _ in expected:
        if len({s["question"] for s in samples if s["query_id"] == qid}) != 1:
            raise ValueError("Paired modes must contain the same question.")
    controls = read_lines(source / "controlled-samples.jsonl")
    if (
        len(controls) != 3
        or len({s["sample_id"] for s in controls}) != 3
        or any(not s.get("controlled") for s in controls)
    ):
        raise ValueError("Three separate controlled samples are required.")
    return samples, controls


def messages(stage: str, payload: dict, feedback: str | None = None) -> list[dict]:
    system = anchored.INVENTORY_SYSTEM if stage == "inventory" else anchored.VERDICT_SYSTEM
    data = {**payload}
    if feedback:
        data["local_validation_feedback"] = feedback
    return [{"role": "system", "content": system}, {"role": "user", "content": canonical(data)}]


def checked_messages(stage: str, payload: dict, feedback: str | None = None) -> list[dict]:
    result = messages(stage, payload, feedback)
    # UTF-8 bytes upper-bound provider tokens; include system, JSON framing and feedback.
    if len(canonical(result).encode("utf-8")) + 4096 > 70000:
        raise ValueError("Full input exceeds the verified 70k-byte token ceiling; do not truncate.")
    return result


def plan(services: Any, source: Path, config: dict) -> dict:
    samples, controls = saved_answers(source)
    budget = config["budget"]
    if (
        config.get("method_version") != anchored.VERSION
        or config.get("max_attempts_per_stage") != 2
        or config.get("max_output_tokens") != anchored.OUTPUT_TOKENS
    ):
        raise ValueError(
            "Method version,8192 output tokens and exactly two maximum attempts per stage must be explicit."
        )
    pilot_ids = config.get("pilot_ids", [])
    if (
        len(pilot_ids) != 5
        or len(set(pilot_ids)) != 5
        or not set(pilot_ids) <= {s["sample_id"] for s in samples}
    ):
        raise ValueError("Reviewed pilot must contain exactly five distinct saved formal samples.")
    if services.llm.model != "qwen3.5-plus" or any(
        s["model"] != services.llm.model for s in samples
    ):
        raise ValueError("Keep the original qwen3.5-plus model.")
    if services.settings.model_region != budget.get("region") or not budget.get("tariffs_verified"):
        raise ValueError("Actual region must match verified tariffs.")
    if services.settings.api_max_retries > 2:
        raise ValueError("New budget permits at most three HTTP attempts per logical call.")
    price = budget["tariffs"][services.llm.model]
    if price != {"input_per_million": 0.8, "output_per_million": 4.8}:
        raise ValueError("Price change needs a new conservative bound and reviewed method budget.")
    ceiling = (
        (70000 * price["input_per_million"] + anchored.OUTPUT_TOKENS * price["output_per_million"])
        / 1_000_000
        * 3
    )
    if (
        budget.get("max_tasks") != 252
        or budget.get("max_cost") != 76
        or any(budget["cost_ceiling_per_task"].get(k) != 0.30 for k in ("inventory", "assessment"))
        or ceiling > 0.30
    ):
        raise ValueError(
            "Reviewed independent v2 budget must be 252 tasks / 76 CNY / 0.30 per stage call."
        )
    sizes = []
    for sample in samples + controls:
        sources = anchored.bundle(sample)
        checked_messages(
            "inventory", {"question": sample["question"], "answer_units": sources["answer_units"]}
        )
        # Stage two actual inventory is checked immediately before every call; no hidden truncation.
        sizes.append(
            {
                "sample_id": sample["sample_id"],
                "answer_units": len(sources["answer_units"]),
                "body_spans": sum(len(e["body_spans"]) for e in sources["evidence"]),
            }
        )
    return {
        "method_version": anchored.VERSION,
        "formal_samples": 60,
        "controlled_samples": 3,
        "logical_calls_nominal": 126,
        "logical_calls_max": 252,
        "http_attempts_max": 756,
        "input_token_upper_bound_per_call": 70000,
        "pilot_logical_calls_max": 20,
        "pilot_http_attempts_max": 60,
        "pilot_cost_reservation_max": 6,
        "output_tokens_max": anchored.OUTPUT_TOKENS,
        "core_llm_temperature": 0,
        "thinking": False,
        "provider_attempts_max": 3,
        "currency": "CNY",
        "cost_reservation_max": 76,
        "derived_cost_upper_per_logical_call": ceiling,
        "new_answer_or_retrieval_calls": 0,
        "sizes": sizes,
        "note": "All 60 use v2; never merge old 48 with new 12. No semantic/human pass implied.",
    }


def stage_call(
    services: Any,
    directory: Path,
    budget: Budget,
    sample_id: str,
    stage: str,
    payload: dict,
    validate: Callable,
    maximum: int = 2,
    force_refresh: bool = False,
    dependency_hash: str | None = None,
) -> dict | None:
    path = directory / "stages.jsonl"
    attempts = sum(r["sample_id"] == sample_id and r["kind"] == stage for r in budget.records)
    successes = [
        r
        for r in read_lines(path)
        if r["sample_id"] == sample_id
        and r["stage"] == stage
        and (dependency_hash is None or r.get("dependency_hash") == dependency_hash)
    ]
    if successes and not force_refresh:
        return successes[-1]["result"]
    errors = [
        e
        for e in read_lines(directory / "errors.jsonl")
        if e["sample_id"] == sample_id and e["stage"] == stage
    ]
    feedback = errors[-1].get("validation_reason") if errors else None
    for attempt in range(attempts + 1, maximum + 1):
        usage_start = len(services.http.calls)
        validating = False
        try:
            request = checked_messages(stage, payload, feedback)
            budget.reserve(stage, sample_id)
            raw = services.llm.complete(
                request, json_mode=True, max_output_tokens=anchored.OUTPUT_TOKENS
            )
            provider_calls = services.http.calls[usage_start:]
            append_json(
                directory / "raw-judge.jsonl",
                {
                    "sample_id": sample_id,
                    "stage": stage,
                    "attempt": attempt,
                    "raw": raw,
                    "method_version": anchored.VERSION,
                    "request_sha256": anchored.digest(canonical(request)),
                    "dependency_hash": dependency_hash,
                    "model": services.llm.model,
                    "provider_finish_reason": provider_calls[-1].get("finish_reason")
                    if provider_calls
                    else None,
                    "max_output_tokens": anchored.OUTPUT_TOKENS,
                },
            )
            validating = True
            result = validate(raw)
            append_json(
                path,
                {
                    "sample_id": sample_id,
                    "stage": stage,
                    "attempt": attempt,
                    "result": result,
                    "dependency_hash": dependency_hash,
                },
            )
            return result
        except Exception as exc:
            diagnostic = getattr(exc, "diagnostic_text", None)
            if callable(diagnostic):
                partial = diagnostic()
                if partial is not None:
                    details = getattr(exc, "details", {})
                    append_json(
                        directory / "raw-judge.jsonl",
                        {
                            "sample_id": sample_id,
                            "stage": stage,
                            "attempt": attempt,
                            "raw": partial,
                            "status": "provider_rejected",
                            "method_version": anchored.VERSION,
                            "model": services.llm.model,
                            "provider_finish_reason": details.get("finish_reason"),
                            "max_output_tokens": details.get("max_tokens", anchored.OUTPUT_TOKENS),
                            "request_sha256": anchored.digest(canonical(request)),
                            "dependency_hash": dependency_hash,
                        },
                    )
            # Local validators have controlled messages. Provider errors may contain secrets.
            feedback = (
                str(exc)[:1000]
                if validating
                else "Input/budget/provider request failed; no unbounded retry."
            )
            append_json(
                directory / "errors.jsonl",
                {
                    "sample_id": sample_id,
                    "stage": stage,
                    "attempt": attempt,
                    "error_type": type(exc).__name__,
                    "error_code": getattr(exc, "code", None),
                    "validation_reason": feedback,
                    "dependency_hash": dependency_hash,
                },
            )
            if isinstance(exc, anchored.InventoryMismatch):
                raise
            if not validating:
                # Invalid input/budget is deterministic; HTTP already retries in core Provider.
                return None
        finally:
            capture_usage(services, directory, f"{sample_id}:{stage}", usage_start, budget)
    return None


def evaluate(services: Any, directory: Path, budget: Budget, sample: dict) -> dict:
    start = time.perf_counter()
    sources = anchored.bundle(sample)
    append_json(directory / "anchors.jsonl", sources)
    inventory = stage_call(
        services,
        directory,
        budget,
        sample["sample_id"],
        "inventory",
        {"question": sample["question"], "answer_units": sources["answer_units"]},
        lambda raw: anchored.validate_inventory(raw, sample, sources),
    )
    verdict = None
    while inventory is not None:
        try:
            verdict = stage_call(
                services,
                directory,
                budget,
                sample["sample_id"],
                "assessment",
                {
                    "question": sample["question"],
                    "answer": sample["answer"],
                    "evidence": [
                        {
                            "evidence_id": e["evidence_id"],
                            "doc_id": e["doc_id"],
                            "title": e.get("title"),
                            "card_tier": e.get("card_tier"),
                            "effective_year": e.get("effective_year"),
                            "body_spans": e["body_spans"],
                        }
                        for e in sources["evidence"]
                    ],
                    "claims": inventory["claims"],
                    "unit_audit": inventory["unit_audit"],
                },
                lambda raw, current=inventory: anchored.validate_assessment(
                    raw, sample, sources, current
                ),
                dependency_hash=anchored.digest(canonical(inventory)),
            )
            break
        except anchored.InventoryMismatch as exc:
            remaining = 2 - sum(
                r["sample_id"] == sample["sample_id"] and r["kind"] == "inventory"
                for r in budget.records
            )
            if remaining <= 0:
                break
            inventory = stage_call(
                services,
                directory,
                budget,
                sample["sample_id"],
                "inventory",
                {
                    "question": sample["question"],
                    "answer_units": sources["answer_units"],
                    "inventory_correction_feedback": exc.mismatches,
                    "instruction": "Re-extract the COMPLETE inventory from original units, preserving all original conditions. Do not change the answer. The assessment found these semantic mismatches.",
                },
                lambda raw: anchored.validate_inventory(raw, sample, sources),
                force_refresh=True,
            )
    result = {
        "sample_id": sample["sample_id"],
        "mode": sample["mode"],
        "controlled": sample.get("controlled", False),
        "status": "completed" if verdict else "failed",
        "method_version": anchored.VERSION,
        "context_relevance": None,
        "faithfulness": None,
        "stage": "inventory" if inventory is None else "assessment",
        "inventory_hash": anchored.digest(canonical(inventory)) if inventory is not None else None,
        "latency_ms": (time.perf_counter() - start) * 1000,
    }
    if verdict:
        claims = verdict["claims"]
        result.update(
            verdict=verdict,
            context_relevance=sum(c["score"] for c in verdict["context_relevance"])
            / (2 * len(verdict["context_relevance"]))
            if verdict["context_relevance"]
            else None,
            faithfulness=sum(c["verdict"] == "supported" for c in claims) / len(claims)
            if claims
            else None,
            claim_count=len(claims),
            citation_validity=citation_validity(sample),
        )
    append_json(directory / "verdicts.jsonl", result)
    return result


def run(
    services: Any,
    source: Path,
    directory: Path,
    config: dict,
    selected_ids: list[str] | None = None,
    resume: bool = False,
) -> dict:
    assessment_plan = plan(services, source, config)
    samples, controls = saved_answers(source)
    manifest = method_manifest()
    captured = {
        "config": config,
        "method": manifest,
        "provider": provider_manifest(services),
        "source_run": source.name,
        "source_samples_sha256": sha_file(source / "samples.jsonl"),
        "source_controls_sha256": sha_file(source / "controlled-samples.jsonl"),
        "model": services.llm.model,
        "build_id": samples[0]["build_id"],
        "plan": assessment_plan,
        "bias": "Same Qwen model inventories/evaluates its old answers. Self-evaluation and omitted-claim bias remain; structural anchors are not human truth.",
    }
    if directory.resolve() == source.resolve():
        raise ValueError("New method must never write into the original run.")
    if resume:
        old = json.loads((directory / "config.json").read_text(encoding="utf-8"))
        if captured != old:
            raise ValueError(
                "Method/code/schema/prompt/config/data changed; pilot results cannot be reused."
            )
        review_path = directory / "human-review.json"
        if review_path.exists() and any(
            r.get("status") == "completed"
            for r in json.loads(review_path.read_text(encoding="utf-8"))
        ):
            raise ValueError(
                "Preserve completed human reviews; do not overwrite them during continuation."
            )
    else:
        directory.mkdir(parents=True, exist_ok=False)
        (directory / "config.json").write_text(
            json.dumps(captured, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (directory / "samples.jsonl").write_bytes((source / "samples.jsonl").read_bytes())
        (directory / "controlled-samples.jsonl").write_bytes(
            (source / "controlled-samples.jsonl").read_bytes()
        )
        (directory / "method-sources").mkdir()
        for code_name, code_sha in manifest["code_sha256"].items():
            original_code = Path(__file__).with_name(code_name)
            if sha_file(original_code) != code_sha:
                raise ValueError("Method source changed during snapshot.")
            (directory / "method-sources" / code_name).write_bytes(original_code.read_bytes())
        (directory / "original-run-manifest.json").write_text(
            json.dumps({p.name: sha_file(p) for p in source.iterdir() if p.is_file()}, indent=2),
            encoding="utf-8",
        )
        for name, value in (
            ("inventory-prompt.txt", anchored.INVENTORY_SYSTEM),
            ("assessment-prompt.txt", anchored.VERDICT_SYSTEM),
        ):
            (directory / name).write_text(value, encoding="utf-8")
        (directory / "schemas.json").write_text(
            json.dumps(manifest["schemas"], ensure_ascii=False, indent=2), encoding="utf-8"
        )
    if (
        sha_file(directory / "samples.jsonl") != captured["source_samples_sha256"]
        or sha_file(directory / "controlled-samples.jsonl") != captured["source_controls_sha256"]
    ):
        raise ValueError("Local immutable sample snapshots changed.")
    budget = Budget(directory, config["budget"], resume=resume)
    existing = {r["sample_id"]: r for r in read_lines(directory / "verdicts.jsonl")}
    all_ids = {s["sample_id"] for s in samples + controls}
    if selected_ids is not None and (
        len(set(selected_ids)) != len(selected_ids) or not set(selected_ids) <= all_ids
    ):
        raise ValueError("Pilot IDs must be distinct saved sample IDs.")
    for sample in samples + controls:
        sid = sample["sample_id"]
        if selected_ids is not None and sid not in selected_ids:
            continue
        if existing.get(sid, {}).get("status") == "completed":
            continue
        existing[sid] = evaluate(services, directory, budget, sample)
    formal = [
        existing.get(
            s["sample_id"], {"sample_id": s["sample_id"], "mode": s["mode"], "status": "missing"}
        )
        for s in samples
    ]
    diagnostics = [
        existing.get(
            s["sample_id"],
            {
                "sample_id": s["sample_id"],
                "mode": "controlled",
                "status": "missing",
                "controlled": True,
            },
        )
        for s in controls
    ]
    (directory / "controlled-verdicts.json").write_text(
        json.dumps(diagnostics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    report = write_report(directory, samples, formal, diagnostics, budget, captured)
    report.update(
        method_version=anchored.VERSION,
        source_run=source.name,
        method_changed=True,
        original_results_merged=False,
        human_review_completed=0,
        scope="pilot" if selected_ids is not None else "full_saved_60",
        semantic_completeness_verified=False,
        pilot_ids=config.get("pilot_ids"),
        pilot_status="completed"
        if all(
            existing.get(sid, {}).get("status") == "completed"
            for sid in config.get("pilot_ids", [])
        )
        else "incomplete",
        method_note="All means use v2 results only. Exhaustive unit accounting is structural; atomic extraction/entailment still require human review.",
    )
    for mode in ("dense", "hybrid_rerank"):
        mode_existing = [v for v in existing.values() if v.get("mode") == mode]
        report["metrics"][mode]["attempted"] = len(mode_existing)
        report["metrics"][mode]["failed"] = sum(v["status"] == "failed" for v in mode_existing)
        report["metrics"][mode]["missing"] = 30 - len(mode_existing)
    report["execution_audit"]["v2_stage_error_records"] = {
        stage: sum(e.get("stage") == stage for e in read_lines(directory / "errors.jsonl"))
        for stage in ("inventory", "assessment")
    }
    (directory / "summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report
