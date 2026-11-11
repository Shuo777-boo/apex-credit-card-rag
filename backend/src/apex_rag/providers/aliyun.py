"""Real DashScope native embedding/rerank and OpenAI-compatible Qwen chat.

Uses the native DashScope embedding/rerank APIs and the compatible chat endpoint.
"""

import math
import time
from collections.abc import Callable
from typing import Any

import httpx

from apex_rag.core.config import Settings
from apex_rag.core.errors import ApexError


class ChatResponseError(ApexError):
    """Public-safe chat failure with explicit, opt-in private text access for audits.

    diagnostic_text() is only for restricted raw-output artifacts. The text is excluded
    from Exception args/repr, public details and ordinary HTTP call records. Never log
    vars(exc) or send the private text through API error details.
    """

    def __init__(self, message: str, *, raw_text: str | None,
                 finish_reason: str | None, max_tokens: int) -> None:
        super().__init__("INVALID_PROVIDER_RESPONSE", message, 502,
                         {"finish_reason": finish_reason, "max_tokens": max_tokens})
        self._raw_text = raw_text

    def diagnostic_text(self) -> str | None:
        """Return actual provider content, including empty/truncated text; never a repair."""
        return self._raw_text


def first_finish_reason(result: dict[str, Any]) -> str | None:
    """Read the actual first chat choice reason; missing/malformed metadata stays null."""
    choices = result.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return None
    value = choices[0].get("finish_reason")
    return value if isinstance(value, str) and len(value) <= 64 else None


def validate_vectors(vectors: list[list[float]], count: int, dimension: int) -> None:
    if len(vectors) != count:
        raise ApexError("INVALID_PROVIDER_RESPONSE", "Embedding count mismatch", 502)
    for vector in vectors:
        if len(vector) != dimension or not all(math.isfinite(v) for v in vector):
            raise ApexError("INVALID_PROVIDER_RESPONSE", "Embedding dimension or values invalid", 502)
        if sum(v * v for v in vector) <= 0:
            raise ApexError("INVALID_PROVIDER_RESPONSE", "Zero embedding cannot use cosine index", 502)


