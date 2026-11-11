"""Offline strict audit of exact-unit recovery, immutable phases and combined metrics."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from apex_rag.evaluation.judge import anchor_recovery as v2
from apex_rag.evaluation.judge import anchored as a
from apex_rag.evaluation.judge import recovery as c
from apex_rag.evaluation.judge import rejudge as r
from apex_rag.evaluation.judge.runner import summarize, usage_summary


def audit(directory):
    config = json.loads((directory / "config.json").read_text(encoding="utf-8"))
    previous = directory.parent / config["plan"]["previous_run"]
    assert c.snapshot(previous) == config["previous_manifest"]
    assert r.method_manifest() == config["base_method"]
    assert r.sha_file(Path(v2.__file__)) == config["code_sha256"]
    assert r.sha_file(Path(c.__file__)) == config["helper_sha256"]
    old, _, samples, controls, combined, failed = v2.inputs(previous)
    samples_by_id = {s["sample_id"]: s for s in samples + controls}
    requests = r.read_lines(directory / "private-requests.jsonl")
    raw = r.read_lines(directory / "raw-judge.jsonl")
    stages = r.read_lines(directory / "stages.jsonl")
    inventories, validated = {}, {}
    for req in requests:
        messages = req["messages"]
        assert a.digest(r.canonical(messages)) == req["request_sha256"]
        assert len(r.canonical(messages).encode("utf-8")) + 4096 == req["input_utf8_bytes_plus_overhead"]
        assert req["input_utf8_bytes_plus_overhead"] <= 110000
        assert messages[0] == {"role": "system", "content": a.INVENTORY_SYSTEM if req["stage"].startswith("inventory") else a.VERDICT_SYSTEM}
        data = json.loads(messages[1]["content"])
        assert "gold_response" not in data
        if req["stage"].startswith("inventory"):
            exact_context = next(e for e in data["recovery_data"]["all_local_mechanical_diagnostics"] if e["kind"] == "immutable_unit_context")
            answer = samples_by_id[req["sample_id"]]["answer"]
            for item in exact_context["units"]:
                a.exact(item["immutable_original_anchor"], answer)
    for stage in stages:
        sid = stage["sample_id"]
        sample, sources = samples_by_id[sid], a.bundle(samples_by_id[sid])
        candidates = [x for x in raw if x["sample_id"] == sid and x["stage"] == stage["stage"]
                      and x.get("dependency_hash") == stage.get("dependency_hash")]
        assert len(candidates) == 1
        if stage["stage"].startswith("inventory"):
            value = a.validate_inventory(candidates[0]["raw"], sample, sources)
            inventories[sid, a.digest(r.canonical(value))] = value
        else:
            value = a.validate_assessment(candidates[0]["raw"], sample, sources, inventories[sid, stage["dependency_hash"]])
        assert value == stage["result"]
        validated[sid, stage["stage"], stage.get("dependency_hash")] = value
    results = {x["sample_id"]: x for x in r.read_lines(directory / "verdicts.jsonl")}
    assert set(results) == set(failed)
    exact_quotes = 0
    for sid, result in results.items():
        if result["status"] == "completed":
            assert any(k[0] == sid and k[2] == result["inventory_hash"] and v == result["verdict"] for k, v in validated.items())
            exact_quotes += sum(len(x["support_quotes"]) for x in result["verdict"]["claims"])
    combined.update(results)
    metrics = summarize([combined[s["sample_id"]] for s in samples])
    summary = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
    assert summary["metrics"] == metrics and summary["base_metrics"] == old["base_metrics"]
    assert summary["previous_phase_metrics"] == old["metrics"]
    for result in summary["verdicts"] + summary["controlled_diagnostics"]:
        assert result == combined[result["sample_id"]]
    budget = json.loads((directory / "budget.json").read_text())
    counts = Counter(x["sample_id"] for x in budget["records"])
    assert max(counts.values()) <= 4
    assert budget["tasks"] == len(requests) == len(raw) <= config["plan"]["logical_calls_max"]
    assert budget["reserved_cost_ceiling"] <= config["plan"]["cost_reservation_max_CNY"]
    return {"run": directory.name, "status": summary["status"], "policy": v2.POLICY,
            "base_and_prior_phases_unchanged": True, "method_unchanged": True,
            "base_metrics": old["base_metrics"], "previous_metrics": old["metrics"],
            "combined_metrics": metrics, "strict_stages": len(stages),
            "exact_support_quotes": exact_quotes, "request_count": len(requests),
            "calls_per_sample": dict(counts), "usage": usage_summary(directory),
            "reserved_CNY": budget["reserved_cost_ceiling"], "actual_bill": None,
            "human_review_completed": 0, "semantic_truth_verified": False,
            "attempt_selection_bias_disclosed": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = audit(args.run)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    args.output.write_text(rendered, encoding="utf-8")
    print(rendered)
