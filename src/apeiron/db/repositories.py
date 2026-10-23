"""Repositories over the Apeiron schema.

Repositories contain SQL only; business rules (filesystem isolation, name
uniqueness across the UI, launch validation) live in the service layer so
they can be tested without a database.
"""

from __future__ import annotations

import sqlite3
from dataclasses import fields
from typing import Any

from apeiron.core.errors import AccountError, DatabaseError, InstanceNotFoundError
from apeiron.db.database import Database
from apeiron.db.models import (
    Account,
    DownloadTask,
    Instance,
    LaunchRecord,
    ModRecord,
    new_id,
    utc_now,
)


class InstanceRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def list(self) -> list[Instance]:
        rows = self.db.query(
            "SELECT * FROM instances ORDER BY COALESCE(last_played_at, created_at) DESC"
        )
        return [Instance.from_row(r) for r in rows]

    def get(self, instance_id: str) -> Instance:
        row = self.db.query_one("SELECT * FROM instances WHERE id = ?", (instance_id,))
        if row is None:
            raise InstanceNotFoundError(f"instance not found: {instance_id}")
        return Instance.from_row(row)

    def exists(self, instance_id: str) -> bool:
        return self.db.query_one("SELECT 1 FROM instances WHERE id = ?", (instance_id,)) is not None

    def find_by_name(self, name: str) -> Instance | None:
        row = self.db.query_one("SELECT * FROM instances WHERE name = ?", (name,))
        return Instance.from_row(row) if row else None

    def add(self, instance: Instance) -> Instance:
        instance.updated_at = utc_now()
        row = instance.to_row()
        columns = ", ".join(row)
        placeholders = ", ".join("?" for _ in row)
        try:
            self.db.execute(
                f"INSERT INTO instances ({columns}) VALUES ({placeholders})", row.values()
            )
        except sqlite3.IntegrityError as exc:
            raise DatabaseError(
                "an instance with this name already exists", detail=str(exc)
            ) from exc
        return instance

    def update(self, instance: Instance) -> Instance:
        instance.updated_at = utc_now()
        row = instance.to_row()
        row.pop("id")
        assignments = ", ".join(f"{key} = ?" for key in row)
        try:
            self.db.execute(
                f"UPDATE instances SET {assignments} WHERE id = ?",
                (*row.values(), instance.id),
            )
        except sqlite3.IntegrityError as exc:
            raise DatabaseError(
                "an instance with this name already exists", detail=str(exc)
            ) from exc
        return instance

    def delete(self, instance_id: str) -> None:
        self.db.execute("DELETE FROM instances WHERE id = ?", (instance_id,))

    def mark_played(self, instance_id: str, seconds: int) -> None:
        now = utc_now()
        self.db.execute(
            "UPDATE instances SET last_played_at = ?, play_count = play_count + 1,"
            " total_play_seconds = total_play_seconds + ?, updated_at = ? WHERE id = ?",
            (now, seconds, now, instance_id),
        )

    def set_installed(self, instance_id: str, installed: bool) -> None:
        self.db.execute(
            "UPDATE instances SET installed = ?, updated_at = ? WHERE id = ?",
            (int(installed), utc_now(), instance_id),
        )

    def unique_name(self, base: str) -> str:
        base = base.strip() or "Instance"
        candidate = base
        index = 2
        while self.find_by_name(candidate) is not None:
            candidate = f"{base} ({index})"
            index += 1
        return candidate


class AccountRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def list(self) -> list[Account]:
        rows = self.db.query(
            "SELECT * FROM accounts ORDER BY last_used_at IS NULL, last_used_at DESC, created_at"
        )
        return [Account.from_row(r) for r in rows]

    def get(self, account_id: str) -> Account:
        row = self.db.query_one("SELECT * FROM accounts WHERE id = ?", (account_id,))
        if row is None:
            raise AccountError(f"account not found: {account_id}")
        return Account.from_row(row)

    def find_by_uuid(self, uuid: str) -> Account | None:
        row = self.db.query_one("SELECT * FROM accounts WHERE uuid = ?", (uuid,))
        return Account.from_row(row) if row else None

    def add(self, account: Account) -> Account:
        row = account.to_row()
        try:
            self.db.execute(
                f"INSERT INTO accounts ({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})",
                row.values(),
            )
        except sqlite3.IntegrityError as exc:
            raise AccountError("this account is already added", detail=str(exc)) from exc
        return account

    def update(self, account: Account) -> Account:
        row = account.to_row()
        row.pop("id")
        assignments = ", ".join(f"{key} = ?" for key in row)
        self.db.execute(
            f"UPDATE accounts SET {assignments} WHERE id = ?", (*row.values(), account.id)
        )
        return account

    def delete(self, account_id: str) -> None:
        self.db.execute(
            "UPDATE instances SET account_id = NULL WHERE account_id = ?", (account_id,)
        )
        self.db.execute("DELETE FROM accounts WHERE id = ?", (account_id,))

    def touch(self, account_id: str) -> None:
        self.db.execute(
            "UPDATE accounts SET last_used_at = ? WHERE id = ?", (utc_now(), account_id)
        )

    def count_by_kind(self, kind: str) -> int:
        row = self.db.query_one("SELECT COUNT(*) AS n FROM accounts WHERE kind = ?", (kind,))
        return int(row["n"]) if row else 0


class DownloadRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def upsert(self, task: DownloadTask) -> DownloadTask:
        task.updated_at = utc_now()
        row = task.to_row()
        columns = ", ".join(row)
        placeholders = ", ".join("?" for _ in row)
        updates = ", ".join(f"{k} = excluded.{k}" for k in row if k != "id")
        self.db.execute(
            f"INSERT INTO download_tasks ({columns}) VALUES ({placeholders})"
            f" ON CONFLICT(id) DO UPDATE SET {updates}",
            row.values(),
        )
        return task

    def get(self, task_id: str) -> DownloadTask | None:
        row = self.db.query_one("SELECT * FROM download_tasks WHERE id = ?", (task_id,))
        return DownloadTask.from_row(row) if row else None

    def list(self, status: str | None = None, limit: int = 200) -> list[DownloadTask]:
        if status:
            rows = self.db.query(
                "SELECT * FROM download_tasks WHERE status = ? ORDER BY updated_at DESC LIMIT ?",
                (status, limit),
            )
        else:
            rows = self.db.query(
                "SELECT * FROM download_tasks ORDER BY updated_at DESC LIMIT ?", (limit,)
            )
        return [DownloadTask.from_row(r) for r in rows]

    def clear_finished(self) -> int:
        cursor = self.db.execute(
            "DELETE FROM download_tasks WHERE status IN ('completed', 'cancelled')"
        )
        return cursor.rowcount

    def mark_interrupted_as_failed(self) -> int:
        """Called at startup: nothing can be running before the app starts."""
        cursor = self.db.execute(
            "UPDATE download_tasks SET status = 'failed', error = ?, updated_at = ?"
            " WHERE status IN ('running', 'pending')",
            ("interrupted by application exit", utc_now()),
        )
        return cursor.rowcount


class ModRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def list_for_instance(self, instance_id: str, kind: str | None = None) -> list[ModRecord]:
        if kind:
            rows = self.db.query(
                "SELECT * FROM instance_mods WHERE instance_id = ? AND kind = ? ORDER BY filename",
                (instance_id, kind),
            )
        else:
            rows = self.db.query(
                "SELECT * FROM instance_mods WHERE instance_id = ? ORDER BY kind, filename",
                (instance_id,),
            )
        return [ModRecord.from_row(r) for r in rows]

    def add(self, record: ModRecord) -> ModRecord:
        row = record.to_row()
        self.db.execute(
            f"INSERT INTO instance_mods ({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})",
            row.values(),
        )
        return record

    def delete(self, record_id: str) -> None:
        self.db.execute("DELETE FROM instance_mods WHERE id = ?", (record_id,))

    def delete_by_filename(self, instance_id: str, filename: str) -> None:
        self.db.execute(
            "DELETE FROM instance_mods WHERE instance_id = ? AND filename = ?",
            (instance_id, filename),
        )

    def set_enabled(self, record_id: str, enabled: bool) -> None:
        self.db.execute(
            "UPDATE instance_mods SET enabled = ? WHERE id = ?", (int(enabled), record_id)
        )


class LaunchRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def start(self, instance_id: str, log_path: str) -> LaunchRecord:
        record = LaunchRecord(instance_id=instance_id, log_path=log_path)
        self.db.execute(
            "INSERT INTO launches (id, instance_id, started_at, status, log_path)"
            " VALUES (?, ?, ?, ?, ?)",
            (record.id, record.instance_id, record.started_at, record.status, record.log_path),
        )
        return record

    def finish(self, launch_id: str, exit_code: int | None, status: str, seconds: int) -> None:
        self.db.execute(
            "UPDATE launches SET ended_at = ?, exit_code = ?, status = ?, duration_seconds = ?"
            " WHERE id = ?",
            (utc_now(), exit_code, status, seconds, launch_id),
        )

    def recent(self, limit: int = 20) -> list[LaunchRecord]:
        rows = self.db.query("SELECT * FROM launches ORDER BY started_at DESC LIMIT ?", (limit,))
        result: list[LaunchRecord] = []
        for row in rows:
            result.append(
                LaunchRecord(
                    id=row["id"],
                    instance_id=row["instance_id"],
                    started_at=row["started_at"],
                    ended_at=row["ended_at"],
                    exit_code=row["exit_code"],
                    status=row["status"],
                    log_path=row["log_path"] or "",
                    duration_seconds=row["duration_seconds"] or 0,
                )
            )
        return result


class AppStateRepository:
    """Small key/value store for UI state that must survive restarts."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def get(self, key: str, default: str = "") -> str:
        row = self.db.query_one("SELECT value FROM app_state WHERE key = ?", (key,))
        return row["value"] if row else default

    def set(self, key: str, value: str) -> None:
        self.db.execute(
            "INSERT INTO app_state (key, value, updated_at) VALUES (?, ?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value,"
            " updated_at = excluded.updated_at",
            (key, value, utc_now()),
        )


def blank_instance(name: str) -> Instance:
    """Helper used by the UI when composing a new instance."""
    return Instance(id=new_id(), name=name)


def instance_fields() -> list[str]:
    return [f.name for f in fields(Instance)]


__all__ = [
    "AccountRepository",
    "Any",
    "AppStateRepository",
    "DownloadRepository",
    "InstanceRepository",
    "LaunchRepository",
    "ModRepository",
    "blank_instance",
    "instance_fields",
]
