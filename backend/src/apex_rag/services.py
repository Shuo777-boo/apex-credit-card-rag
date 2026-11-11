"""Application service container and lazy, explicit extension registration."""

import argparse
import importlib
from collections.abc import Callable
from typing import Any

from apex_rag.core.config import Settings
from apex_rag.generation.service import AnswerService
from apex_rag.ingestion.pipeline import IngestionPipeline
from apex_rag.providers.aliyun import AliyunEmbedding, AliyunLLM, AliyunReranker, DashScopeHTTP
from apex_rag.retrieval.service import SearchService
from apex_rag.storage.db import Database
from apex_rag.storage.repository import Repository


class Services:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()
        self.db = Database(self.settings)
        self.repository = Repository(self.db)
        self.http = DashScopeHTTP(self.settings)
        self.embedding = AliyunEmbedding(self.http)
        self.reranker = AliyunReranker(self.http)
        self.llm = AliyunLLM(self.http)
        self.search = SearchService(self.settings, self.repository, self.embedding, self.reranker)
        self.answer = AnswerService(self.search, self.llm)
        self.ingestion = IngestionPipeline(self.settings, self.repository, self.embedding)
        self.extension_reproduce: Callable[[dict[str, Any]], Any] | None = None
        self.extensions_registered = False

    def close(self) -> None:
        self.db.close()
        self.http.close()


def extension_module() -> Any:
    try:
        return importlib.import_module("apex_rag.extensions.register")
    except ModuleNotFoundError as exc:
        if exc.name not in {"apex_rag.extensions", "apex_rag.extensions.register"}:
            raise
        return None


def register_cli(parser: argparse.ArgumentParser, services: Services) -> None:
    module = extension_module()
    if module is not None:
        module.register_cli(parser, services)
        services.extensions_registered = True


def register_api(router: Any, services: Services) -> None:
    module = extension_module()
    if module is not None:
        module.register_api(router, services)
        services.extensions_registered = True
