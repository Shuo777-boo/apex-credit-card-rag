"""Psycopg 3 pool, versioned transactional migrations and vector adapters."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import psycopg
from pgvector.psycopg import register_vector
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from apex_rag.core.config import Settings
from apex_rag.core.errors import ApexError


class Database:
    def __init__(self, settings: Settings) -> None:
        """Create a closed pool; opening acquires no model credits or implicit migration."""
        self.settings = settings
        dsn = settings.database_url.get_secret_value()
        # Docker port bindings are IPv4 loopback. On Windows localhost can try ::1 first
        # and exhaust the pool acquisition deadline before its IPv4 fallback connects.
        if conninfo_to_dict(dsn).get("host") == "localhost":
            dsn = make_conninfo(dsn, host="127.0.0.1")
        self.pool = ConnectionPool(dsn, min_size=settings.pool_min_size,
            max_size=settings.pool_max_size, open=False, timeout=5, kwargs={"row_factory": dict_row,
                "connect_timeout": 5}, configure=self._configure)
        self.opened = False

    @staticmethod
    def _configure(conn: psycopg.Connection[Any]) -> None:
        # Migration can create extension before the first pooled connection is acquired.
        register_vector(conn)
        conn.commit()

    @contextmanager
    def connection(self) -> Iterator[psycopg.Connection[Any]]:
        """Yield a transactional pooled connection; commit on success, rollback on error.

        Acquisition/connect failures become safe DATABASE_UNAVAILABLE errors. SQL errors
        remain typed Psycopg exceptions for API/CLI handlers; credentials are never echoed.
        """
        try:
            if not self.opened:
                self.pool.open(wait=False)
                self.opened = True
            with self.pool.connection() as conn:
                yield conn
        except (psycopg.OperationalError, psycopg_pool_timeout()):
            raise ApexError("DATABASE_UNAVAILABLE", "PostgreSQL connection is unavailable") from None

    def migrate(self) -> list[str]:
        """Apply nonempty packaged SQL migrations once under a transaction advisory lock.

        Returns applied version names; does not import data, vectorize, or activate builds.
        Missing resources and failed database changes fail explicitly without partial commit.
        """
        applied: list[str] = []
        try:
            with psycopg.connect(self.settings.database_url.get_secret_value(), connect_timeout=5) as conn:
                conn.execute("SELECT pg_advisory_xact_lock(741938001)")
                conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations(version text PRIMARY KEY, applied_at timestamptz DEFAULT now())")
                directory = Path(__file__).parent / "migrations"
                migrations = sorted(directory.glob("*.sql"))
                if not migrations:
                    raise ApexError("MIGRATIONS_MISSING", "No packaged SQL migrations found", 500)
                for path in migrations:
                    if not conn.execute("SELECT 1 FROM schema_migrations WHERE version=%s", (path.stem,)).fetchone():
                        conn.execute(path.read_text(encoding="utf-8"))
                        conn.execute("INSERT INTO schema_migrations(version) VALUES(%s)", (path.stem,))
                        applied.append(path.stem)
        except psycopg.Error:
            raise ApexError("DATABASE_UNAVAILABLE", "Migration failed; verify PostgreSQL, privileges and pgvector") from None
        return applied

    def close(self) -> None:
        if self.opened:
            self.pool.close()


def psycopg_pool_timeout() -> type[Exception]:
    from psycopg_pool import PoolTimeout

    return PoolTimeout
