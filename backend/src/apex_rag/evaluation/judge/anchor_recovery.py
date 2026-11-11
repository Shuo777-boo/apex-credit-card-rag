"""Second explicit recovery policy: immutable unit context for mechanically misplaced facts.

No changes to base SYSTEM/schema/scoring or previous recovery. Extra attempts are disclosed.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from . import anchored as a
from . import recovery as c
from . import rejudge as r
from .runner import Budget, append_json, citation_validity, write_report

POLICY = "qwen-judge-v2.2-anchor-recovery-v2"


def unit_diagnostics(raw: str, sample: dict, sources: dict) -> list[dict]:
    errors = c.diagnostics(raw, sample, sources, None)
    try:
        parsed = a.Inventory.model_validate_json(raw)
        values = {u.unit_id: u for u in parsed.units}
    except ValueError:
        values = {}
    units = []
    for anchor in sources["answer_units"]:
        unit = values.get(anchor["span_id"])
        actual = a.numeric_literals(" ".join(unit.claims)) if unit else set()
        original = a.numeric_literals(anchor["text"])
        lost = original - actual if unit and unit.claims else set()
        # Numeric matches are diagnostic candidates, never proof of semantic identity.
        elsewhere = [{"unit_id": u.unit_id, "claim": claim, "matching_digits": sorted(a.numeric_literals(claim) & lost)}
                     for u in values.values() if u.unit_id != anchor["span_id"] for claim in u.claims
                     if a.numeric_literals(claim) & lost]
        units.append({"immutable_original_anchor": anchor,
                      "previous_claims_for_this_unit": unit.claims if unit else [],
                      "digits_missing_from_this_units_claims": sorted(lost),
                      "other_units_with_matching_digits_candidates_only": elsewhere})
    return errors + [{"kind": "immutable_unit_context",
                      "scope": "Keep every assertion with the original unit that contains that assertion. Neighbouring text is context only. Re-extract all claims per unit; do not shift claims between anchors, omit fees, duplicate facts or invent assertions. Matching digits are mechanical hints, not gold or semantic proof.",
                      "units": units}]


def inputs(previous: Path):
    report = json.loads((previous / "summary.json").read_text(encoding="utf-8"))
    captured = json.loads((previous / "config.json").read_text(encoding="utf-8"))
    if report.get("recovery_policy") != c.POLICY:
        raise ValueError("Anchor recovery requires an ended recovery-v1 report")
    base = previous.parent / captured["plan"]["base_run"]
    if c.snapshot(base) != captured["base_manifest"]:
        raise ValueError("Original base changed")
    if r.sha_file(Path(c.__file__)) != captured["recovery_code_sha256"]:
        raise ValueError("Previous recovery code changed")
    _, samples, controls, _ = c.base_data(base)
    all_verdicts = {v["sample_id"]: v for v in report["verdicts"] + report["controlled_diagnostics"]}
    if set(all_verdicts) != {s["sample_id"] for s in samples + controls}:
        raise ValueError("Previous combined report must contain full63")
    failed = [s["sample_id"] for s in samples + controls if all_verdicts[s["sample_id"]]["status"] == "failed"]
    if not failed:
        raise ValueError("No remaining failed sample")
    # This version targets failed inventory only. Assessment changes require separate review.
    old_raw = r.read_lines(previous / "raw-judge.jsonl")
    for sid in failed:
        rows = [x for x in old_raw if x["sample_id"] == sid]
        if not rows or any(x.get("dependency_hash") is not None for x in rows):
            raise ValueError("Anchor-context recovery is limited to inventory failures")
    # Strictly revalidate inherited recovery successes, including exact body references.
    inherited = {x["sample_id"]: x["inventory"] for x in r.read_lines(previous / "inherited-inventories.jsonl")}
    for sample in samples + controls:
        sid = sample["sample_id"]
        value = all_verdicts[sid]
        if value.get("phase") != "targeted_recovery" or value["status"] != "completed":
            continue
        inv = inherited[sid]
        assert a.digest(r.canonical(inv)) == value["inventory_hash"]
        valid = False
        for row in old_raw:
            if row["sample_id"] == sid and row.get("dependency_hash") == value["inventory_hash"]:
                try:
                    valid |= a.validate_assessment(row["raw"], sample, a.bundle(sample), inv) == value["verdict"]
                except ValueError:
                    pass
        if not valid:
            raise ValueError("Previous recovery success lacks valid matching raw")
    return report, captured, samples, controls, all_verdicts, failed


def plan(services, previous: Path) -> dict:
    report, captured, _, _, _, failed = inputs(previous)
    if r.provider_manifest(services) != captured["provider"]:
        raise ValueError("Provider changed")
    n = len(failed)
    return {"policy": POLICY, "base_method": a.VERSION, "previous_run": previous.name,
            "original_base_run": captured["plan"]["base_run"], "failed_ids": failed,
            "logical_calls_max": 4*n, "http_attempts_max": 12*n,
            "cost_reservation_max_CNY": round(1.60*n, 8),
            "input_bound": c.INPUT_BOUND, "max_output_tokens": a.OUTPUT_TOKENS,
            "previous_status": report["status"], "new_answers_or_retrieval": 0,
            "note": "Two inventory attempts and two assessment attempts maximum per remaining failed sample. Same rubric; explicit phase/selection differences."}


def evaluate(services, previous, directory, budget, sample):
    sources, sid = a.bundle(sample), sample["sample_id"]
    raw = [x for x in r.read_lines(previous / "raw-judge.jsonl") if x["sample_id"] == sid][-1]["raw"]
    errors = unit_diagnostics(raw, sample, sources)
    inventory = None
    for name in ("inventory_context_1", "inventory_context_2"):
        inventory, last_raw, _ = c.call(services, directory, budget, sample, name, None, raw, errors)
        if inventory is not None:
            break
        if last_raw is not None:
            raw = last_raw
            errors = unit_diagnostics(raw, sample, sources)
    verdict = None
    if inventory is not None:
        for name in ("assessment_context_1", "assessment_context_2"):
            verdict, raw, errors = c.call(services, directory, budget, sample, name, inventory, raw, errors)
            if verdict is not None or any(e["kind"] == "inventory_mismatch" for e in errors):
                break
    result = {"sample_id": sid, "mode": sample["mode"], "controlled": sample.get("controlled", False),
              "status": "completed" if verdict else "failed", "phase": "anchor_context_recovery",
              "policy": POLICY, "context_relevance": None, "faithfulness": None,
              "inventory_hash": a.digest(r.canonical(inventory)) if inventory is not None else None}
    if verdict:
        claims, context = verdict["claims"], verdict["context_relevance"]
        result.update(verdict=verdict, claim_count=len(claims),
                      faithfulness=sum(x["verdict"] == "supported" for x in claims)/len(claims) if claims else None,
                      context_relevance=sum(x["score"] for x in context)/(2*len(context)) if context else None,
                      citation_validity=citation_validity(sample))
    append_json(directory / "verdicts.jsonl", result)
    return result


def run(services, previous: Path, directory: Path, *, resume=False) -> dict:
    planned = plan(services, previous)
    old, prior_config, samples, controls, combined, failed = inputs(previous)
    captured = {"policy": POLICY, "plan": planned, "previous_manifest": c.snapshot(previous),
                "base_method": r.method_manifest(), "provider": r.provider_manifest(services),
                "code_sha256": r.sha_file(Path(__file__)), "helper_sha256": r.sha_file(Path(c.__file__)),
                "bias": "Same Qwen self-evaluation and claim-omission risk; failed-only extra attempts create selection and attempt differences. Human0/10."}
    if directory.resolve().is_relative_to(previous.resolve()):
        raise ValueError("Use new independent directory")
    if resume:
        if json.loads((directory / "config.json").read_text(encoding="utf-8")) != captured:
            raise ValueError("Frozen phase changed")
        review = directory / "human-review.json"
        if review.exists() and any(x.get("status") == "completed" for x in json.loads(review.read_text(encoding="utf-8"))):
            raise ValueError("Preserve human reviews")
    else:
        directory.mkdir(parents=True, exist_ok=False)
        (directory / "config.json").write_text(json.dumps(captured, ensure_ascii=False, indent=2), encoding="utf-8")
        for name in ("samples.jsonl", "controlled-samples.jsonl"):
            (directory / name).write_bytes((previous / name).read_bytes())
        (directory / "policy-source.py").write_bytes(Path(__file__).read_bytes())
    n = len(failed)
    config = {"currency": "CNY", "max_tasks": 4*n, "max_cost": round(1.60*n, 8),
              "tariffs_verified": True, "tariffs": {"qwen3.5-plus": {"input_per_million": .8, "output_per_million": 4.8}},
              "cost_ceiling_per_task": {x: .40 for x in ("inventory_context_1", "inventory_context_2", "assessment_context_1", "assessment_context_2")}}
    budget = Budget(directory, config, resume=resume)
    results = {x["sample_id"]: x for x in r.read_lines(directory / "verdicts.jsonl")}
    for sample in samples + controls:
        sid = sample["sample_id"]
        if sid in failed and results.get(sid, {}).get("status") != "completed":
            results[sid] = evaluate(services, previous, directory, budget, sample)
    combined.update(results)
    report = write_report(directory, samples, [combined[s["sample_id"]] for s in samples],
                          [combined[s["sample_id"]] for s in controls], budget, captured)
    report.update(method_version=f"{a.VERSION}+recovery-v1+anchor-recovery-v2", recovery_policy=POLICY,
                  scope="full_saved_60_with_targeted_recovery", source_run=previous.name,
                  original_results_merged=False, base_results_reused=True,
                  base_status=old["base_status"], base_metrics=old["base_metrics"],
                  base_failed_ids=old["base_failed_ids"], base_budget_tasks=old["base_budget_tasks"],
                  base_provider_cost_summary=old["base_provider_cost_summary"],
                  previous_phase_status=old["status"], previous_phase_metrics=old["metrics"],
                  previous_phase_provider_cost_summary=old["provider_cost_summary"],
                  previous_phase_budget=old["budget"], remaining_failed_ids=failed,
                  recovery_status_counts=dict(Counter(x["status"] for x in results.values())),
                  recovery_plan=planned, semantic_completeness_verified=False,
                  method_note="Original base57/60 and first recovery59/60 retained; only remaining inventory failure gets exact-unit diagnostic extra attempts. Final metrics use explicit staged recovery, not a uniform attempt policy. Self-evaluation bias/human0/10 remain.")
    if c.snapshot(previous) != captured["previous_manifest"]:
        raise ValueError("Previous phase changed")
    (directory / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
