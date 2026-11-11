"""Explicit ZERO-MODEL orphan fixture for deployment restart/recovery checks.

Use --create once after migration, restart backend, then GET /api/v1/evaluation-jobs.
It inserts only three clearly labeled interrupted-work fixtures, never submits a worker.
No models, vectors, active builds or existing jobs are modified. No automatic cleanup.
"""

import argparse
import json
from datetime import UTC, datetime
from uuid import uuid4

from psycopg.types.json import Jsonb

from apex_rag.schemas import EvaluationJob, JobEstimate
from apex_rag.services import Services


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--create", action="store_true", help="Explicitly insert3zero-model diagnostic rows")
    args = parser.parse_args()
    if not args.create:
        parser.error("--create is required; never runs on import or startup")
    services = Services()
    ids = []
    try:
        for state in ("queued", "running", "cancelling"):
            identity = str(uuid4())
            timestamp = datetime.now(UTC).isoformat()
            estimate = JobEstimate(configurations=[], total_questions=0, logical_calls_max=0, http_attempts_max=0,
                cost_ceiling_cny=0, conservative_estimate_cny=0, prices_cny_per_million={},
                caveats=["DIAGNOSTIC_ORPHAN_FIXTURE;not a real run;zero modelcalls/no worker"])
            data = EvaluationJob(job_id=identity, status=state, estimate=estimate,
                progress={"total_questions": 0, "total_runs": 0}, created_at=timestamp, updated_at=timestamp).model_dump(mode="json")
            with services.db.connection() as conn:
                conn.execute("INSERT INTO evaluation_jobs(job_id,idempotency_key,request_hash,data) VALUES(%s,%s,%s,%s)",
                    (identity, uuid4(), "DIAGNOSTIC_ORPHAN_FIXTURE", Jsonb(data)))
            ids.append({"job_id": identity, "inserted_status": state})
        print(json.dumps({"fixture_only": True, "model_calls": 0, "items": ids,
                          "next": "Restart backend then GET eachjob; expected interrupted;no evaluation_runs or paid replay"}))
    finally:
        services.close()


if __name__ == "__main__":
    main()
