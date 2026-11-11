"""Check profiles and argument-list subprocesses without pretending to run Docker."""

import importlib.util
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[3]


def test_profiles_and_health_dependencies():
    value = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = value["services"]
    assert {name for name, svc in services.items() if not svc.get("profiles")} == {"postgres"}
    for name in ("backend", "frontend"):
        assert services[name]["profiles"] == ["app"]
    for name in ("bootstrap", "reproduce"):
        assert services[name]["profiles"] == ["tools"]
        assert services[name]["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert services["bootstrap"]["command"][5] == "bootstrap"
    assert services["reproduce"]["command"][:3] == [
        "python",
        "scripts/reproduce.py",
        "--skip-database-start",
    ]
    assert services["backend"]["depends_on"]["postgres"]["condition"] == "service_healthy"


def test_reproduction_uses_argument_vector_and_propagates_failure():
    spec = importlib.util.spec_from_file_location("reproduction", ROOT / "scripts/reproduce.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with patch("subprocess.run") as call:
        module.run(
            ["python", "-m", "apex_rag.cli", "reproduce", "--config", "space path.yaml"], ROOT, 10
        )
        assert call.call_args.kwargs["shell"] is False and call.call_args.kwargs["check"] is True
        assert call.call_args.args[0][-1] == "space path.yaml"
    with patch("subprocess.run", side_effect=subprocess.CalledProcessError(4, ["python"])):
        try:
            module.run(["python"], ROOT, 10)
        except subprocess.CalledProcessError as error:
            assert error.returncode == 4
        else:
            raise AssertionError("Failure swallowed")


def test_wrapper_evidence_uses_isolated_reports_environment(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "isolated_reproduction", ROOT / "scripts/reproduce.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with (
        patch.dict("os.environ", {"APEX_REPORTS_DIR": str(tmp_path)}),
        patch("sys.argv", ["reproduce.py", "--skip-database-start"]),
        patch.object(module, "run") as call,
    ):
        assert module.main() == 0
    record = json.loads((tmp_path / "bonus-runs/reproduction-command.json").read_text())
    assert record["status"] == "passed" and len(record["steps"]) == 1
    assert call.call_args.args[0][1:4] == ["-m", "apex_rag.cli", "reproduce"]
