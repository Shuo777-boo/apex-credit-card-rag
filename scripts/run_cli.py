"""Bound a container CLI workflow and preserve its nonzero exit status."""

from __future__ import annotations
import argparse
import subprocess
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=int, required=True)
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    argv = args.arguments[1:] if args.arguments[:1] == ["--"] else args.arguments
    if args.timeout <= 0 or not argv:
        parser.error("A positive timeout and core CLI arguments are required.")
    try:
        return subprocess.run(
            [sys.executable, "-m", "apex_rag.cli", *argv],
            check=False,
            timeout=args.timeout,
            shell=False,
        ).returncode
    except subprocess.TimeoutExpired:
        print(
            "Core CLI workflow timed out; partial data and reports are preserved.",
            file=sys.stderr,
        )
        return 124


if __name__ == "__main__":
    raise SystemExit(main())
