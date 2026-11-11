"""Protocol fixtures only: these are never used by formal evaluation or demo."""

import httpx
import pytest
from pydantic import SecretStr

from apex_rag.core.config import Settings
from apex_rag.core.errors import ApexError
from apex_rag.providers.aliyun import (
    AliyunEmbedding,
    AliyunLLM,
    AliyunReranker,
    ChatResponseError,
    DashScopeHTTP,
)


def test_embedding_order_dimension_text_type_and_native_protocol():
    requests = []
    def handler(request):
        import json
        payload = json.loads(request.content)
        requests.append(payload)
        assert request.url.path == "/api/v1/services/embeddings/text-embedding/text-embedding"
        return httpx.Response(200, json={"output": {"embeddings": [
            {"text_index": 1, "embedding": [2.] * 64}, {"text_index": 0, "embedding": [1.] * 64}]}})
    settings = Settings(dashscope_api_key=SecretStr("test-fixture-only"), embedding_dimension=64)
    provider = AliyunEmbedding(DashScopeHTTP(settings, httpx.Client(transport=httpx.MockTransport(handler))))
    vectors = provider.embed(["one", "two"], input_type="query")
    assert vectors[0] == [1.] * 64
    assert requests[0]["parameters"] == {"text_type": "query", "dimension": 64, "output_type": "dense"}


@pytest.mark.parametrize("bad", [[1.] * 63, [0.] * 64, [float("nan")] * 64])
def test_no_vector_truncation_or_nonfinite_or_zero(bad):
    def handler(request):
        # Build JSON for finite variants; NaN cannot be encoded by httpx json.
        import json
        return httpx.Response(200, content=json.dumps({"output": {"embeddings": [{"text_index": 0, "embedding": bad}]}}))
    provider = AliyunEmbedding(DashScopeHTTP(Settings(dashscope_api_key=SecretStr("test-fixture-only"), embedding_dimension=64),
        httpx.Client(transport=httpx.MockTransport(handler))))
    with pytest.raises(ApexError):
        provider.embed(["one"])


def test_real_rerank_protocol_index_validation():
    def handler(request):
        import json
        payload = json.loads(request.content)
        assert payload["model"] == "qwen3-rerank"
        assert "messages" not in payload
        assert request.url.path == "/api/v1/services/rerank/text-rerank/text-rerank"
        return httpx.Response(200, json={"output": {"results": [{"index": 0, "relevance_score": .9}, {"index": 0, "relevance_score": .8}]}})
    provider = AliyunReranker(DashScopeHTTP(Settings(dashscope_api_key=SecretStr("test-fixture-only")),
        httpx.Client(transport=httpx.MockTransport(handler))))
    with pytest.raises(ApexError, match="schema"):
        provider.rerank("q", ["a", "b"], top_n=2)


