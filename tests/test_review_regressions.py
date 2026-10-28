"""Regression coverage for PR #1 review findings and ordered mirror fallback."""

from __future__ import annotations

import hashlib
import io
import json
import os
import threading
import time
import urllib.error
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from apeiron.auth.microsoft import MicrosoftAuth
from apeiron.core.archive import extract_zip, zip_directory
from apeiron.core.config import Settings, SettingsStore
from apeiron.core.errors import (
    ArchiveError,
    DownloadError,
    IntegrityError,
    LoaderError,
    NetworkError,
    PermissionError_,
)
from apeiron.core.http import HttpClient, HttpConfig, Response
from apeiron.core.paths import AppPaths
from apeiron.instances.manager import InstanceManager, instance_summary
from apeiron.mc.launch import _split_extra
from apeiron.mc.manifest import MANIFEST_URL, VersionManifest
from apeiron.mc.version import Library, Rule, rules_allow
from apeiron.modpack import curseforge, mrpack
from apeiron.modpack.service import ModpackService
from apeiron.net.downloader import DownloadManager, DownloadSpec
from apeiron.net.sources import MIRROR_ROOTS, download_candidates

DATA = b"verified Minecraft bytes"


@pytest.mark.parametrize(
    "path,prefix",
    [
        ("https://piston-meta.mojang.com/v1/x.json", "/v1/x.json"),
        ("https://piston-data.mojang.com/v1/x.jar", "/v1/x.jar"),
        ("https://resources.download.minecraft.net/ab/abcdef", "/assets/ab/abcdef"),
        ("https://libraries.minecraft.net/org/example.jar", "/maven/org/example.jar"),
        ("https://maven.minecraftforge.net/net/forge.jar", "/maven/net/forge.jar"),
        ("https://meta.fabricmc.net/v2/versions/game", "/fabric-meta/v2/versions/game"),
        (
            "https://maven.neoforged.net/releases/net/neoforged/neoforge/x.jar",
            "/maven/net/neoforged/neoforge/x.jar",
        ),
    ],
)
def test_mirror_priority_and_mapping(path, prefix):
    assert download_candidates(path) == (*(root + prefix for root in MIRROR_ROOTS), path)


@pytest.mark.parametrize(
    "url",
    [
        "https://login.microsoftonline.com/token",
        "https://api.minecraftservices.com/minecraft/profile",
        "https://api.curseforge.com/v1/mods/1",
        "https://cdn.modrinth.com/a.jar",
        "https://libraries.minecraft.net.evil.test/a.jar",
        "https://user:secret@libraries.minecraft.net/a.jar",
    ],
)
def test_unrelated_and_authenticated_urls_are_never_mirrored(url):
    assert download_candidates(url) == (url,)


def test_metadata_fallback_rejects_corrupt_hash():
    http = HttpClient(HttpConfig(use_mirrors=True, retries=0))
    url = "https://piston-meta.mojang.com/v1/x.json"
    seen = []

    def request(candidate, **kwargs):
        seen.append(candidate)
        if len(seen) == 1:
            raise NetworkError("mirror down")
        return Response(candidate, 200, body=b"bad" if len(seen) == 2 else DATA)

    with patch.object(http, "_request", request):
        assert http.get_bytes(url, expected_sha1=hashlib.sha1(DATA).hexdigest()) == DATA
    assert seen == list(download_candidates(url)[:3])


def test_download_fallback_restarts_partial_files_and_counts_bytes(tmp_path, fake_http):
    url = "https://piston-data.mojang.com/v1/x.jar"
    candidates = download_candidates(url)
    fake_http.candidates = download_candidates
    fake_http.add(candidates[0], b"bad")
    fake_http.add(candidates[1], DATA)
    snapshots = []
    manager = DownloadManager(fake_http, retries=0, progress_callback=snapshots.append)
    spec = DownloadSpec(
        url, tmp_path / "x.jar", sha1=hashlib.sha1(DATA).hexdigest(), size=len(DATA)
    )
    manager.download(spec)
    assert spec.destination.read_bytes() == DATA
    assert fake_http.calls == list(candidates[:2])
    assert snapshots[-1].bytes_done == len(DATA)
    manager.download(spec)
    assert snapshots[-1].bytes_done == len(DATA)
    assert snapshots[-1].completed_tasks == 1


