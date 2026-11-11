"""FastAPI v1 entrypoint with stable safe error envelopes and active-build readiness."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

import psycopg
from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException

from apex_rag.core.errors import ApexError
from apex_rag.evaluation.jobs import EvaluationJobs
from apex_rag.evaluation.pricing import official_pricing
from apex_rag.schemas import (
    AnswerRequest,
    AnswerResponse,
    Document,
    ErrorBody,
    ErrorResponse,
    EvaluationDetail,
    EvaluationJob,
    EvaluationJobList,
    EvaluationJobRequest,
    EvaluationList,
    EvaluationOptions,
    HealthResponse,
    JobEstimate,
    PricingCatalog,
    SearchRequest,
    SearchResponse,
)
from apex_rag.services import Services, register_api


def create_app(services: Services | None = None) -> FastAPI:
    container = services or Services()
    jobs = EvaluationJobs(container)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        # No DB/model on startup; job API access performs stale ownership recovery.
        try:
            if jobs.threads:
                jobs.close()
        finally:
            container.close()

    app = FastAPI(title="Apex Policy RAG", version="1.0.0", lifespan=lifespan,
        responses={status: {"model": ErrorResponse} for status in (404, 409, 422, 500, 502, 503)})
    app.state.services = container
    app.add_middleware(CORSMiddleware, allow_origins=container.settings.cors_origins,
        allow_methods=["GET", "POST"], allow_headers=["Content-Type"])

    @app.middleware("http")
    async def request_identity(request: Request, call_next: Any) -> Any:
        request.state.request_id = str(uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    def error_response(request: Request, code: str, message: str, status: int,
                       details: dict[str, Any] | None = None) -> JSONResponse:
        return JSONResponse(status_code=status, content=ErrorResponse(request_id=request.state.request_id,
            error=ErrorBody(code=code, message=message, details=details or {})).model_dump(mode="json"))

    @app.exception_handler(ApexError)
    async def apex_error(request: Request, exc: ApexError) -> JSONResponse:
        return error_response(request, exc.code, exc.message, exc.status, exc.details)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return error_response(request, "VALIDATION_ERROR", "Request validation failed", 422,
            {"fields": [{"location": list(x["loc"]), "type": x["type"]} for x in exc.errors()]})

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        return error_response(request, "NOT_FOUND" if exc.status_code == 404 else "HTTP_ERROR", "HTTP request failed", exc.status_code)

    @app.exception_handler(psycopg.Error)
    async def database_error(request: Request, exc: psycopg.Error) -> JSONResponse:
        return error_response(request, "DATABASE_UNAVAILABLE", "Database operation failed; check migration and active build", 503)

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        return error_response(request, "INTERNAL_ERROR", "Unexpected server error; use request_id for diagnosis", 500)

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(status="ok", details={"capabilities": {
            "graph_enhanced": container.search.graph_search is not None,
            "extensions_registered": container.extensions_registered},
            "models": container.settings.public_models()})

    @app.get("/health/ready", response_model=HealthResponse)
    def ready() -> HealthResponse:
        build = container.repository.get_build()
        counts = container.repository.validate_ready(build)
        if build["config"]["embedding_fingerprint"] != container.settings.embedding_fingerprint:
            raise ApexError("BUILD_NOT_READY", "Active embedding fingerprint differs from configured provider")
        return HealthResponse(status="ready", build_id=build["build_id"], details=counts)

    router = APIRouter(prefix="/api/v1")

    router.add_api_route("/readiness", ready, methods=["GET"], response_model=HealthResponse)

    @router.get("/capabilities")
    def capabilities() -> dict[str, Any]:
        return {"graph_enhanced": container.search.graph_search is not None,
                "extensions_registered": container.extensions_registered,
                "models": container.settings.public_models()}

    @router.post("/search", response_model=SearchResponse)
    def search(body: SearchRequest, request: Request) -> SearchResponse:
        return container.search.search(body, request_id=request.state.request_id)

    @router.post("/answer", response_model=AnswerResponse)
    def answer(body: AnswerRequest, request: Request) -> AnswerResponse:
        return container.answer.answer(body, request_id=request.state.request_id)

    @router.get("/evaluation-options", response_model=EvaluationOptions)
    def evaluation_options() -> dict[str, Any]:
        return jobs.options()

    @router.get("/pricing", response_model=PricingCatalog)
    def pricing() -> PricingCatalog:
        return official_pricing(container.settings.model_region)

    @router.post("/evaluation-jobs/estimate", response_model=JobEstimate)
    def evaluation_estimate(body: EvaluationJobRequest) -> JobEstimate:
        return jobs.estimate(body)

    @router.post("/evaluation-jobs", response_model=EvaluationJob, status_code=202)
    def evaluation_submit(body: EvaluationJobRequest) -> EvaluationJob:
        return jobs.submit(body)

    @router.get("/evaluation-jobs", response_model=EvaluationJobList)
    def evaluation_jobs() -> EvaluationJobList:
        return jobs.list()

    @router.get("/evaluation-jobs/{job_id}", response_model=EvaluationJob)
    def evaluation_job(job_id: str) -> EvaluationJob:
        return jobs.get(job_id)

    @router.post("/evaluation-jobs/{job_id}/cancel", response_model=EvaluationJob)
    def evaluation_cancel(job_id: str) -> EvaluationJob:
        return jobs.cancel(job_id)

    @router.get("/documents/{doc_id}", response_model=Document)
    def document(doc_id: str) -> Document:
        return Document.model_validate(container.repository.get_document(doc_id))

    @router.get("/evaluations", response_model=EvaluationList)
    def evaluations() -> EvaluationList:
        return container.repository.list_evaluations()

    @router.get("/evaluations/{run_id}", response_model=EvaluationDetail)
    def evaluation(run_id: str) -> EvaluationDetail:
        return container.repository.get_evaluation(run_id)

    @router.get("/evaluations/{run_id}/artifacts/{artifact_name}")
    def evaluation_artifact(run_id: str, artifact_name: str) -> FileResponse:
        detail = container.repository.get_evaluation(run_id)
        if artifact_name not in detail.artifacts:
            raise ApexError("NOT_FOUND", "Evaluation artifact not found", 404)
        root = container.settings.path(container.settings.reports_dir).resolve()
        target = container.settings.path(detail.artifacts[artifact_name]).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            raise ApexError("NOT_FOUND", "Evaluation artifact not found", 404)
        return FileResponse(target, filename=target.name)

    register_api(router, container)
    app.include_router(router)
    return app


app = create_app()
