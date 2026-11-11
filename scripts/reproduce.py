"""Run the core reproduction CLI from any cwd; preserve failures and never invoke a shell."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone


def run(argv: list[str], root: Path, timeout: int) -> None:
    """Run an argument vector without echoing environment secrets."""
    subprocess.run(argv, cwd=root, check=True, timeout=timeout, shell=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/reproduce.yaml")
    parser.add_argument("--skip-database-start", action="store_true")
    parser.add_argument("--database-timeout", type=int, default=180)
    parser.add_argument("--workflow-timeout", type=int, default=7200)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    config = Path(args.config)
    config = config if config.is_absolute() else root / config
    if not config.is_file() or min(args.database_timeout, args.workflow_timeout) <= 0:
        parser.error("Configuration must exist and timeouts must be positive.")
    reports_root = Path(os.environ.get("APEX_REPORTS_DIR", "reports"))
    reports_root = reports_root if reports_root.is_absolute() else root / reports_root
    output = reports_root / "bonus-runs" / "reproduction-command.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    evidence = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "steps": [],
        "status": "running",
    }
    steps = []
    if not args.skip_database_start:
        steps.extend(
            [
                (["docker", "info", "--format", "{{.ServerVersion}}"], 30),
                (["docker", "compose", "version"], 30),
                (
                    [
                        "docker",
                        "compose",
                        "up",
                        "-d",
                        "--wait",
                        "--wait-timeout",
                        str(args.database_timeout),
                        "postgres",
                    ],
                    args.database_timeout + 60,
                ),
            ]
        )
    steps.append(
        (
            [
                sys.executable,
                "-m",
                "apex_rag.cli",
                "reproduce",
                "--config",
                str(config),
            ],
            args.workflow_timeout,
        )
    )
    try:
        for argv, timeout in steps:
            entry = {"argv": argv, "status": "running"}
            evidence["steps"].append(entry)
            run(argv, root, timeout)
            entry["status"] = "passed"
        evidence["status"] = "passed"
        return 0
    except (OSError, subprocess.SubprocessError) as exc:
        evidence["status"] = "failed"
        evidence["steps"][-1]["status"] = "failed"
        evidence["error_type"] = type(exc).__name__
        print(
            f"Reproduction failed: {type(exc).__name__}; inspect stage output.",
            file=sys.stderr,
        )
        return 1
    finally:
        evidence["finished_at"] = datetime.now(timezone.utc).isoformat()
        output.write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
        )


if __name__ == "__main__":
    raise SystemExit(main())