def test_sha512_only_pack_is_verified_and_cached_files_are_rechecked(tmp_path, fake_http):
    url = "https://cdn.modrinth.com/mod.jar"
    fake_http.add(url, DATA)
    spec = DownloadSpec(
        url, tmp_path / "mod.jar", size=len(DATA), sha512=hashlib.sha512(DATA).hexdigest()
    )
    manager = DownloadManager(fake_http, retries=0)
    manager.download(spec)
    assert manager.download(spec).skipped
    spec.destination.write_bytes(b"x" * len(DATA))
    assert not manager.download(spec).skipped
    bad = DownloadSpec(url, tmp_path / "bad.jar", sha512="0" * 128)
    with pytest.raises(DownloadError):
        manager.download(bad)
    assert not bad.destination.exists()


def test_oauth_http400_protocol_error_body_is_preserved():
    http = HttpClient(HttpConfig(retries=0))
    auth = MicrosoftAuth(http, "client")
    for error in ["authorization_pending", "slow_down", "authorization_declined", "expired_token"]:
        body = json.dumps({"error": error}).encode()
        response = urllib.error.HTTPError(
            "https://login.live.com/token", 400, "Bad Request", {}, io.BytesIO(body)
        )
        with patch("urllib.request.urlopen", side_effect=response):
            assert (
                auth._form_post("https://login.live.com/token", {}, allow_error_body=True)["error"]
                == error
            )
    response = urllib.error.HTTPError(
        "https://login.live.com/token", 400, "Bad Request", {}, io.BytesIO(b"{}")
    )
    with patch("urllib.request.urlopen", side_effect=response), pytest.raises(NetworkError):
        http.request("https://login.live.com/token", method="POST")


def test_stream_rejects_https_to_http_redirect_before_reading():
    class Raw:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def geturl(self):
            return "http://attacker.test/mod.jar"

        def read(self, *args):
            pytest.fail("downgraded body must not be consumed")

    with patch("urllib.request.urlopen", return_value=Raw()), pytest.raises(NetworkError):
        list(HttpClient().stream("https://cdn.example.test/mod.jar"))


def test_macos_native_and_rule_names():
    assert rules_allow([Rule(os_name="osx")], platform_key="macos")
    library = Library.from_dict(
        {
            "name": "test:native:1",
            "natives": {"osx": "natives-osx"},
            "downloads": {
                "classifiers": {
                    "natives-osx": {"path": "native.jar", "url": "https://x/native.jar"}
                }
            },
        }
    )
    assert library.native_artifact("macos").path == "native.jar"


@pytest.mark.parametrize("value", [None, "bad", [], {}, True, float("nan"), float("inf")])
def test_settings_invalid_types_fall_back(tmp_path, value):
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps(
            {
                "download_retries": value,
                "log_level": value,
                "download_timeout": value,
                "schema_version": value,
            }
        )
    )
    settings = SettingsStore(path).load()
    assert settings.download_retries == Settings().download_retries
    assert settings.log_level == "INFO"
    assert settings.download_timeout == Settings().download_timeout


def test_existing_root_permissions_are_preserved(tmp_path):
    if os.name != "posix":
        pytest.skip("POSIX modes")
    tmp_path.chmod(0o755)
    AppPaths(tmp_path).ensure()
    assert tmp_path.stat().st_mode & 0o777 == 0o755
    assert (tmp_path / "data").stat().st_mode & 0o777 == 0o700


