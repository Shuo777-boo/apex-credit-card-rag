"""Root env loading and override priority without reading or writing actual secrets."""

import os
import subprocess
import sys


def test_root_env_loaded_from_arbitrary_cwd_and_environment_wins(tmp_path):
    (tmp_path / ".env").write_text("APEX_EMBEDDING_DIMENSION=512\nDASHSCOPE_API_KEY=fixture-only\n", encoding="utf-8")
    child = tmp_path / "nested"
    child.mkdir()
    environment = {k: v for k, v in os.environ.items() if not k.startswith("APEX_") and k != "DASHSCOPE_API_KEY"}
    environment["APEX_PROJECT_ROOT"] = str(tmp_path)
    code = "from apex_rag.core.config import Settings; s=Settings(); print(s.embedding_dimension, bool(s.dashscope_api_key.get_secret_value()))"
    result = subprocess.run([sys.executable, "-c", code], cwd=child, env=environment, text=True, capture_output=True, check=True)
    assert result.stdout.strip() == "512 True"
    environment["APEX_EMBEDDING_DIMENSION"] = "768"
    result = subprocess.run([sys.executable, "-c", code], cwd=child, env=environment, text=True, capture_output=True, check=True)
    assert result.stdout.strip() == "768 True"


def test_packaged_migrations_are_available():
    from pathlib import Path
    import apex_rag
    root = Path(apex_rag.__file__).parent
    migrations = sorted((root / "storage" / "migrations").glob("*.sql"))
    assert [path.name for path in migrations] == ["001_core.sql", "002_evaluation_jobs.sql"]
    assert all(path.read_text(encoding="utf-8").strip() for path in migrations)


def test_localhost_pool_uses_ipv4_docker_binding():
    from apex_rag.core.config import Settings
    from apex_rag.storage.db import Database
    from psycopg.conninfo import conninfo_to_dict
    from pydantic import SecretStr
    db = Database(Settings(database_url=SecretStr("postgresql://fixture:fixture@localhost:55434/fixture")))
    assert conninfo_to_dict(db.pool.conninfo)["host"] == "127.0.0.1"
    db.close()
