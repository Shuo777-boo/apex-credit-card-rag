"""CLI dispatch and bounded bootstrap/reproduce orchestration shared by local and containers."""

import argparse
import json
import sys
from typing import Any
from uuid import uuid4

import psycopg
import yaml
from pydantic import ValidationError

from apex_rag.core.config import Settings
from apex_rag.core.errors import ApexError
from apex_rag.ingestion.data import copy_official, load_official
from apex_rag.schemas import Strategy
from apex_rag.services import Services, register_cli


def load_config(settings: Settings, path: str) -> dict[str, Any]:
    value = yaml.safe_load(settings.path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Configuration must be a YAML mapping")
    return value


def bootstrap(services: Services, config: dict[str, Any]) -> dict[str, Any]:
    """Migrate, resume necessary paid embeddings, index and activate configured strategy.

    Idempotently reuses cached vectors; does not run quality, latency benchmark or Judge.
    Failures propagate nonzero through CLI and cannot activate an incomplete build.
    """
    services.db.migrate()
    strategies: list[Strategy] = config.get("strategies", ["policy_hierarchy"])
    if not strategies or any(s not in {"fixed_window", "policy_hierarchy"} for s in strategies):
        raise ValueError("Invalid bootstrap strategies")
    builds: list[dict[str, Any]] = []
    for strategy in strategies:
        result = services.ingestion.ingest(strategy, **config.get("chunking", {}))
        result["indexes"] = services.ingestion.build_indexes(result["build_id"], **config.get("hnsw", {}))
        builds.append(result)
    active_strategy = config.get("activate_strategy", strategies[-1])
    active = next((b for b in builds if b["config"]["strategy"] == active_strategy), None)
    if active is None:
        raise ValueError("activate_strategy must be one of the prepared strategies")
    services.ingestion.activate(active["build_id"])
    return {"status": "ready", "builds": builds, "active_build_id": active["build_id"], "evaluations_run": 0}


def reproduce(services: Services, config: dict[str, Any]) -> dict[str, Any]:
    """Execute explicit paid two-build/six-mode reproduction and persisted reports.

    Requires allow_paid_api, uses same APIs/algorithms as serving, includes both index
    benchmarks and invokes only requested bonus mapping via extension_reproduce. Failed
    questions/extension requests remain evidence and make the CLI exit nonzero.
    """
    from apex_rag.evaluation.index_benchmark import benchmark_index
    from apex_rag.evaluation.runner import combined_report, evaluate, report, write_json

    if not config.get("allow_paid_api", False):
        raise ValueError("reproduce requires allow_paid_api: true in its explicit config")
    prepared = bootstrap(services, {**config, "strategies": ["fixed_window", "policy_hierarchy"]})
    runs = []
    errors = []
    for build in prepared["builds"]:
        for mode in ["dense", "hybrid", "hybrid_rerank"]:
            result = evaluate(services, {"build_id": build["build_id"], "mode": mode})
            runs.append(result)
            report(services, result["run_id"])
            if result["status"] != "completed":
                errors.append(result["run_id"])
    benchmark = [benchmark_index(services, b["build_id"], repeats=int(config.get("benchmark_repeats", 34)))
        for b in prepared["builds"]] if config.get("benchmark_index", True) else None
    path = combined_report(services, runs)
    bonus = config.get("bonus", {})
    extension_result = None
    if any(bonus.values()):
        if services.extension_reproduce is None:
            raise ApexError("EXTENSION_UNAVAILABLE", "Bonus reproduction requested but extension hook missing")
        extension_result = services.extension_reproduce(bonus)
    output = {"prepared": prepared, "runs": runs, "benchmark": benchmark, "report": path,
        "bonus": extension_result, "failed_runs": errors}
    write_json(services.settings.path(services.settings.reports_dir) / "core-reproduction.json", output)
    if errors:
        raise ApexError("EVALUATION_FAILED", "Core reproduction contains failed questions", 502, {"failed_runs": errors})
    return output


def dispatch(services: Services, args: argparse.Namespace) -> Any:
    if callable(getattr(args, "handler", None)):
        return args.handler(args)
    match args.command:
        case "validate-data":
            hashes = copy_official(services.settings.path(services.settings.data_dir))
            docs, queries, _ = load_official(services.settings.path(services.settings.data_dir))
            return {"documents": len(docs), "queries": len(queries), "hashes": hashes}
        case "probe-models":
            vector = services.embedding.embed(["Apex balance transfer policy"], input_type="query")[0]
            ranked = services.reranker.rerank("balance transfer fee", ["Balance transfer fee is 3%.", "Airport lounge benefit."], top_n=2)
            answer = services.llm.complete([{"role": "user", "content": 'Reply with a JSON object {"ok":true}.'}], json_mode=True)
            return {"models": services.settings.public_models(), "dimension": len(vector), "rerank": ranked, "llm_json": json.loads(answer), "calls": services.http.calls}
        case "verify-answers":
            from apex_rag.generation.validation import verify_answers

            return verify_answers(services)
        case "db":
            return {"applied": services.db.migrate()}
        case "ingest":
            return services.ingestion.ingest(args.strategy)
        case "index":
            return services.ingestion.build_indexes(args.build_id) if args.index_command == "build" else services.ingestion.activate(args.build_id)
        case "bootstrap":
            return bootstrap(services, load_config(services.settings, args.config))
        case "reproduce":
            return reproduce(services, load_config(services.settings, args.config))
        case "evaluate":
            from apex_rag.evaluation.runner import evaluate

            result = evaluate(services, load_config(services.settings, args.config))
            if result["status"] != "completed":
                raise ApexError("EVALUATION_FAILED", "Evaluation recorded failed questions", 502, {"run_id": result["run_id"]})
            return result
        case "report":
            from apex_rag.evaluation.runner import report

            return report(services, args.run_id)
        case "benchmark-index":
            from apex_rag.evaluation.index_benchmark import benchmark_index

            return benchmark_index(services, args.build_id)
        case _:
            raise ValueError("Unrecognized command")


def execute(parser: argparse.ArgumentParser) -> int:
    services: Services | None = None
    command_name = "initialization"
    try:
        services = Services()
        register_cli(parser, services)
        args = parser.parse_args()
        command_name = args.command
        result = dispatch(services, args)
        print(json.dumps(result, ensure_ascii=False, default=str))
        return 0
    except ApexError as exc:
        print(json.dumps({"error": {"code": exc.code, "message": exc.message, "details": exc.details}}, ensure_ascii=False), file=sys.stderr)
        return 1
    except (ValidationError, ValueError, OSError, yaml.YAMLError):
        print(json.dumps({"error": {"code": "CONFIGURATION_ERROR", "message": "Check typed environment, config path and official input files"}}), file=sys.stderr)
        return 2
    except psycopg.Error:
        print(json.dumps({"error": {"code": "DATABASE_ERROR", "message": "Database operation failed; verify migrations and permissions"}}), file=sys.stderr)
        return 1
    except Exception:
        print(json.dumps({"error": {"code": "EXECUTION_ERROR", "message": "Command failed; inspect saved artifacts and verify optional dependencies"}}), file=sys.stderr)
        return 1
    finally:
        if services:
            if services.http.calls:
                from apex_rag.evaluation.runner import write_json

                write_json(services.settings.path(services.settings.reports_dir) / "core-evidence" /
                    f"provider-{command_name}-{uuid4().hex[:8]}.json", {"calls": services.http.calls,
                        "actual_http_attempts": services.http.attempts,
                        "max_http_attempts": services.settings.api_max_requests})
            services.close()
