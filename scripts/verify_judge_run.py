"""Offline structural audit of an anchored Judge run; never makes model calls.

Revalidates raw outputs and exact source spans, not semantic truth or human review.
Run from the repository root with the same installed backend environment.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from apex_rag.evaluation.judge import anchored, rejudge
from apex_rag.evaluation.judge.runner import summarize, usage_summary


def audit(directory: Path) -> dict:
    captured = json.loads((directory / "config.json").read_text(encoding="utf-8"))
    samples = {
        s["sample_id"]: s
        for filename in ("samples.jsonl", "controlled-samples.jsonl")
        for s in rejudge.read_lines(directory / filename)
    }
    raw = rejudge.read_lines(directory / "raw-judge.jsonl")
    stages = rejudge.read_lines(directory / "stages.jsonl")
    inventories: dict[tuple[str, str], dict] = {}
    checked_stages = {}
    for stage in stages:
        sid = stage["sample_id"]
        sample = samples[sid]
        sources = anchored.bundle(sample)
        candidates = [
            r for r in raw
            if r["sample_id"] == sid and r["stage"] == stage["stage"]
            and r["attempt"] == stage["attempt"]
            and r.get("dependency_hash") == stage.get("dependency_hash")
        ]
        assert len(candidates) == 1, (sid, "raw correspondence")
        item = candidates[0]
        if stage["stage"] == "inventory":
            validated = anchored.validate_inventory(item["raw"], sample, sources)
            key = anchored.digest(rejudge.canonical(validated))
            inventories[sid, key] = validated
        else:
            inventory = inventories[sid, stage["dependency_hash"]]
            validated = anchored.validate_assessment(item["raw"], sample, sources, inventory)
        assert validated == stage["result"], (sid, "raw/result mismatch")
        checked_stages[sid, stage["stage"], stage.get("dependency_hash")] = validated
    verdicts = {r["sample_id"]: r for r in rejudge.read_lines(directory / "verdicts.jsonl")}
    support_count = 0
    for sid, verdict in verdicts.items():
        if verdict["status"] != "completed":
            continue
        validated = checked_stages[sid, "assessment", verdict["inventory_hash"]]
        assert validated == verdict["verdict"], (sid, "final/stage mismatch")
        claims = validated["claims"]
        faith = sum(c["verdict"] == "supported" for c in claims) / len(claims) if claims else None
        context = validated["context_relevance"]
        relevance = sum(c["score"] for c in context) / (2 * len(context)) if context else None
        assert faith == verdict["faithfulness"] and relevance == verdict["context_relevance"]
        support_count += sum(len(c["support_quotes"]) for c in claims)
    source = directory.parent / captured["source_run"]
    original = json.loads((directory / "original-run-manifest.json").read_text(encoding="utf-8"))
    current = {p.name: rejudge.sha_file(p) for p in source.iterdir() if p.is_file()}
    assert original == current, "Original run files changed"
    assert rejudge.method_manifest() == captured["method"], "Frozen method changed"
    from apex_rag.providers.aliyun import AliyunLLM
    import inspect
    assert rejudge.sha_file(Path(inspect.getfile(AliyunLLM))) == captured["provider"]["code_sha256"]
    for name, key in (("samples.jsonl", "source_samples_sha256"),
                      ("controlled-samples.jsonl", "source_controls_sha256")):
        assert rejudge.sha_file(directory / name) == captured[key]
    budget = json.loads((directory / "budget.json").read_text(encoding="utf-8"))
    attempts = Counter((r["sample_id"], r["kind"]) for r in budget["records"])
    assert max(attempts.values(), default=0) <= 2 and budget["tasks"] <= 252
    assert budget["reserved_cost_ceiling"] <= 76
    formal = [v for v in verdicts.values() if not v.get("controlled")]
    controls = [v for v in verdicts.values() if v.get("controlled")]
    usage = rejudge.read_lines(directory / "provider-usage.jsonl")
    report = {
        "method_version": captured["method"]["version"],
        "formal_status_counts": dict(Counter(v["status"] for v in formal)),
        "control_status_counts": dict(Counter(v["status"] for v in controls)),
        "strictly_revalidated_stages": len(stages),
        "exact_support_quotes": support_count,
        "metrics": summarize(formal),
        "usage": usage_summary(directory),
        "finish_reasons": dict(Counter(u.get("finish_reason") for u in usage)),
        "largest_output_tokens": max((u.get("usage", {}).get("completion_tokens", 0) for u in usage), default=0),
        "budget_tasks": budget["tasks"],
        "reservation_CNY": budget["reserved_cost_ceiling"],
        "actual_bill": None,
        "original_run_unchanged": True,
        "method_provider_sample_hashes_unchanged": True,
        "semantic_truth_verified": False,
        "human_reviews_completed": 0,
        "note": "Structural audit only. Original failures/raw remain; no paid calls or human decisions.",
        "audit_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit(args.run)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered)
