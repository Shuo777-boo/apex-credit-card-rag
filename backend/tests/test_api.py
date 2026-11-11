"""FastAPI validation/error/liveness tests without model or database calls."""

from fastapi.testclient import TestClient

from apex_rag.core.config import Settings
from apex_rag.core.errors import ApexError
from apex_rag.main import create_app
from apex_rag.providers.aliyun import ChatResponseError
from apex_rag.services import Services


def test_health_validation_and_error_envelope_without_io(monkeypatch):
    services = Services(Settings())
    monkeypatch.setattr(services.repository, "get_build", lambda *a, **k: (_ for _ in ()).throw(ApexError("BUILD_NOT_READY", "No active build")))
    with TestClient(create_app(services)) as client:
        assert client.get("/health").status_code == 200
        assert "graph_enhanced" in client.get("/api/v1/capabilities").json()
        response = client.post("/api/v1/search", json={"query": "secret input", "top_k": 0})
        assert response.status_code == 422
        body = response.json()
        assert body["error"]["code"] == "VALIDATION_ERROR"
        assert "secret input" not in response.text
        assert response.headers["x-request-id"] == body["request_id"]
        ready = client.get("/health/ready")
        assert ready.status_code == 503
        assert ready.json()["error"]["code"] == "BUILD_NOT_READY"
        assert client.get("/missing").json()["error"]["code"] == "NOT_FOUND"
        schema = client.get("/openapi.json").json()
        assert "/api/v1/answer" in schema["paths"]


def test_chat_truncation_error_never_exposes_private_text(monkeypatch):
    services = Services(Settings())
    failure = ChatResponseError("Generation exceeded bounded output budget",
        raw_text="private-truncated-output", finish_reason="length", max_tokens=2400)

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(services.answer, "answer", fail)
    with TestClient(create_app(services)) as client:
        response = client.post("/api/v1/answer", json={"query": "fee?"})
        assert response.status_code == 502
        assert response.json()["error"]["details"] == {"finish_reason": "length", "max_tokens": 2400}
        assert response.json()["error"]["code"] == "INVALID_PROVIDER_RESPONSE"
        assert "private-truncated-output" not in response.text
    assert failure.diagnostic_text() == "private-truncated-output"
