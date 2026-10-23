"""Minecraft version metadata: parsing, rule evaluation and inheritance.

Mojang's version JSON is a moving target: legacy versions use
``minecraftArguments`` while modern ones use structured ``arguments`` arrays
with rules, and mod loaders express themselves as *child* versions that
``inheritsFrom`` a vanilla parent.  This module models that faithfully so the
launcher never hard-codes one launch command for every version.
"""

from __future__ import annotations

import json
import platform
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from apeiron.core.errors import ConfigError, UnsupportedError
from apeiron.core.paths import current_platform_key

# Keys merged by appending (parent first) rather than replacing.
_LIST_KEYS = ("libraries", "game", "jvm")


@dataclass(frozen=True)
class Rule:
    action: str = "allow"
    os_name: str | None = None
    os_version: str | None = None
    os_arch: str | None = None
    features: Mapping[str, bool] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> Rule:
        os_block = raw.get("os") or {}
        arch = os_block.get("arch")
        if arch == "x86":
            arch = "x86"
        return cls(
            action=str(raw.get("action", "allow")),
            os_name=os_block.get("name"),
            os_version=os_block.get("version"),
            os_arch=arch,
            features=dict(raw.get("features") or {}),
        )


def _os_matches(rule: Rule, platform_key: str, os_version: str) -> bool:
    if rule.os_name is not None and rule.os_name != platform_key:
        return False
    if rule.os_arch is not None:
        machine = platform.machine().lower()
        wanted = rule.os_arch.lower()
        if wanted == "x86" and machine not in ("i386", "i686", "x86"):
            return False
        if wanted == "x86_64" and machine not in ("x86_64", "amd64"):
            return False
        if wanted == "arm64" and machine not in ("aarch64", "arm64"):
            return False
    if rule.os_version is not None:
        try:
            if not re.search(rule.os_version, os_version):
                return False
        except re.error:
            return False
    return True


def rules_allow(
    rules: Sequence[Rule] | None,
    *,
    platform_key: str | None = None,
    os_version: str = "",
    features: Mapping[str, bool] | None = None,
    default: bool = True,
) -> bool:
    """Evaluate a Mojang rule list.

    The algorithm is: start from ``default`` (true when no rules exist) and let
    the *last* matching rule decide, which is what the official launcher does.
    """
    if not rules:
        return True
    platform_key = platform_key or current_platform_key()
    features = features or {}
    # With an explicit rule list the default is *deny*: a lone
    # {"action":"allow","os":{"name":"windows"}} must exclude every other OS.
    allowed = False
    for rule in rules:
        if not _os_matches(rule, platform_key, os_version or platform.release()):
            continue
        if rule.features and any(
            features.get(key, False) != value for key, value in rule.features.items()
        ):
            continue
        allowed = rule.action == "allow"
    return allowed


@dataclass(frozen=True)
class Artifact:
    path: str
    url: str
    sha1: str = ""
    size: int = 0

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> Artifact | None:
        if not raw:
            return None
        url = raw.get("url")
        path = raw.get("path")
        if not url or not path:
            return None
        return cls(
            path=str(path),
            url=str(url),
            sha1=str(raw.get("sha1") or ""),
            size=int(raw.get("size") or 0),
        )