def test_archive_export_omits_external_symlink(tmp_path):
    root = tmp_path / "instance"
    root.mkdir()
    secret = tmp_path / "secret"
    secret.write_text("private")
    try:
        (root / "leak").symlink_to(secret)
        (root / "outside").symlink_to(tmp_path, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation unavailable")
    (root / "safe").write_text("public")
    target = zip_directory(root, tmp_path / "out.zip")
    with zipfile.ZipFile(target) as zf:
        assert zf.namelist() == ["safe"]


def test_restore_unsafe_backup_preserves_existing_worlds(repos, app_paths, tmp_path):
    manager = InstanceManager(repos["instances"], app_paths)
    instance = manager.create(name="worlds", mc_version="1.20.1")
    world = Path(instance.directory) / "saves" / "world.dat"
    world.write_bytes(DATA)
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../../escape", "bad")
    with pytest.raises(PermissionError_):
        manager.restore_backup(instance.id, archive)
    assert world.read_bytes() == DATA
    assert not list(Path(instance.directory).glob("restore-*"))


def test_overrides_use_shared_size_and_symlink_guards(tmp_path):
    archive = tmp_path / "pack.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("overrides/config/big", b"x" * 1024)
    with pytest.raises(ArchiveError):
        extract_zip(archive, tmp_path / "dest", prefixes=("overrides",), max_total_bytes=16)
    with zipfile.ZipFile(archive, "w") as zf:
        info = zipfile.ZipInfo("overrides/link")
        info.external_attr = 0o120777 << 16
        zf.writestr(info, "../../secret")
    with pytest.raises(PermissionError_):
        mrpack.extract_overrides(archive, tmp_path / "dest")


def test_version_metadata_hash_is_verified(fake_http, tmp_path):
    url = "https://piston-meta.mojang.com/version.json"
    fake_http.add_json(MANIFEST_URL, {"versions": [{"id": "test", "url": url, "sha1": "0" * 40}]})
    fake_http.add_json(url, {"id": "test", "mainClass": "tampered"})
    manifest = VersionManifest(fake_http, tmp_path)
    with pytest.raises(IntegrityError):
        manifest.download_version_json("test", tmp_path / "versions")
    assert not (tmp_path / "versions/test/test.json").exists()


def test_instance_summary_preserves_editor_values(repos, app_paths):
    manager = InstanceManager(repos["instances"], app_paths)
    instance = manager.create(name="custom", mc_version="1.20.1", width=1600, height=900)
    instance.jvm_args = "-Dcustom=true"
    instance.game_args = "--demo"
    projection = instance_summary(instance)
    assert (projection["width"], projection["height"]) == (1600, 900)
    assert projection["jvmArgs"] == instance.jvm_args
    assert projection["gameArgs"] == instance.game_args


def test_custom_arguments_preserve_windows_paths_and_quoting():
    assert _split_extra(r'-Dconfig=C:\mods\config.json "-Dname=hello world"') == [
        r"-Dconfig=C:\mods\config.json",
        "-Dname=hello world",
    ]


def test_modpack_loader_failure_rolls_back_instance(tmp_path, repos, app_paths, fake_http):
    archive = tmp_path / "pack.mrpack"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(
            mrpack.INDEX_NAME,
            json.dumps(
                {
                    "formatVersion": 1,
                    "dependencies": {"minecraft": "1.20.1", "fabric-loader": "missing"},
                    "files": [],
                }
            ),
        )
    instances = InstanceManager(repos["instances"], app_paths)
    service = ModpackService(
        instances, repos["mods"], DownloadManager(fake_http), http=fake_http, paths=app_paths
    )
    with pytest.raises(LoaderError):
        service.import_archive(archive)
    assert instances.list() == []
    assert list(app_paths.instances.iterdir()) == []


def test_exported_modpack_keeps_config_directory(tmp_path, repos, app_paths, fake_http):
    instances = InstanceManager(repos["instances"], app_paths)
    instance = instances.create(name="config", mc_version="1.20.1")
    (Path(instance.directory) / "config/example.toml").write_text("x=1")
    service = ModpackService(
        instances, repos["mods"], DownloadManager(fake_http), http=fake_http, paths=app_paths
    )
    archive = service.export_mrpack(instance, tmp_path / "export.mrpack")
    target = tmp_path / "import"
    mrpack.extract_overrides(archive, target)
    assert (target / "config/example.toml").read_text() == "x=1"


def test_batches_serialize_without_resetting_active_progress(tmp_path, fake_http):
    started = threading.Event()
    release = threading.Event()
    seen = []

    def stream(url, **kwargs):
        seen.append(url)
        if url == "https://x/first":
            started.set()
            assert release.wait(5)
        yield DATA

    fake_http.stream = stream
    manager = DownloadManager(fake_http, retries=0)
    errors = []

    def run(name):
        try:
            manager.download(DownloadSpec("https://x/" + name, tmp_path / name, size=len(DATA)))
        except Exception as exc:
            errors.append(exc)

    first = threading.Thread(target=run, args=("first",))
    second = threading.Thread(target=run, args=("second",))
    first.start()
    assert started.wait(5)
    second.start()
    time.sleep(0.05)
    assert seen == ["https://x/first"]
    release.set()
    first.join(5)
    second.join(5)
    assert not errors
    assert seen == ["https://x/first", "https://x/second"]


def test_legacy_asset_layouts_and_instance_resources(tmp_path, fake_http, app_paths):
    from apeiron.mc.installer import JavaInstaller
    from apeiron.mc.version import VersionDescriptor

    sha1 = hashlib.sha1(DATA).hexdigest()
    index = {
        "virtual": True,
        "map_to_resources": True,
        "objects": {"sounds/example.ogg": {"hash": sha1, "size": len(DATA)}},
    }
    index_file = tmp_path / "index.json"
    index_file.write_text(json.dumps(index))
    fake_http.add(f"https://resources.download.minecraft.net/{sha1[:2]}/{sha1}", DATA)
    installer = JavaInstaller(fake_http, app_paths, DownloadManager(fake_http), None)
    descriptor = VersionDescriptor(id="old", assets="legacy")
    with patch.object(installer, "_download_asset_index", return_value=index_file):
        installer._install_assets(descriptor, lambda stage: None)
    assert (app_paths.assets / "virtual/legacy/sounds/example.ogg").read_bytes() == DATA
    instance = tmp_path / "instance"
    installer.materialize_resources(SimpleNamespace(asset_index="legacy"), instance)
    assert (instance / "resources/sounds/example.ogg").read_bytes() == DATA


def test_neoforge_two_component_game_version(fake_http, app_paths):
    from apeiron.loaders.forge import NeoForgeLoader

    loader = NeoForgeLoader(fake_http, app_paths)
    fake_http.add(
        f"{loader.maven_base}/net/neoforged/neoforge/maven-metadata.xml",
        b"<metadata><version>21.0.10</version><version>21.1.1</version></metadata>",
    )
    assert [v.version for v in loader.versions("1.21")] == ["21.0.10"]


def test_content_categories_and_nested_paths_survive_import(tmp_path, fake_http, app_paths, repos):
    entries = []
    for directory in ["mods", "resourcepacks", "shaderpacks", "datapacks"]:
        url = f"https://cdn.example.test/{directory}.jar"
        fake_http.add(url, DATA)
        entries.append(
            {
                "path": f"{directory}/nested/example.jar",
                "downloads": [url],
                "hashes": {"sha512": hashlib.sha512(DATA).hexdigest()},
                "fileSize": len(DATA),
            }
        )
    archive = tmp_path / "pack.mrpack"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(
            mrpack.INDEX_NAME,
            json.dumps(
                {"formatVersion": 1, "dependencies": {"minecraft": "1.20.1"}, "files": entries}
            ),
        )
    service = ModpackService(
        InstanceManager(repos["instances"], app_paths),
        repos["mods"],
        DownloadManager(fake_http),
        http=fake_http,
        paths=app_paths,
    )
    result = service.import_archive(archive)
    records = repos["mods"].list_for_instance(result.instance.id)
    assert {r.kind for r in records} == {"mod", "resourcepack", "shaderpack", "datapack"}
    for record in records:
        assert record.filename == "nested/example.jar"
        service.remove_content(result.instance, record)
    assert not list(Path(result.instance.directory).rglob("*.jar"))


def test_curseforge_import_records_manageable_mods(tmp_path, fake_http, app_paths, repos):
    archive = tmp_path / "pack.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(
            curseforge.MANIFEST_NAME,
            json.dumps(
                {
                    "name": "CF",
                    "minecraft": {"version": "1.20.1"},
                    "files": [{"projectID": 1, "fileID": 2}],
                }
            ),
        )
    fake_http.add_json(
        f"{curseforge.API_BASE}/mods/1/files/2",
        {
            "data": {
                "downloadUrl": "https://cdn.example.test/a.jar",
                "fileName": "a.jar",
                "fileLength": len(DATA),
            }
        },
    )
    fake_http.add("https://cdn.example.test/a.jar", DATA)
    service = ModpackService(
        InstanceManager(repos["instances"], app_paths),
        repos["mods"],
        DownloadManager(fake_http),
        http=fake_http,
        paths=app_paths,
        curseforge_key="user-key",
    )
    result = service.import_archive(archive)
    records = repos["mods"].list_for_instance(result.instance.id)
    assert len(records) == 1
    assert records[0].source == "curseforge"
    service.remove_content(result.instance, records[0])
    assert not (Path(result.instance.directory) / "mods/a.jar").exists()


