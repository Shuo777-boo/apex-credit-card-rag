"""Explicit ZERO-MODEL restart check using the core-owned diagnostic fixture.

Stops/restarts only this Compose backend and inserts three labeled orphan fixture rows.
Requires prior migration and no active real job. Does not submit evaluation jobs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time
import urllib.request
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--empty-restart", action="store_true",
                        help="Restart with no active jobs, insert no fixture rows (demo default)")
    parser.add_argument("--frontend", default="http://127.0.0.1:18080")
    parser.add_argument("--backend", default="http://127.0.0.1:18001")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.execute:
        parser.error("Explicit --execute required for labeled fixture insertion and backend restart")
    root = Path(__file__).resolve().parents[1]
    fixture = root / "backend/diagnostics/job_restart_fixture.py"
    record: dict = {"kind": "zero_model_orphan_restart_fixture", "model_calls": 0,
                    "fixture_sha256": hashlib.sha256(fixture.read_bytes()).hexdigest(),
                    "steps": [], "status": "running"}

    def save() -> None:
        args.output.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

    def get(path: str) -> dict:
        with urllib.request.urlopen(args.frontend.rstrip("/") + path, timeout=15) as response:
            return json.load(response)

    def run(argv: list[str], timeout: int = 150) -> str:
        started = time.monotonic()
        result = subprocess.run(argv, cwd=root, check=True, capture_output=True, text=True,
                                timeout=timeout)
        record["steps"].append({"argv": argv, "exit": result.returncode,
                                "elapsed_seconds": time.monotonic() - started})
        save()
        return result.stdout

    stopped = False
    try:
        jobs = get("/api/v1/evaluation-jobs")
        if any(x["status"] in {"queued", "running", "cancelling"} for x in jobs["items"]):
            raise ValueError("Active job exists; refuse restart fixture until it has ended")
        before = get("/api/v1/evaluations")
        record["evaluation_runs_before"] = sorted(x["run_id"] for x in before["items"])
        run(["docker", "compose", "--profile", "app", "stop", "backend"])
        stopped = True
        # Demo deployment uses --empty-restart: real orphan states are covered in isolated DB tests.
        # Explicit fixture mode is reserved for an isolated database/schema, never demo job lists.
        if args.empty_restart:
            created = {"fixture_only": False, "model_calls": 0, "items": []}
        else:
            fixture_output = run([__import__("sys").executable, str(fixture), "--create"], timeout=30)
            created = json.loads(fixture_output)
            assert created["fixture_only"] and created["model_calls"] == 0 and len(created["items"]) == 3
        record["fixture"] = created
        save()
        run(["docker", "compose", "--profile", "app", "up", "-d", "--no-build",
             "--wait", "--wait-timeout", "120", "backend"])
        stopped = False
        recovered = [get("/api/v1/evaluation-jobs/" + x["job_id"]) for x in created["items"]]
        record["recovered"] = recovered
        assert all(x["status"] == "interrupted" and x["estimate"]["total_questions"] == 0
                   and not x["runs"] for x in recovered)
        after = get("/api/v1/evaluations")
        jobs_after = get("/api/v1/evaluation-jobs")
        record["job_ids_before"] = sorted(x["job_id"] for x in jobs["items"])
        record["job_ids_after"] = sorted(x["job_id"] for x in jobs_after["items"])
        if args.empty_restart:
            assert record["job_ids_before"] == record["job_ids_after"]
        record["evaluation_runs_after"] = sorted(x["run_id"] for x in after["items"])
        assert record["evaluation_runs_before"] == record["evaluation_runs_after"]
        with urllib.request.urlopen(args.backend.rstrip("/") + "/health/ready", timeout=10) as response:
            record["readiness_status"] = response.status
        record["status"] = "passed"
    except Exception as exc:
        record["status"] = "failed"
        record["error_type"] = type(exc).__name__
        save()
        raise SystemExit("Restart check failed; inspect saved nonsecret step evidence") from None
    finally:
        if stopped:
            try:
                run(["docker", "compose", "--profile", "app", "up", "-d", "--no-build",
                     "--wait", "--wait-timeout", "120", "backend"])
            except Exception as exc:
                record["restoration_error_type"] = type(exc).__name__
        save()
    print(json.dumps({"status": record["status"], "fixture_rows_retained": len(created["items"]),
                      "model_calls": 0, "new_evaluation_runs": 0}))


if __name__ == "__main__":
    main()
