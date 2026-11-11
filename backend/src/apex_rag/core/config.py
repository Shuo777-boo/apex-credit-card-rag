"""Validated environment-only settings and project-relative paths."""

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def project_root() -> Path:
    import os

    return Path(os.environ.get("APEX_PROJECT_ROOT", Path(__file__).resolve().parents[4])).resolve()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="APEX_", extra="ignore", populate_by_name=True, env_file=project_root() / ".env", env_file_encoding="utf-8")
    database_url: SecretStr = SecretStr("postgresql://apex:apex@localhost:5432/apex")
    dashscope_api_key: SecretStr = Field(default=SecretStr(""), validation_alias="DASHSCOPE_API_KEY")
    model_region: Literal["beijing", "singapore"] = "beijing"
    workspace_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9-]+$")
    embedding_model: Literal["text-embedding-v4"] = "text-embedding-v4"
    embedding_dimension: int = 1024
    rerank_model: Literal["qwen3-rerank"] = "qwen3-rerank"
    llm_model: Literal["qwen3.5-plus"] = "qwen3.5-plus"
    api_timeout_seconds: float = Field(default=60, gt=0, le=180)
    api_max_retries: int = Field(default=2, ge=0, le=3)
    api_max_requests: int = Field(default=1500, ge=1, le=10000)
    pool_min_size: int = Field(default=1, ge=0)
    pool_max_size: int = Field(default=10, ge=1, le=100)
    candidate_depth: int = Field(default=20, ge=20, le=500)
    rerank_depth: int = Field(default=20, ge=20, le=500)
    rrf_k: int = Field(default=60, ge=1)
    cors_origins: list[str] = ["http://localhost:5173"]
    data_dir: str = "data"
    reports_dir: str = "reports"

    @field_validator("workspace_id", mode="before")
    @classmethod
    def blank_workspace(cls, value: str | None) -> str | None:
        return value or None

    @model_validator(mode="after")
    def validate_dimensions(self) -> "Settings":
        if self.embedding_dimension not in {64, 128, 256, 512, 768, 1024, 1536}:
            raise ValueError("Use a v4-supported dimension <=2000 for pgvector vector HNSW")
        if self.pool_min_size > self.pool_max_size:
            raise ValueError("pool_min_size exceeds pool_max_size")
        if self.rerank_depth > self.candidate_depth * 2:
            raise ValueError("rerank_depth cannot exceed the maximum two-route candidate pool")
        return self

    @property
    def dashscope_base(self) -> str:
        if self.workspace_id:
            region = "cn-beijing" if self.model_region == "beijing" else "ap-southeast-1"
            return f"https://{self.workspace_id}.{region}.maas.aliyuncs.com"
        return "https://dashscope.aliyuncs.com" if self.model_region == "beijing" else "https://dashscope-intl.aliyuncs.com"

    @property
    def embedding_fingerprint(self) -> str:
        value = [self.model_region, self.workspace_id, self.embedding_model, self.embedding_dimension, "document", "dense"]
        return hashlib.sha256(json.dumps(value).encode()).hexdigest()

    def path(self, value: str) -> Path:
        path = Path(value)
        return path if path.is_absolute() else project_root() / path

    def public_models(self) -> dict[str, str | int]:
        return {"region": self.model_region, "embedding": self.embedding_model,
                "dimension": self.embedding_dimension, "reranker": self.rerank_model,
                "llm": self.llm_model}
