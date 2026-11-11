"""Mechanical neighbouring-unit context without moving claims or weakening the rubric."""

import copy
import json
import subprocess
import sys
from pathlib import Path

from test_judge_anchored import fixture
from test_judge_recovery import make_base, responder

from apex_rag.evaluation.judge import anchor_recovery as v2
from apex_rag.evaluation.judge import anchored as a
from apex_rag.evaluation.judge import recovery as c
from apex_rag.evaluation.judge import rejudge as r


def test_misplaced_adjacent_number_diagnoses_exact_units_without_mutation():
    sample = fixture()
    sample["answer"] = "Late payment fee is $29;Returned payment fee is $35."
    sources = a.bundle(sample)
    raw = {"units": [
        {"unit_id": "A0001", "claims": ["Late payment fee is $29", "Returned payment fee is $35"], "nonfactual_reason": None},
        {"unit_id": "A0002", "claims": ["Returned payments incur a fee"], "nonfactual_reason": None},
    ]}
    before = copy.deepcopy(raw)
    errors = v2.unit_diagnostics(json.dumps(raw), sample, sources)
    context = errors[-1]["units"]
    assert context[1]["digits_missing_from_this_units_claims"] == ["35"]
    assert context[1]["other_units_with_matching_digits_candidates_only"][0]["unit_id"] == "A0001"
    for item in context:
        anchor = item["immutable_original_anchor"]
        assert a.exact(anchor, sample["answer"]) == anchor["text"]
    assert raw == before


def test_two_inventory_attempts_explicit_phase_merge_and_idempotence(tmp_path):
    base, services = make_base(tmp_path, fail_inventory=True)
    services.llm.complete = lambda *args, **kwargs: '{"units":[]}'
    previous = tmp_path / "previous"
    first = c.run(services, base, previous)
    assert first["status"] == "incomplete"
    before = c.snapshot(previous)
    calls = []

    def complete(messages, **kwargs):
        calls.append(messages)
        if len(calls) == 1:
            return '{"units":[]}'
        return responder(messages, **kwargs)

    services.llm.complete = complete
    plan = v2.plan(services, previous)
    assert plan["logical_calls_max"] == 4 and plan["cost_reservation_max_CNY"] == 1.6
    destination = tmp_path / "anchor"
    report = v2.run(services, previous, destination)
    assert len(calls) == 3 and report["status"] == "completed_pending_human"
    assert report["base_metrics"]["dense"]["completed"] == 29
    assert report["previous_phase_metrics"]["dense"]["completed"] == 29
    assert report["metrics"]["dense"]["completed"] == 30
    assert report["human_review_completed"] == 0 and before == c.snapshot(previous)
    root = Path(__file__).resolve().parents[3]
    subprocess.run([sys.executable, str(root / "scripts/verify_judge_anchor_recovery.py"),
                    str(destination), "--output", str(tmp_path / "audit.json")],
                   check=True, capture_output=True, text=True)
    assert all(m[0]["content"] in {a.INVENTORY_SYSTEM, a.VERDICT_SYSTEM} for m in calls)
    v2.run(services, previous, destination, resume=True)
    assert len(calls) == 3 and before == c.snapshot(previous)
    raw = r.read_lines(destination / "raw-judge.jsonl")
    assert raw[-1]["dependency_hash"] is not None


def test_remaining_inventory_can_fail_without_fabricating_scores(tmp_path):
    base, services = make_base(tmp_path, fail_inventory=True)
    services.llm.complete = lambda *args, **kwargs: '{"units":[]}'
    previous = tmp_path / "previous"
    c.run(services, base, previous)
    report = v2.run(services, previous, tmp_path / "anchor")
    assert report["status"] == "incomplete" and report["metrics"]["dense"]["completed"] == 29
    budget = json.loads((tmp_path / "anchor/budget.json").read_text())
    assert budget["tasks"] == 2