class DashScopeHTTP:
    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        self.settings = settings
        self.client = client or httpx.Client(timeout=settings.api_timeout_seconds)
        self.calls: list[dict[str, Any]] = []
        self.attempts = 0
        self.before_attempt: Callable[[], None] | None = None

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Perform one real logical HTTP call with finite retries and shared attempt budget.

        All outcomes append safe metadata, per-attempt status/ms and nullable usage. Never
        persist authentication, raw prompts or provider exception bodies. HTTP auth errors
        do not retry; rate/transient transport failures retry at most configured count.
        """
        started = time.perf_counter()
        record: dict[str, Any] = {"model": payload.get("model"), "request_id": None,
            "status": "failed", "error_type": None, "http_status": None,
            "attempts": 0, "elapsed_ms": None, "usage": None, "attempt_details": [],
            "max_tokens": payload.get("max_tokens"), "finish_reason": None}
        try:
            key = self.settings.dashscope_api_key.get_secret_value()
            if not key:
                raise ApexError("PROVIDER_NOT_CONFIGURED", "DASHSCOPE_API_KEY is required")
            for attempt in range(self.settings.api_max_retries + 1):
                if self.before_attempt:
                    self.before_attempt()
                if self.attempts >= self.settings.api_max_requests:
                    raise ApexError("MODEL_BUDGET_EXCEEDED", "Process model request budget exhausted", 429,
                        {"max_requests": self.settings.api_max_requests})
                self.attempts += 1
                record["attempts"] += 1
                at = time.perf_counter()
                try:
                    response = self.client.post(self.settings.dashscope_base + path,
                        headers={"Authorization": f"Bearer {key}"}, json=payload)
                except (httpx.TimeoutException, httpx.NetworkError) as exc:
                    record["attempt_details"].append({"attempt": attempt + 1,
                        "http_status": None, "error_type": type(exc).__name__,
                        "elapsed_ms": (time.perf_counter() - at) * 1000})
                    if attempt < self.settings.api_max_retries:
                        time.sleep(min(2 ** attempt, 4))
                        continue
                    raise ApexError("PROVIDER_ERROR", "Model network request failed after bounded retries", 502) from None
                record["http_status"] = response.status_code
                record["attempt_details"].append({"attempt": attempt + 1,
                    "http_status": response.status_code, "error_type": "HTTP_ERROR" if response.is_error else None,
                    "elapsed_ms": (time.perf_counter() - at) * 1000})
                if response.status_code in {429, 500, 502, 503, 504} and attempt < self.settings.api_max_retries:
                    time.sleep(min(2 ** attempt, 4))
                    continue
                if response.is_error:
                    raise ApexError("PROVIDER_ERROR", "Model request rejected", 502,
                        {"http_status": response.status_code, "model": payload.get("model"), "attempts": attempt + 1})
                try:
                    result: dict[str, Any] = response.json()
                    if not isinstance(result, dict) or result.get("code"):
                        raise ValueError()
                except (ValueError, TypeError):
                    raise ApexError("INVALID_PROVIDER_RESPONSE", "Model returned invalid JSON envelope", 502) from None
                record.update(status="completed", request_id=result.get("request_id", result.get("id")),
                              usage=result.get("usage"), finish_reason=first_finish_reason(result))
                return result
            raise ApexError("PROVIDER_ERROR", "Model retry limit reached", 502)
        except ApexError as exc:
            record["error_type"] = exc.code
            raise
        finally:
            record["elapsed_ms"] = (time.perf_counter() - started) * 1000
            self.calls.append(record)

    def close(self) -> None:
        self.client.close()


class AliyunEmbedding:
    def __init__(self, http: DashScopeHTTP) -> None:
        self.http = http
        self.model: str = http.settings.embedding_model
        self.dimension = http.settings.embedding_dimension

    def embed(self, texts: list[str], *, input_type: str = "document") -> list[list[float]]:
        """Call v4 in <=10-text batches and restore text_index order without truncation.

        Validates count, finite nonzero vectors and exact configured dimension. Empty input
        costs nothing; every nonempty missing batch is paid. Raises on malformed output.
        """
        if not texts:
            return []
        if input_type not in {"document", "query"}:
            raise ValueError("input_type must be document or query")
        vectors: list[list[float]] = []
        for offset in range(0, len(texts), 10):
            batch = texts[offset:offset + 10]
            result = self.http.post("/api/v1/services/embeddings/text-embedding/text-embedding",
                {"model": self.model, "input": {"texts": batch}, "parameters": {
                    "dimension": self.dimension, "text_type": input_type, "output_type": "dense"}})
            try:
                items = result["output"]["embeddings"]
                if sorted(item["text_index"] for item in items) != list(range(len(batch))):
                    raise ValueError()
                part = [[float(v) for v in item["embedding"]] for item in sorted(items, key=lambda x: x["text_index"])]
                validate_vectors(part, len(batch), self.dimension)
            except (KeyError, TypeError, ValueError, OverflowError):
                raise ApexError("INVALID_PROVIDER_RESPONSE", "Embedding schema validation failed", 502) from None
            vectors.extend(part)
        return vectors


class AliyunReranker:
    def __init__(self, http: DashScopeHTTP) -> None:
        self.http = http
        self.model: str = http.settings.rerank_model

    def rerank(self, query: str, documents: list[str], *, top_n: int) -> list[tuple[int, float]]:
        """Call dedicated qwen3-rerank on supplied contextualized chunks.

        Returns unique validated zero-based input indices with descending relevance scores;
        never substitutes a chat model. Empty candidates require no external request.
        """
        if not documents:
            return []
        result = self.http.post("/api/v1/services/rerank/text-rerank/text-rerank",
            {"model": self.model, "input": {"query": query, "documents": documents},
             "parameters": {"top_n": min(top_n, len(documents)), "return_documents": False}})
        try:
            ranked = [(int(x["index"]), float(x["relevance_score"])) for x in result["output"]["results"]]
            if len(ranked) != min(top_n, len(documents)) or len({i for i, _ in ranked}) != len(ranked):
                raise ValueError()
            if any(i < 0 or i >= len(documents) or not math.isfinite(s) for i, s in ranked):
                raise ValueError()
            return sorted(ranked, key=lambda x: -x[1])
        except (KeyError, TypeError, ValueError):
            raise ApexError("INVALID_PROVIDER_RESPONSE", "Reranker schema validation failed", 502) from None


class AliyunLLM:
    def __init__(self, http: DashScopeHTTP) -> None:
        self.http = http
        self.model: str = http.settings.llm_model

    def complete(self, messages: list[dict[str, str]], *, json_mode: bool = False,
                 max_output_tokens: int = 2400) -> str:
        """Call Qwen chat with bounded output, optional JSON object and disabled thinking.

        Output budget is a strict integer1..8192, default2400; reject before HTTP/billing.
        Returns nonempty model content; truncation/network/schema errors fail explicitly.
        Callers own semantic/citation checks and must not treat JSON validity as truth.
        """
        if type(max_output_tokens) is not int or not 1 <= max_output_tokens <= 8192:
            raise ValueError("max_output_tokens must be an integer from1 through8192")
        payload: dict[str, Any] = {"model": self.model, "messages": messages,
            "temperature": 0, "max_tokens": max_output_tokens, "enable_thinking": False}
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        result = self.http.post("/compatible-mode/v1/chat/completions", payload)
        finish_reason = first_finish_reason(result)
        content: Any = None
        try:
            content = result["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError):
            pass
        raw_text = content if isinstance(content, str) else None
        if finish_reason == "length":
            raise ChatResponseError("Generation exceeded bounded output budget",
                raw_text=raw_text, finish_reason=finish_reason, max_tokens=payload["max_tokens"])
        if not isinstance(content, str) or not content.strip():
            raise ChatResponseError("Chat schema validation failed",
                raw_text=raw_text, finish_reason=finish_reason, max_tokens=payload["max_tokens"])
        return content
