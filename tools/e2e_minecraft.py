#!/usr/bin/env python3
"""End-to-end Minecraft: Java Edition verification.

Performs what a user does, with no mocks:

1. resolves the official version manifest;
2. downloads a Temurin JRE through the Adoptium API;
3. installs a full Minecraft version (client jar, libraries, natives, assets);
4. verifies every file against its published checksum;
5. creates an offline profile and an instance;
6. launches the game as a child process and waits for the client to report a
   running game loop;
7. stops the process and records the exit status.

Usage::

    xvfb-run -a python3 tools/e2e_minecraft.py --version 1.20.1 \
        --data-dir /tmp/apeiron-e2e --report /tmp/e2e.json

Exit code 0 only when every stage succeeded *and* the game actually started.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

REPORT: dict = {"stages": [], "ok": False}


def stage(name: str, ok: bool, **detail) -> None:
    entry = {"stage": name, "ok": bool(ok), **detail}
    REPORT["stages"].append(entry)
    print(("PASS " if ok else "FAIL ") + name + (f"  {detail}" if detail else ""), flush=True)
    if not ok:
        raise SystemExit(f"stage failed: {name}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", default="1.20.1")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--report", default="")
    parser.add_argument("--java-major", type=int, default=17)
    parser.add_argument("--skip-assets", action="store_true")
    parser.add_argument("--timeout", type=float, default=180.0)
    args = parser.parse_args()

    os.environ["APEIRON_HOME"] = args.data_dir
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

    from apeiron.core import logging as native
    from apeiron.services.app_context import build_context
    from apeiron.core.errors import ApeironError

    context = build_context()
    stage("bootstrap", True, data_root=str(context.paths.root))

    # 1. official manifest -------------------------------------------------
    versions = context.manifest.load()
    releases = [v.id for v in versions if v.is_release]
    stage("manifest", args.version in releases, count=len(versions), latest=releases[0])
    descriptor = context.manifest.fetch_version_json(args.version)
    stage(
        "version_json",
        "downloads" in descriptor,
        java=descriptor.get("javaVersion", {}).get("majorVersion"),
        libraries=len(descriptor.get("libraries", [])),
    )

    # 2. managed Java runtime ---------------------------------------------
    from apeiron.mc.java import AdoptiumClient, install_runtime, probe_java

    java_root = context.paths.java
    java_runtime = None
    for candidate in java_root.glob("temurin-*/.ready"):
        binary = next((p for p in (candidate.parent).rglob("bin/java")), None)
        if binary:
            java_runtime = probe_java(binary)
            break
    if java_runtime is None:
        client = AdoptiumClient(context.http)
        package = client.latest(args.java_major)
        stage("adoptium_lookup", bool(package.url), package=package.name, major=package.major)

        def download(url: str, target: Path, checksum: str) -> None:
            from apeiron.net.downloader import DownloadSpec

            context.downloader.run([DownloadSpec(url=url, destination=target, category="java")])
            if checksum:
                import hashlib

                digest = hashlib.sha256()
                with target.open("rb") as handle:
                    for block in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(block)
                if digest.hexdigest() != checksum:
                    target.unlink(missing_ok=True)
                    raise ApeironError("Java package checksum mismatch")

        java_runtime = install_runtime(package, java_root, download=download)
    stage("java_runtime", java_runtime.major >= args.java_major,
          version=java_runtime.version, major=java_runtime.major, managed=java_runtime.managed)

    # 3/4. install the game files -----------------------------------------
    started = time.monotonic()
    installed = context.installer.install(
        args.version, include_assets=not args.skip_assets
    )
    problems = context.installer.verify(args.version)
    stage(
        "install",
        not problems,
        seconds=round(time.monotonic() - started, 1),
        classpath=len(installed.classpath),
        assets=installed.asset_index,
        problems=problems[:5],
    )

    # 5. offline account + instance ---------------------------------------
    account = context.accounts.add_offline("E2ETester")
    instance = context.instances.create(
        name="E2E", mc_version=args.version, account_id=account.id, memory_mb=2048
    )
    stage("instance", Path(instance.directory).is_dir(), id=instance.id)

    # 6. launch ------------------------------------------------------------
    evidence = {"started": False, "lines": [], "signal": ""}
    markers = (
        "Setting user:",
        "Backend library:",
        "LWJGL",
        "OpenAL initialized",
        "Sound engine started",
        "Created:",
        "Reloading ResourceManager",
    )

    def on_line(line: str) -> None:
        text = line.strip()
        if not text:
            return
        if len(evidence["lines"]) < 400:
            evidence["lines"].append(text)
        for marker in markers:
            if marker.lower() in text.lower():
                evidence["started"] = True
                if not evidence["signal"]:
                    evidence["signal"] = text[:200]

    session = context.accounts.session_for(account.id)
    process = context.launcher.launch(
        instance,
        player_name=session.player_name,
        uuid=session.uuid,
        access_token=session.access_token,
        user_type=session.user_type,
        on_line=on_line,
    )
    stage("process_started", process.pid is not None, pid=process.pid)

    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        if evidence["started"] or not process.running:
            break
        time.sleep(0.5)

    if evidence["started"]:
        time.sleep(8)  # let the client create its window and load resources

    running = process.running
    exit_code = process.stop(grace=10)
    process.join_reader(timeout=10)
    stage(
        "game_running",
        bool(evidence["started"]),
        signal=evidence["signal"],
        still_running_before_stop=running,
        exit_code=exit_code,
        log_lines=len(evidence["lines"]),
    )

    log_path = context.launcher.launches.recent(1)[0].log_path
    stage("game_log", Path(log_path).exists(), path=log_path,
          bytes=Path(log_path).stat().st_size if Path(log_path).exists() else 0)

    REPORT["ok"] = all(item["ok"] for item in REPORT["stages"])
    native.flush()
    return 0 if REPORT["ok"] else 1


if __name__ == "__main__":
    try:
        code = main()
    except SystemExit as exc:
        code = 1
        print(f"aborted: {exc}", file=sys.stderr)
    except BaseException as exc:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        REPORT["error"] = str(exc)
        code = 1
    finally:
        report_path = ""
        for index, argv in enumerate(sys.argv):
            if argv == "--report" and index + 1 < len(sys.argv):
                report_path = sys.argv[index + 1]
        if report_path:
            Path(report_path).write_text(json.dumps(REPORT, indent=2, ensure_ascii=False))
        print(json.dumps(REPORT, indent=2, ensure_ascii=False)[:4000])
    raise SystemExit(code)
