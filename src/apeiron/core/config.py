"""User settings: schema, validation, atomic persistence and migration.

Settings live in ``<root>/data/settings.json``.  Unknown keys are preserved on
save so that a newer build's options survive a downgrade, and every value is
validated on load: a corrupt or hand-edited file degrades to defaults instead
of crashing the launcher.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from apeiron.core.errors import ConfigError
from apeiron.core.logging import get_logger

log = get_logger("config")

SCHEMA_VERSION = 2
SUPPORTED_LANGUAGES = ("system", "zh_CN", "en_US")
SUPPORTED_THEMES = ("system", "light", "dark")

MEMORY_MIN_MB = 512
MEMORY_MAX_MB = 65536
CONCURRENCY_MIN = 1
CONCURRENCY_MAX = 16


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


@dataclass
class Settings:
    """Persisted user preferences (not secrets)."""

    schema_version: int = SCHEMA_VERSION

    # appearance / language
    language: str = "system"
    theme: str = "system"
    accent: str = "blue"

    # window state
    window_width: int = 1200
    window_height: int = 800

    # downloads
    max_concurrent_downloads: int = 4
    download_timeout: float = 30.0
    download_retries: int = 3

    # java / launch defaults
    java_path: str = ""
    default_memory_mb: int = 4096
    default_jvm_args: str = ""
    default_game_args: str = ""
    close_game_on_launcher_exit: bool = False

    # behaviour
    check_updates: bool = True
    update_channel: str = "stable"
    show_snapshots: bool = False
    confirm_before_delete: bool = True
    log_level: str = "INFO"
    last_instance_id: str = ""

    # bookkeeping for keys written by newer builds
    extra: dict[str, Any] = field(default_factory=dict, repr=False)

    # -- validation ---------------------------------------------------------
    def normalised(self) -> Settings:
        """Return a copy with every field inside its supported range."""
        clone = Settings(**{f.name: getattr(self, f.name) for f in fields(Settings)})
        clone.extra = dict(self.extra)
        if clone.language not in SUPPORTED_LANGUAGES:
            clone.language = "system"
        if clone.theme not in SUPPORTED_THEMES:
            clone.theme = "system"
        clone.max_concurrent_downloads = _clamp(
            int(clone.max_concurrent_downloads), CONCURRENCY_MIN, CONCURRENCY_MAX
        )
        clone.default_memory_mb = _clamp(int(clone.default_memory_mb), MEMORY_MIN_MB, MEMORY_MAX_MB)
        clone.download_timeout = float(max(5.0, min(300.0, float(clone.download_timeout))))
        clone.download_retries = _clamp(int(clone.download_retries), 0, 10)
        clone.window_width = _clamp(int(clone.window_width), 960, 8000)
        clone.window_height = _clamp(int(clone.window_height), 640, 8000)
        clone.log_level = clone.log_level.upper()
        if clone.log_level not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            clone.log_level = "INFO"
        if clone.update_channel not in ("stable", "beta"):
            clone.update_channel = "stable"
        return clone

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("extra", None)
        data.update(self.extra)
        return data


class SettingsStore:
    """Loads and saves :class:`Settings` with crash-safe writes."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._settings = Settings()

    @property
    def current(self) -> Settings:
        return self._settings

    def load(self) -> Settings:
        if not self.path.exists():
            self._settings = Settings()
            return self._settings
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            # Keep the broken file for diagnosis rather than silently deleting
            # user data, then continue with defaults.
            log.error("settings unreadable (%s); falling back to defaults", exc)
            try:
                self.path.with_suffix(".json.broken").write_bytes(self.path.read_bytes())
            except OSError:
                pass
            self._settings = Settings()
            return self._settings

        if not isinstance(raw, dict):
            log.error("settings root is not an object; using defaults")
            self._settings = Settings()
            return self._settings

        raw = migrate(dict(raw))
        known = {f.name for f in fields(Settings)} - {"extra"}
        values: dict[str, Any] = {}
        unknown: dict[str, Any] = {}
        for key, value in raw.items():
            if key in known:
                values[key] = value
            else:
                unknown[key] = value
        settings = Settings(**{k: v for k, v in values.items() if k != "schema_version"})
        settings.extra = unknown
        settings.schema_version = SCHEMA_VERSION
        self._settings = settings.normalised()
        return self._settings

    def save(self, settings: Settings | None = None) -> None:
        if settings is not None:
            self._settings = settings.normalised()
        payload = json.dumps(self._settings.to_dict(), indent=2, ensure_ascii=False) + "\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            _atomic_write(self.path, payload.encode("utf-8"))
        except OSError as exc:
            raise ConfigError(f"cannot save settings to {self.path}", detail=str(exc)) from exc

    def update(self, **changes: Any) -> Settings:
        merged = self._settings.to_dict()
        merged.update(changes)
        known = {f.name for f in fields(Settings)} - {"extra"}
        values = {k: v for k, v in merged.items() if k in known}
        extra = {k: v for k, v in merged.items() if k not in known}
        settings = Settings(**{k: v for k, v in values.items() if k != "schema_version"})
        settings.extra = extra
        settings.schema_version = SCHEMA_VERSION
        self.save(settings)
        return self._settings


def _atomic_write(path: Path, data: bytes) -> None:
    """Write ``data`` then rename, so a crash cannot truncate settings."""
    directory = path.parent
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(directory))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if os.name == "posix":
            os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def migrate(raw: dict[str, Any]) -> dict[str, Any]:
    """Upgrade a settings mapping in place and return it.

    Version 1 stored ``lang`` and ``dark_mode``; version 2 introduced the
    tri-state ``language`` (``system``) and ``theme``.
    """
    version = int(raw.get("schema_version", 1) or 1)
    if version < 2:
        lang = raw.pop("lang", None)
        if lang in ("zh_CN", "en_US"):
            raw["language"] = lang
        else:
            raw.setdefault("language", "system")
        if "dark_mode" in raw:
            raw["theme"] = "dark" if raw.pop("dark_mode") else "light"
    raw["schema_version"] = SCHEMA_VERSION
    return raw