def test_launch_identity_failure_records_and_stop_duration(tmp_path, repos, app_paths, monkeypatch):
    from apeiron.core.errors import LaunchError
    from apeiron.services.launcher import LaunchService, LaunchState

    manager = InstanceManager(repos["instances"], app_paths)
    instance = manager.create(name="launch", mc_version="1.20.1")
    installer = SimpleNamespace(materialize_resources=lambda *args: None)
    service = LaunchService(
        app_paths,
        None,
        None,
        installer,
        repos["instances"],
        repos["launches"],
        java_runtimes=lambda: [],
        default_java=lambda: "",
    )
    monkeypatch.setattr(service, "ensure_installed", lambda *args: object())
    monkeypatch.setattr(
        service, "choose_java", lambda *args: SimpleNamespace(path=tmp_path / "java")
    )
    options = []
    monkeypatch.setattr(
        "apeiron.services.launcher.build_command",
        lambda installed, opt: options.append(opt) or ["java"],
    )

    class Process:
        pid = 123
        running = True
        fail = True

        def __init__(self, *args, **kwargs):
            self.callback = kwargs["on_exit"]

        def start(self):
            if self.fail:
                raise OSError("cannot execute Java")

        def stop(self):
            self.running = False
            return 0

    monkeypatch.setattr("apeiron.services.launcher.GameProcess", Process)
    with pytest.raises(OSError):
        service.launch(instance, player_name="test", uuid="a", client_id="client-id")
    assert repos["launches"].recent(1)[0].status == "failed"
    assert options[0].client_id == "client-id"
    Process.fail = False
    process = service.launch(instance, player_name="test", uuid="a")
    with pytest.raises(LaunchError):
        service.delete_instance(instance.id, manager.delete)
    assert manager.get(instance.id)
    # Capture timestamp belongs to this launch even if state changes during stop.
    with patch("apeiron.services.launcher.time.time", return_value=service.state.started_at + 12):
        service.stop()
        service.state = LaunchState()
        process.callback(0)
    assert (
        next(r for r in repos["launches"].recent(10) if r.status == "exited").duration_seconds == 12
    )