def test_bounded_retry_and_no_auth_error_retry(monkeypatch):
    monkeypatch.setattr("apex_rag.providers.aliyun.time.sleep", lambda seconds: None)
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(429, json={"message": "provider secret body never exposed"})
    http = DashScopeHTTP(Settings(dashscope_api_key=SecretStr("test-fixture-only"), api_max_retries=2),
        httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(ApexError) as error:
        http.post("/test", {"model": "test"})
    assert len(calls) == 3
    assert http.calls[0]["status"] == "failed"
    assert http.calls[0]["attempts"] == 3
    assert http.calls[0]["http_status"] == 429
    assert http.calls[0]["usage"] is None
    assert len(http.calls[0]["attempt_details"]) == 3
    assert http.calls[0]["elapsed_ms"] >= 0
    assert "secret" not in str(error.value.details)
    calls.clear()
    http.client = httpx.Client(transport=httpx.MockTransport(lambda r: (calls.append(r) or httpx.Response(401))))
    with pytest.raises(ApexError):
        http.post("/test", {})
    assert len(calls) == 1
    assert http.calls[-1]["attempts"] == 1 and http.calls[-1]["http_status"] == 401


def test_process_budget_prevents_additional_request():
    calls = []
    http = DashScopeHTTP(Settings(dashscope_api_key=SecretStr("test-fixture-only"), api_max_requests=1),
        httpx.Client(transport=httpx.MockTransport(lambda r: (calls.append(r) or httpx.Response(200, json={"ok": True})))))
    assert http.post("/test", {}) == {"ok": True}
    with pytest.raises(ApexError) as exceeded:
        http.post("/test", {})
    assert exceeded.value.code == "MODEL_BUDGET_EXCEEDED"
    assert len(calls) == 1
    assert http.calls[-1]["status"] == "failed" and http.calls[-1]["attempts"] == 0


def test_timeout_failure_is_recorded_without_sensitive_request(monkeypatch):
    def handler(request):
        raise httpx.ReadTimeout("fixture transport private detail")
    http = DashScopeHTTP(Settings(dashscope_api_key=SecretStr("test-fixture-only"), api_max_retries=0),
        httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(ApexError):
        http.post("/test", {"model": "model", "input": "sensitive prompt"})
    assert http.calls[0]["attempt_details"][0]["error_type"] == "ReadTimeout"
    assert http.calls[0]["attempts"] == 1
    assert "sensitive" not in str(http.calls)
    assert "private detail" not in str(http.calls)
    assert http.calls[0]["finish_reason"] is None
    assert http.calls[0]["max_tokens"] is None


@pytest.mark.parametrize("content", ['{"claims":["private incomplete', ""])
def test_chat_length_safe_metadata_and_private_exact_text(content):
    import json

    def handler(request):
        payload = json.loads(request.content)
        assert payload["model"] == "qwen3.5-plus"
        assert payload["max_tokens"] == 2400
        assert payload["enable_thinking"] is False
        return httpx.Response(200, json={"id": "fixture-chat", "choices": [
            {"finish_reason": "length", "message": {"content": content}}],
            "usage": {"completion_tokens": 2400}})

    http = DashScopeHTTP(Settings(dashscope_api_key=SecretStr("fixture-key")),
        httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(ChatResponseError) as caught:
        AliyunLLM(http).complete([{"role": "user", "content": "private-input"}], json_mode=True)
    exc = caught.value
    assert isinstance(exc, ApexError) and exc.code == "INVALID_PROVIDER_RESPONSE"
    assert exc.diagnostic_text() == content
    assert exc.details == {"finish_reason": "length", "max_tokens": 2400}
    assert http.calls[-1]["finish_reason"] == "length"
    assert http.calls[-1]["max_tokens"] == 2400
    assert http.calls[-1]["status"] == "completed"  #transport, not valid completion
    safe = json.dumps(http.calls) + str(exc.details) + str(exc) + repr(exc)
    assert "private" not in safe and "fixture-key" not in safe
    assert http.calls[-1]["attempts"] == 1


@pytest.mark.parametrize("choice,expected,reason", [
    ({"finish_reason": "stop", "message": {"content": '{"ok":true}'}}, '{"ok":true}', "stop"),
    ({"message": {"content": "valid"}}, "valid", None),
    ({"finish_reason": "stop", "message": {"content": []}}, None, "stop"),
    ({"finish_reason": "length"}, None, "length"),
])
def test_chat_completion_or_schema_failure_never_fabricates_finish_reason(choice, expected, reason):
    http = DashScopeHTTP(Settings(dashscope_api_key=SecretStr("fixture-key")),
        httpx.Client(transport=httpx.MockTransport(lambda request:
            httpx.Response(200, json={"choices": [choice]}))))
    llm = AliyunLLM(http)
    if expected is None:
        with pytest.raises(ChatResponseError) as caught:
            llm.complete([])
        assert caught.value.diagnostic_text() is None
        assert caught.value.details["finish_reason"] == reason
    else:
        assert llm.complete([]) == expected
    assert http.calls[-1]["finish_reason"] == reason
    assert http.calls[-1]["max_tokens"] == 2400


@pytest.mark.parametrize("limit", [1, 8192])
@pytest.mark.parametrize("reason", ["stop", "length"])
def test_explicit_chat_output_limit_maps_to_payload_audit_and_safe_error(limit, reason):
    import json

    def handler(request):
        payload = json.loads(request.content)
        assert payload["max_tokens"] == limit
        assert payload["model"] == "qwen3.5-plus"
        assert payload["enable_thinking"] is False
        assert payload["response_format"] == {"type": "json_object"}
        return httpx.Response(200, json={"choices": [{"finish_reason": reason,
            "message": {"content": "private-model-result"}}]})

    http = DashScopeHTTP(Settings(dashscope_api_key=SecretStr("fixture-key")),
        httpx.Client(transport=httpx.MockTransport(handler)))
    llm = AliyunLLM(http)
    if reason == "length":
        with pytest.raises(ChatResponseError) as caught:
            llm.complete([], json_mode=True, max_output_tokens=limit)
        assert caught.value.details == {"finish_reason": reason, "max_tokens": limit}
        assert caught.value.diagnostic_text() == "private-model-result"
        assert "private-model-result" not in str(caught.value) + repr(caught.value) + str(caught.value.details)
    else:
        assert llm.complete([], json_mode=True, max_output_tokens=limit) == "private-model-result"
    assert http.calls[-1]["max_tokens"] == limit
    assert http.calls[-1]["finish_reason"] == reason
    assert "private-model-result" not in str(http.calls)


@pytest.mark.parametrize("invalid", [True, False, 0, -1, 8193, 2400.0, "8192", None])
def test_invalid_chat_output_limit_rejected_before_http(invalid):
    requests = []
    http = DashScopeHTTP(Settings(dashscope_api_key=SecretStr("fixture-key")),
        httpx.Client(transport=httpx.MockTransport(lambda request: requests.append(request))))
    with pytest.raises(ValueError, match="max_output_tokens"):
        AliyunLLM(http).complete([], max_output_tokens=invalid)
    assert requests == [] and http.calls == [] and http.attempts == 0
