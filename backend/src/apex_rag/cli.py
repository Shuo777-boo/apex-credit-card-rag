"""Public CLI; command execution is loaded lazily to keep --help free of I/O."""

import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="apex-rag")
    commands = parser.add_subparsers(dest="command", required=True)
    db = commands.add_parser("db").add_subparsers(dest="db_command", required=True)
    db.add_parser("migrate")
    ingest = commands.add_parser("ingest")
    ingest.add_argument("--strategy", required=True, choices=["fixed_window", "policy_hierarchy"])
    index = commands.add_parser("index").add_subparsers(dest="index_command", required=True)
    for name in ["build", "activate"]:
        index.add_parser(name).add_argument("--build-id", required=True)
    commands.add_parser("benchmark-index").add_argument("--build-id", required=True)
    commands.add_parser("report").add_argument("--run-id", required=True)
    for name in ["evaluate", "bootstrap", "reproduce"]:
        child = commands.add_parser(name)
        child.add_argument("--config", default=f"configs/{name}.yaml")
    commands.add_parser("validate-data")
    commands.add_parser("probe-models")
    commands.add_parser("verify-answers")
    return parser


def main() -> int:
    from apex_rag.workflow import execute

    return execute(build_parser())


if __name__ == "__main__":
    raise SystemExit(main())