def test_file_url_conversion_and_backend_completion(app_paths, monkeypatch):
    from PySide6.QtCore import QCoreApplication, QUrl

    from apeiron.services.app_context import build_context
    from apeiron.ui.backend import Backend, local_path

    app = QCoreApplication.instance() or QCoreApplication([])
    target = app_paths.root / "中文 file.zip"
    assert local_path(QUrl.fromLocalFile(str(target)).toString()) == target
    context = build_context(root=app_paths.root)
    backend = Backend(context)
    callbacks = {}
    monkeypatch.setattr(
        backend, "_run", lambda task, fn, on_done=None: callbacks.update({task: (fn, on_done)})
    )
    backend.installJava(21)
    runtime = app_paths.java / "bin/java"
    callbacks["install-java"][1]({"path": str(runtime), "version": "21"})
    assert context.settings.java_path == str(runtime)
    backend._download_progress({"totalTasks": 1, "completedTasks": 1, "failedTasks": 0})
    assert backend.status["kind"] == "idle"
    stopped = []
    monkeypatch.setattr(context.launcher, "stop", lambda: stopped.append(True))
    backend.shutdown(0)
    assert stopped == []
    context.settings_store.update(close_game_on_launcher_exit=True)
    backend.shutdown(0)
    assert stopped == [True]
    context.database.close()
    assert app is not None


@pytest.mark.parametrize("root", MIRROR_ROOTS)
def test_embedded_mirror_metadata_urls_keep_all_fallbacks(root):
    path = "/v1/packages/abc/1.20.1.json"
    assert download_candidates(root + path) == (
        *(mirror + path for mirror in MIRROR_ROOTS),
        "https://piston-meta.mojang.com" + path,
    )