@dataclass
class Library:
    name: str
    downloads: dict[str, Artifact] = field(default_factory=dict)
    natives: dict[str, str] = field(default_factory=dict)
    rules: list[Rule] = field(default_factory=list)
    extract: tuple[str, ...] = ()
    url: str = ""
    sha1: str = ""
    size: int = 0

    # -- helpers ------------------------------------------------------------
    @property
    def group(self) -> str:
        return self.name.split(":")[0]

    @property
    def artifact_name(self) -> str:
        return self.name.split(":")[1] if ":" in self.name else self.name

    @property
    def version(self) -> str:
        parts = self.name.split(":")
        return parts[2] if len(parts) > 2 else ""

    @property
    def key(self) -> str:
        """Group + artifact, used to detect that a child overrides a parent."""
        return f"{self.group}:{self.artifact_name}"

    def artifact(self) -> Artifact | None:
        return self.downloads.get("artifact")

    def resolved_artifact(self) -> Artifact | None:
        """Artifact for a library, including maven-base entries.

        Mod loaders (Fabric, Quilt, Forge, NeoForge) publish libraries as
        ``name`` + ``url`` (a Maven *base* URL) instead of fully resolved
        ``downloads.artifact`` objects.  Returning ``None`` for those would
        silently drop required jars from the classpath, so the path and URL are
        constructed here from the Maven coordinates.
        """
        artifact = self.artifact()
        if artifact is not None:
            return artifact
        if not self.url:
            return None
        relative = self.relative_path()
        base = self.url if self.url.endswith("/") else self.url + "/"
        return Artifact(path=relative, url=base + relative, sha1=self.sha1, size=self.size)

    def native_artifact(self, platform_key: str) -> Artifact | None:
        classifier = self.natives.get(platform_key)
        if not classifier:
            return None
        classifier = classifier.replace(
            "${arch}", "64" if platform.machine().endswith("64") else "32"
        )
        return self.downloads.get(f"natives-{classifier}") or self.downloads.get(classifier)

    def is_allowed(self, *, platform_key: str, features: Mapping[str, bool] | None = None) -> bool:
        return rules_allow(self.rules, platform_key=platform_key, features=features)

    def relative_path(self) -> str:
        """Maven-style path for a library that only declares a repository URL."""
        artifact = self.artifact()
        if artifact is not None:
            return artifact.path
        parts = self.name.split(":")
        if len(parts) < 3:
            raise ConfigError(f"malformed library name: {self.name}")
        group, artifact_id, version = parts[0], parts[1], parts[2]
        classifier = parts[3] if len(parts) > 3 else ""
        filename = f"{artifact_id}-{version}"
        if classifier:
            filename += f"-{classifier}"
        return "/".join(group.split(".")) + f"/{artifact_id}/{version}/{filename}.jar"

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> Library:
        downloads_raw = raw.get("downloads") or {}
        downloads: dict[str, Artifact] = {}
        for key, value in downloads_raw.items():
            if key == "classifiers":
                for ckey, cvalue in (value or {}).items():
                    parsed = Artifact.from_dict(cvalue)
                    if parsed:
                        downloads[f"natives-{ckey}"] = parsed
                        downloads.setdefault(ckey, parsed)
                continue
            parsed = Artifact.from_dict(value)
            if parsed:
                downloads[key] = parsed
        return cls(
            name=str(raw.get("name", "")),
            downloads=downloads,
            natives=dict(raw.get("natives") or {}),
            rules=[Rule.from_dict(r) for r in raw.get("rules") or []],
            extract=tuple((raw.get("extract") or {}).get("exclude") or ()),
            url=str(raw.get("url") or ""),
            sha1=str(raw.get("sha1") or ""),
            size=int(raw.get("size") or 0),
        )


@dataclass
class VersionDescriptor:
    """A parsed version JSON, optionally merged with its parent."""

    id: str
    type: str = "release"
    main_class: str = ""
    assets: str = "legacy"
    asset_index: Mapping[str, Any] = field(default_factory=dict)
    downloads: Mapping[str, Any] = field(default_factory=dict)
    libraries: list[Library] = field(default_factory=list)
    arguments_game: list[Any] = field(default_factory=list)
    arguments_jvm: list[Any] = field(default_factory=list)
    legacy_arguments: str = ""
    java_major: int = 0  # 0 means "unspecified"; inherited from the parent when absent
    java_component: str = ""
    release_time: str = ""
    compliance_level: int = 0
    inherits_from: str = ""
    logging: Mapping[str, Any] = field(default_factory=dict)
    minimum_launcher_version: int = 0
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False)
    parent: VersionDescriptor | None = field(default=None, repr=False)

    # -- construction -------------------------------------------------------
    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> VersionDescriptor:
        if not isinstance(raw, Mapping) or "id" not in raw:
            raise ConfigError("version JSON has no 'id'")
        arguments = raw.get("arguments") or {}
        return cls(
            id=str(raw["id"]),
            type=str(raw.get("type", "release")),
            main_class=str(raw.get("mainClass", "")),
            assets=str(raw.get("assets", "legacy")),
            asset_index=dict(raw.get("assetIndex") or {}),
            downloads=dict(raw.get("downloads") or {}),
            libraries=[Library.from_dict(lib) for lib in raw.get("libraries") or []],
            arguments_game=list(arguments.get("game") or []),
            arguments_jvm=list(arguments.get("jvm") or []),
            legacy_arguments=str(raw.get("minecraftArguments") or ""),
            java_major=int((raw.get("javaVersion") or {}).get("majorVersion") or 0),
            java_component=str((raw.get("javaVersion") or {}).get("component") or ""),
            release_time=str(raw.get("releaseTime") or ""),
            compliance_level=int(raw.get("complianceLevel") or 0),
            inherits_from=str(raw.get("inheritsFrom") or ""),
            logging=dict(raw.get("logging") or {}),
            minimum_launcher_version=int(raw.get("minimumLauncherVersion") or 0),
            raw=raw,
        )

    @classmethod
    def from_file(cls, path: Path) -> VersionDescriptor:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigError(f"cannot read version JSON {path}", detail=str(exc)) from exc
        return cls.from_dict(payload)

    # -- inheritance --------------------------------------------------------
    def merged(self) -> VersionDescriptor:
        """Return a descriptor with all parents folded in (child wins)."""
        if self.parent is None:
            return self
        parent = self.parent.merged()

        # Libraries: parent entries first; a child overrides by group:artifact
        # (the version differs, so comparing full coordinates is not enough).
        child_keys = {lib.key for lib in self.libraries}
        libraries = [lib for lib in parent.libraries if lib.key not in child_keys]
        libraries.extend(self.libraries)

        # Arguments: parent then child, preserving rule objects.
        game = [*parent.arguments_game, *self.arguments_game]
        jvm = [*parent.arguments_jvm, *self.arguments_jvm]

        asset_index = self.asset_index or parent.asset_index
        assets = self.assets if self.asset_index else parent.assets
        downloads = {**parent.downloads, **self.downloads}

        return VersionDescriptor(
            id=self.id,
            type=self.type,
            main_class=self.main_class or parent.main_class,
            assets=assets,
            asset_index=asset_index,
            downloads=downloads,
            libraries=libraries,
            arguments_game=game,
            arguments_jvm=jvm,
            legacy_arguments=self.legacy_arguments or parent.legacy_arguments,
            java_major=self.java_major or parent.java_major,
            java_component=self.java_component or parent.java_component,
            release_time=self.release_time or parent.release_time,
            compliance_level=max(self.compliance_level, parent.compliance_level),
            inherits_from=self.inherits_from,
            logging=self.logging or parent.logging,
            minimum_launcher_version=max(
                self.minimum_launcher_version, parent.minimum_launcher_version
            ),
            raw=self.raw,
        )

    @property
    def required_java(self) -> int:
        """Java major required to run this version (never lower than 8)."""
        return self.merged().java_major or 8

    def client_download(self) -> Artifact | None:
        """The client jar.

        Unlike libraries, the ``downloads.client`` entry has no Maven ``path``:
        it is just a url/sha1/size triple, so ``Artifact.from_dict`` (which
        requires a path) must not be used here.
        """
        merged = self.merged()
        raw = merged.downloads.get("client") or {}
        url = raw.get("url")
        if not url:
            return None
        return Artifact(
            path=str(raw.get("path") or f"{merged.id}.jar"),
            url=str(url),
            sha1=str(raw.get("sha1") or ""),
            size=int(raw.get("size") or 0),
        )

    def logging_config(self) -> Artifact | None:
        """Optional log4j configuration; its entry also has no ``path``."""
        merged = self.merged()
        client = (merged.logging or {}).get("client") or {}
        raw = client.get("file") or {}
        url = raw.get("url")
        if not url:
            return None
        identifier = str(raw.get("id") or "client-log4j.xml")
        return Artifact(
            path=identifier,
            url=str(url),
            sha1=str(raw.get("sha1") or ""),
            size=int(raw.get("size") or 0),
        )

    def native_libraries(self, platform_key: str) -> list[Library]:
        merged = self.merged()
        return [
            library
            for library in merged.libraries
            if library.natives and library.is_allowed(platform_key=platform_key)
        ]


