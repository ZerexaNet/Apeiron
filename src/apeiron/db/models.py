"""Typed records stored in the Apeiron SQLite database."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

GameType = Literal["java", "bedrock"]
AccountKind = Literal["microsoft", "offline"]
LoaderKind = Literal["vanilla", "fabric", "forge", "neoforge", "quilt"]


def new_id() -> str:
    return uuid.uuid4().hex


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Instance:
    """A launchable game instance (one directory, one configuration)."""

    id: str = field(default_factory=new_id)
    name: str = ""
    game_type: GameType = "java"
    mc_version: str = ""
    loader: LoaderKind = "vanilla"
    loader_version: str = ""
    account_id: str | None = None
    java_path: str = ""
    java_major: int = 0
    memory_mb: int = 4096
    jvm_args: str = ""
    game_args: str = ""
    width: int = 854
    height: int = 480
    fullscreen: bool = False
    directory: str = ""
    icon: str = ""
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    last_played_at: str | None = None
    play_count: int = 0
    total_play_seconds: int = 0
    installed: bool = False

    @property
    def display_loader(self) -> str:
        if self.loader == "vanilla" or not self.loader_version:
            return "Vanilla"
        return f"{self.loader.capitalize()} {self.loader_version}"

    def to_row(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "game_type": self.game_type,
            "mc_version": self.mc_version,
            "loader": self.loader,
            "loader_version": self.loader_version,
            "account_id": self.account_id,
            "java_path": self.java_path,
            "java_major": self.java_major,
            "memory_mb": self.memory_mb,
            "jvm_args": self.jvm_args,
            "game_args": self.game_args,
            "width": self.width,
            "height": self.height,
            "fullscreen": int(self.fullscreen),
            "directory": self.directory,
            "icon": self.icon,
            "tags": json.dumps(self.tags, ensure_ascii=False),
            "metadata": json.dumps(self.metadata, ensure_ascii=False),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_played_at": self.last_played_at,
            "play_count": self.play_count,
            "total_play_seconds": self.total_play_seconds,
            "installed": int(self.installed),
        }

    @classmethod
    def from_row(cls, row: Any) -> Instance:
        def _json(value: Any, fallback: Any) -> Any:
            if not value:
                return fallback
            try:
                return json.loads(value)
            except (TypeError, json.JSONDecodeError):
                return fallback

        return cls(
            id=row["id"],
            name=row["name"],
            game_type=row["game_type"],
            mc_version=row["mc_version"],
            loader=row["loader"],
            loader_version=row["loader_version"] or "",
            account_id=row["account_id"],
            java_path=row["java_path"] or "",
            java_major=row["java_major"] or 0,
            memory_mb=row["memory_mb"],
            jvm_args=row["jvm_args"] or "",
            game_args=row["game_args"] or "",
            width=row["width"] or 854,
            height=row["height"] or 480,
            fullscreen=bool(row["fullscreen"]),
            directory=row["directory"],
            icon=row["icon"] or "",
            tags=_json(row["tags"], []),
            metadata=_json(row["metadata"], {}),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_played_at=row["last_played_at"],
            play_count=row["play_count"] or 0,
            total_play_seconds=row["total_play_seconds"] or 0,
            installed=bool(row["installed"]),
        )


@dataclass
class Account:
    """Metadata for an authenticated or local identity.

    No credential is ever stored here: tokens live in the OS credential
    store and are referenced by ``credential_ref``.
    """

    id: str = field(default_factory=new_id)
    kind: AccountKind = "offline"
    name: str = ""
    uuid: str = ""
    microsoft_id: str = ""
    skin_model: str = "classic"
    verified: bool = False
    credential_ref: str = ""
    created_at: str = field(default_factory=utc_now)
    last_used_at: str | None = None

    def to_row(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "name": self.name,
            "uuid": self.uuid,
            "microsoft_id": self.microsoft_id,
            "skin_model": self.skin_model,
            "verified": int(self.verified),
            "credential_ref": self.credential_ref,
            "created_at": self.created_at,
            "last_used_at": self.last_used_at,
        }

    @classmethod
    def from_row(cls, row: Any) -> Account:
        return cls(
            id=row["id"],
            kind=row["kind"],
            name=row["name"],
            uuid=row["uuid"],
            microsoft_id=row["microsoft_id"] or "",
            skin_model=row["skin_model"] or "classic",
            verified=bool(row["verified"]),
            credential_ref=row["credential_ref"] or "",
            created_at=row["created_at"],
            last_used_at=row["last_used_at"],
        )


@dataclass
class DownloadTask:
    id: str = field(default_factory=new_id)
    url: str = ""
    destination: str = ""
    expected_sha1: str = ""
    expected_size: int = 0
    status: str = "pending"  # pending|running|completed|failed|cancelled
    bytes_done: int = 0
    bytes_total: int = 0
    attempts: int = 0
    error: str = ""
    category: str = "other"
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def to_row(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "url": self.url,
            "destination": self.destination,
            "expected_sha1": self.expected_sha1,
            "expected_size": self.expected_size,
            "status": self.status,
            "bytes_done": self.bytes_done,
            "bytes_total": self.bytes_total,
            "attempts": self.attempts,
            "error": self.error,
            "category": self.category,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_row(cls, row: Any) -> DownloadTask:
        return cls(
            id=row["id"],
            url=row["url"],
            destination=row["destination"],
            expected_sha1=row["expected_sha1"] or "",
            expected_size=row["expected_size"] or 0,
            status=row["status"],
            bytes_done=row["bytes_done"] or 0,
            bytes_total=row["bytes_total"] or 0,
            attempts=row["attempts"] or 0,
            error=row["error"] or "",
            category=row["category"] or "other",
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


@dataclass
class ModRecord:
    id: str = field(default_factory=new_id)
    instance_id: str = ""
    filename: str = ""
    kind: str = "mod"  # mod|resourcepack|shaderpack|datapack
    source: str = ""  # modrinth|curseforge|manual
    project_id: str = ""
    version_id: str = ""
    enabled: bool = True
    sha1: str = ""
    sha512: str = ""
    size: int = 0
    source_url: str = ""
    installed_at: str = field(default_factory=utc_now)

    def to_row(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "instance_id": self.instance_id,
            "filename": self.filename,
            "kind": self.kind,
            "source": self.source,
            "project_id": self.project_id,
            "version_id": self.version_id,
            "enabled": int(self.enabled),
            "sha1": self.sha1,
            "sha512": self.sha512,
            "size": self.size,
            "source_url": self.source_url,
            "installed_at": self.installed_at,
        }

    @classmethod
    def from_row(cls, row: Any) -> ModRecord:
        return cls(
            id=row["id"],
            instance_id=row["instance_id"],
            filename=row["filename"],
            kind=row["kind"],
            source=row["source"] or "",
            project_id=row["project_id"] or "",
            version_id=row["version_id"] or "",
            enabled=bool(row["enabled"]),
            sha1=row["sha1"] or "",
            sha512=row["sha512"] or "",
            size=row["size"] or 0,
            source_url=row["source_url"] or "",
            installed_at=row["installed_at"],
        )


@dataclass
class LaunchRecord:
    id: str = field(default_factory=new_id)
    instance_id: str = ""
    started_at: str = field(default_factory=utc_now)
    ended_at: str | None = None
    exit_code: int | None = None
    status: str = "running"  # running|exited|crashed|cancelled
    log_path: str = ""
    duration_seconds: int = 0
