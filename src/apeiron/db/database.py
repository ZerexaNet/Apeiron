"""SQLite connection management and schema migrations.

Migrations are ordered, append-only and recorded in ``PRAGMA user_version``.
Each migration runs inside a transaction; a failure leaves the database at the
last good version so that a partially applied upgrade cannot corrupt data.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterable
from pathlib import Path

from apeiron.core.errors import DatabaseError
from apeiron.core.logging import get_logger

log = get_logger("database")

# Each entry is (version, statements).  Never edit a shipped migration; add a
# new one instead.
MIGRATIONS: list[tuple[int, tuple[str, ...]]] = [
    (
        1,
        (
            """
            CREATE TABLE instances (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                game_type TEXT NOT NULL DEFAULT 'java',
                mc_version TEXT NOT NULL,
                loader TEXT NOT NULL DEFAULT 'vanilla',
                loader_version TEXT,
                account_id TEXT,
                java_path TEXT,
                java_major INTEGER DEFAULT 0,
                memory_mb INTEGER DEFAULT 4096,
                jvm_args TEXT DEFAULT '',
                game_args TEXT DEFAULT '',
                width INTEGER DEFAULT 854,
                height INTEGER DEFAULT 480,
                fullscreen INTEGER DEFAULT 0,
                directory TEXT NOT NULL,
                icon TEXT DEFAULT '',
                tags TEXT DEFAULT '[]',
                metadata TEXT DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_played_at TEXT,
                play_count INTEGER DEFAULT 0,
                total_play_seconds INTEGER DEFAULT 0,
                installed INTEGER DEFAULT 0
            )
            """,
            "CREATE UNIQUE INDEX idx_instances_name ON instances(name)",
            "CREATE INDEX idx_instances_last_played ON instances(last_played_at DESC)",
            """
            CREATE TABLE accounts (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                name TEXT NOT NULL,
                uuid TEXT NOT NULL,
                microsoft_id TEXT DEFAULT '',
                skin_model TEXT DEFAULT 'classic',
                verified INTEGER NOT NULL DEFAULT 0,
                credential_ref TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                last_used_at TEXT
            )
            """,
            "CREATE UNIQUE INDEX idx_accounts_uuid ON accounts(uuid)",
            """
            CREATE TABLE download_tasks (
                id TEXT PRIMARY KEY,
                url TEXT NOT NULL,
                destination TEXT NOT NULL,
                expected_sha1 TEXT DEFAULT '',
                expected_size INTEGER DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'pending',
                bytes_done INTEGER DEFAULT 0,
                bytes_total INTEGER DEFAULT 0,
                attempts INTEGER DEFAULT 0,
                error TEXT DEFAULT '',
                category TEXT DEFAULT 'other',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            "CREATE INDEX idx_download_status ON download_tasks(status)",
            """
            CREATE TABLE instance_mods (
                id TEXT PRIMARY KEY,
                instance_id TEXT NOT NULL REFERENCES instances(id) ON DELETE CASCADE,
                filename TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'mod',
                source TEXT DEFAULT '',
                project_id TEXT DEFAULT '',
                version_id TEXT DEFAULT '',
                enabled INTEGER NOT NULL DEFAULT 1,
                sha1 TEXT DEFAULT '',
                size INTEGER DEFAULT 0,
                installed_at TEXT NOT NULL
            )
            """,
            "CREATE INDEX idx_mods_instance ON instance_mods(instance_id)",
            """
            CREATE TABLE launches (
                id TEXT PRIMARY KEY,
                instance_id TEXT NOT NULL REFERENCES instances(id) ON DELETE CASCADE,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                exit_code INTEGER,
                status TEXT NOT NULL DEFAULT 'running',
                log_path TEXT DEFAULT '',
                duration_seconds INTEGER DEFAULT 0
            )
            """,
            "CREATE INDEX idx_launches_instance ON launches(instance_id, started_at DESC)",
            """
            CREATE TABLE app_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
        ),
    ),
    (
        2,
        (
            "ALTER TABLE instances ADD COLUMN notes TEXT DEFAULT ''",
            "CREATE TABLE instance_files ("
            " id TEXT PRIMARY KEY,"
            " instance_id TEXT NOT NULL REFERENCES instances(id) ON DELETE CASCADE,"
            " path TEXT NOT NULL, sha1 TEXT DEFAULT '', size INTEGER DEFAULT 0,"
            " updated_at TEXT NOT NULL)",
            "CREATE INDEX idx_files_instance ON instance_files(instance_id)",
        ),
    ),
    (
        3,
        (
            # Needed to export a redistributable .mrpack: the URL lets the
            # receiving launcher download the file instead of re-hosting it.
            "ALTER TABLE instance_mods ADD COLUMN source_url TEXT DEFAULT ''",
            "ALTER TABLE instance_mods ADD COLUMN sha512 TEXT DEFAULT ''",
        ),
    ),
]

LATEST_VERSION = MIGRATIONS[-1][0]


class Database:
    """Thread-confined SQLite wrapper (one connection per thread)."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._write_lock = threading.RLock()

    # -- connection ---------------------------------------------------------
    @property
    def connection(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._connect()
            self._local.conn = conn
        return conn

    def _connect(self) -> sqlite3.Connection:
        try:
            conn = sqlite3.connect(
                str(self.path), timeout=15.0, isolation_level=None, check_same_thread=False
            )
        except sqlite3.Error as exc:
            raise DatabaseError(f"cannot open database {self.path}", detail=str(exc)) from exc
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=15000")
        return conn

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    # -- migrations ---------------------------------------------------------
    def current_version(self) -> int:
        return int(self.connection.execute("PRAGMA user_version").fetchone()[0])

    def migrate(self) -> int:
        """Apply pending migrations and return the resulting version."""
        with self._write_lock:
            version = self.current_version()
            if version > LATEST_VERSION:
                raise DatabaseError(
                    "database was created by a newer Apeiron build",
                    detail=f"found schema {version}, this build supports {LATEST_VERSION}",
                )
            for target, statements in MIGRATIONS:
                if target <= version:
                    continue
                log.info("applying database migration %d", target)
                conn = self.connection
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    for statement in statements:
                        conn.execute(statement)
                    conn.execute(f"PRAGMA user_version={target}")
                    conn.execute("COMMIT")
                except sqlite3.Error as exc:
                    conn.execute("ROLLBACK")
                    raise DatabaseError(f"migration {target} failed", detail=str(exc)) from exc
                version = target
            return version

    # -- helpers ------------------------------------------------------------
    def execute(self, sql: str, params: Iterable[object] = ()) -> sqlite3.Cursor:
        with self._write_lock:
            return self.connection.execute(sql, tuple(params))

    def query(self, sql: str, params: Iterable[object] = ()) -> list[sqlite3.Row]:
        return list(self.connection.execute(sql, tuple(params)).fetchall())

    def query_one(self, sql: str, params: Iterable[object] = ()) -> sqlite3.Row | None:
        return self.connection.execute(sql, tuple(params)).fetchone()

    def transaction(self):
        """Context manager for a write transaction."""
        return _Transaction(self)

    def integrity_check(self) -> bool:
        row = self.connection.execute("PRAGMA integrity_check").fetchone()
        return bool(row) and row[0] == "ok"


class _Transaction:
    def __init__(self, db: Database) -> None:
        self.db = db

    def __enter__(self) -> Database:
        self.db._write_lock.acquire()
        self.db.connection.execute("BEGIN IMMEDIATE")
        return self.db

    def __exit__(self, exc_type, exc, tb) -> None:
        conn = self.db.connection
        try:
            if exc_type is None:
                conn.execute("COMMIT")
            else:
                conn.execute("ROLLBACK")
        finally:
            self.db._write_lock.release()