def resolve_arguments(
    template: Iterable[Any],
    values: Mapping[str, str],
    *,
    platform_key: str | None = None,
    features: Mapping[str, bool] | None = None,
) -> list[str]:
    """Expand a structured ``arguments`` array into concrete argv entries."""
    platform_key = platform_key or current_platform_key()
    output: list[str] = []
    for item in template:
        if isinstance(item, str):
            output.append(_substitute(item, values))
        elif isinstance(item, Mapping):
            rules = [Rule.from_dict(r) for r in item.get("rules") or []]
            if not rules_allow(rules, platform_key=platform_key, features=features):
                continue
            value = item.get("value")
            if isinstance(value, str):
                output.append(_substitute(value, values))
            elif isinstance(value, Sequence):
                output.extend(_substitute(str(v), values) for v in value)
    return [token for token in output if token != ""]


def split_legacy_arguments(legacy: str, values: Mapping[str, str]) -> list[str]:
    """Legacy ``minecraftArguments`` are space separated, with quoting."""
    if not legacy:
        return []
    tokens = re.findall(r'"[^"]*"|\S+', legacy)
    return [_substitute(token.strip('"'), values) for token in tokens]


def _substitute(token: str, values: Mapping[str, str]) -> str:
    if "${" not in token:
        return token

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key in values:
            return values[key]
        # Unknown placeholders are left untouched so that the launch fails with
        # a visible, diagnosable argument instead of silently dropping a flag.
        return match.group(0)

    return re.sub(r"\$\{([a-z_0-9]+)\}", replace, token)


def classpath_entries(
    libraries: Sequence[Library], libraries_root: Path, client_jar: Path
) -> list[str]:
    """Ordered classpath, de-duplicated by maven coordinates."""
    seen: set[str] = set()
    entries: list[str] = []
    for library in libraries:
        artifact = library.resolved_artifact()
        if artifact is None:
            continue
        if library.name in seen:
            continue
        seen.add(library.name)
        entries.append(str(libraries_root / artifact.path))
    entries.append(str(client_jar))
    return entries


def check_java_compatibility(required_major: int, actual_major: int) -> None:
    """Raise a clear, translated error instead of a cryptic Java failure."""
    if required_major and actual_major and actual_major < required_major:
        raise UnsupportedError(
            f"this version requires Java {required_major} or newer, found {actual_major}",
            hint="Install a matching Java runtime in Settings > Java, or let Apeiron download one.",
        )
