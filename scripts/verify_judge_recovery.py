"""Offline verify a targeted recovery, including requests, strict raw and phase totals."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from apex_rag.evaluation.judge import anchored as a
from apex_rag.evaluation.judge import recovery as c
from apex_rag.evaluation.judge import rejudge as r
from apex_rag.evaluation.judge.runner import summarize, usage_summary


def audit(directory: Path) -> dict:
    config = json.loads((directory / "config.json").read_text(encoding="utf-8"))
    base = directory.parent / config["plan"]["base_run"]
    assert c.snapshot(base) == config["base_manifest"], "Base changed after recovery freeze"
    assert r.sha_file(Path(c.__file__)) == config["recovery_code_sha256"]
    assert r.method_manifest() == config["base_method"]
    base_report, samples, controls, base_verdicts = c.base_data(base)
    by_id = {s["sample_id"]: s for s in samples + controls}
    requests = r.read_lines(directory / "private-requests.jsonl")
    raw = r.read_lines(directory / "raw-judge.jsonl")
    stages = r.read_lines(directory / "stages.jsonl")
    inventories = {(x["sample_id"], a.digest(r.canonical(x["inventory"]))): x["inventory"]
                   for x in r.read_lines(directory / "inherited-inventories.jsonl")}
    validated = {}
    for request in requests:
        messages = request["messages"]
        assert a.digest(r.canonical(messages)) == request["request_sha256"]
        assert len(r.canonical(messages).encode("utf-8")) + 4096 == request["input_utf8_bytes_plus_overhead"]
        assert request["input_utf8_bytes_plus_overhead"] <= c.INPUT_BOUND
        expected_system = a.INVENTORY_SYSTEM if request["stage"] == "inventory_recovery" else a.VERDICT_SYSTEM
        assert messages[0] == {"role": "system", "content": expected_system}
        payload = json.loads(messages[1]["content"])
        assert "recovery_data" in payload and "gold_response" not in payload
    for stage in stages:
        sid, name = stage["sample_id"], stage["stage"]
        sample, sources = by_id[sid], a.bundle(by_id[sid])
        candidates = [x for x in raw if x["sample_id"] == sid and x["stage"] == name
                      and x.get("dependency_hash") == stage.get("dependency_hash")]
        assert len(candidates) == 1
        if name == "inventory_recovery":
            result = a.validate_inventory(candidates[0]["raw"], sample, sources)
            inventories[sid, a.digest(r.canonical(result))] = result
        else:
            result = a.validate_assessment(candidates[0]["raw"], sample, sources,
                                           inventories[sid, stage["dependency_hash"]])
        assert result == stage["result"]
        validated[sid, name, stage.get("dependency_hash")] = result
    recovered = {v["sample_id"]: v for v in r.read_lines(directory / "verdicts.jsonl")}
    assert set(recovered) == set(config["plan"]["failed_ids"])
    support = 0
    for sid, verdict in recovered.items():
        if verdict["status"] == "completed":
            assert any(key[0] == sid and key[2] == verdict["inventory_hash"] and value == verdict["verdict"]
                       for key, value in validated.items())
            support += sum(len(x["support_quotes"]) for x in verdict["verdict"]["claims"])
    combined = {**base_verdicts, **recovered}
    report = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
    metrics = summarize([combined[s["sample_id"]] for s in samples])
    assert report["metrics"] == metrics
    assert report["base_metrics"] == base_report["metrics"]
    for sid, original in base_verdicts.items():
        if sid not in recovered:
            displayed = next(v for v in report["verdicts"] if v["sample_id"] == sid) if not original.get("controlled") else next(v for v in report["controlled_diagnostics"] if v["sample_id"] == sid)
            assert {k: v for k, v in displayed.items() if k != "phase"} == original
    budget = json.loads((directory / "budget.json").read_text())
    per_sample = Counter(x["sample_id"] for x in budget["records"])
    assert max(per_sample.values()) <= 4 and budget["tasks"] == len(requests) == len(raw)
    assert budget["tasks"] <= config["plan"]["logical_calls_max"]
    assert budget["reserved_cost_ceiling"] <= config["plan"]["cost_reservation_max_CNY"]
    return {"run": directory.name, "policy": c.POLICY, "status": report["status"],
            "base_run_unchanged": True, "base_status": base_report["status"],
            "base_metrics": base_report["metrics"], "combined_metrics": metrics,
            "recovery_status_counts": dict(Counter(x["status"] for x in recovered.values())),
            "strict_valid_stages": len(stages), "exact_recovery_support_quotes": support,
            "unchanged_system_requests": len(requests), "per_sample_calls": dict(per_sample),
            "usage": usage_summary(directory), "reserved_CNY": budget["reserved_cost_ceiling"],
            "actual_bill": None, "human_completed": 0, "semantic_truth_verified": False,
            "selection_bias": "Failed-only extra attempts, not one uniform attempt policy; base and recovery disclosed separately"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.run)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    args.output.write_text(text, encoding="utf-8")
    print(text)
