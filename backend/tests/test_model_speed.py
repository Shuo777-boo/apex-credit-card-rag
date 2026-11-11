"""No paid requests: diagnostic spending/retry/isolation guards."""

import runpy
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from apex_rag.core.config import Settings
from apex_rag.core.errors import ApexError

DIAG = runpy.run_path(str(Path(__file__).parents[1] / "diagnostics/model_speed.py"))


def test_budget_records_failed_attempt_and_reserves_retry(tmp_path):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(403, json={"code": "Denied"})

    settings = Settings(dashscope_api_key=SecretStr("test-key"), api_max_retries=1, api_max_requests=200)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    http = DIAG["BudgetHTTP"](settings, tmp_path, client)
    payload = {"model": "gte-rerank-v2", "input": {"query": "abc", "documents": ["def", "ghi"]}}
    assert DIAG["cost_bound"](payload) == pytest.approx((12 + 2048) * 2 * .8 / 1e6)
    with pytest.raises(ApexError, match="rejected"):
        http.post("/test", payload)
    assert len(requests) == 1  #403 never retries
    assert http.calls[0]["status"] == "failed"
    assert (tmp_path / "provider-calls.json").exists()
    http.reserved_cny = 10
    with pytest.raises(ApexError, match="budget"):
        http.post("/test", payload)
    assert len(requests) == 1
    http.close()


def test_v2_exact_dimension_and_no_dimension_parameter():
    class Fake:
        def post(self, path, payload):
            assert payload["model"] == "text-embedding-v2"
            assert payload["parameters"] == {"text_type": "query"}
            return {"output": {"embeddings": [{"text_index": 0, "embedding": [1.] * 1536}]}}

    assert len(DIAG["embed"](Fake(), "text-embedding-v2", ["hello"], "query")[0]) == 1536


def test_existing_paid_audit_rejects_rerun(tmp_path):
    (tmp_path / "provider-calls.json").write_text("{}")
    with pytest.raises(ValueError, match="never silently rerun"):
        DIAG["main"](tmp_path)
