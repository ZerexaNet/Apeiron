#!/usr/bin/env python3
"""Build a standalone Apeiron distribution with Nuitka.

Steps performed (all failures are fatal — a compiler exit code of 0 is not
evidence that the artifact works):

1. rebuild the C11 logging extension in place;
2. regenerate and compile the translation catalogues;
3. compile the application with ``--standalone --enable-plugin=pyside6``,
   including the QML module, the ``.qm`` files and the native extension;
4. execute the produced entry point with ``--self-check`` and require ``ok``.

Usage::

    python packaging/build_nuitka.py --output-dir dist
    python packaging/build_nuitka.py --onefile            # single executable
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(command: list[str], *, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    print("+", " ".join(command), flush=True)
    result = subprocess.run(command, cwd=str(cwd or ROOT), check=False)
    if check and result.returncode != 0:
        raise SystemExit(f"command failed ({result.returncode}): {' '.join(command)}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="dist")
    parser.add_argument("--onefile", action="store_true")
    parser.add_argument("--skip-self-check", action="store_true")
    parser.add_argument("--jobs", default="")
    args = parser.parse_args()

    if sys.version_info < (3, 10):
        raise SystemExit("Python 3.10+ is required")

    try:
        import nuitka  # noqa: F401
    except ImportError:
        raise SystemExit("Nuitka is not installed: pip install '.[build]'") from None

    out_dir = (ROOT / args.output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. native extension ---------------------------------------------------
    run([sys.executable, "setup.py", "build_ext", "--inplace"])
    extension = next(iter((ROOT / "src" / "apeiron").glob("_logcore*")), None)
    if extension is None:
        raise SystemExit("the native extension was not produced")

    # 2. translations -------------------------------------------------------
    run([sys.executable, "tools/build_translations.py"])
    for qm in (ROOT / "src" / "apeiron" / "i18n").glob("*.qm"):
        if qm.stat().st_size < 512:
            raise SystemExit(f"suspiciously small translation file: {qm}")

    # 3. compilation --------------------------------------------------------
    command = [
        sys.executable,
        "-m",
        "nuitka",
        "--standalone",
        "--assume-yes-for-downloads",
        "--enable-plugin=pyside6",
        "--include-qt-plugins=platforms,imageformats,iconengines,tls",
        "--include-package=apeiron",
        "--include-package-data=apeiron",
        "--noinclude-dlls=*.debug",
        f"--output-dir={out_dir}",
        f"--output-filename=Apeiron{'.exe' if platform.system() == 'Windows' else ''}",
        "--company-name=Zerexa Ltd",
        "--product-name=Apeiron",
        "--file-version=0.1.0",
        "--product-version=0.1.0",
        "--file-description=Apeiron Minecraft launcher",
        "--copyright=Zerexa Ltd",
        # The extension is compiled C, not Python source: copy it as data.
        f"--include-data-files={extension}=apeiron/{extension.name}",
        "packaging/entry.py",
    ]
    if args.onefile:
        command.insert(2, "--onefile")
    if args.jobs:
        command.insert(2, f"--jobs={args.jobs}")
    run(command)

    # 4. verify the artifact ------------------------------------------------
    if args.onefile:
        binary = out_dir / ("Apeiron.exe" if platform.system() == "Windows" else "Apeiron.bin")
    else:
        suffix = ".exe" if platform.system() == "Windows" else ""
        for dist_name in ("entry.dist", "__main__.dist"):
            candidate = out_dir / dist_name / f"Apeiron{suffix}"
            if candidate.exists():
                binary = candidate
                break
        else:
            binary = out_dir / "entry.dist" / f"Apeiron{suffix}"

    if not binary.exists():
        matches = list(out_dir.rglob("Apeiron*"))
        raise SystemExit(f"artifact not found; candidates: {[str(m) for m in matches][:5]}")

    print(f"artifact: {binary}")

    if not args.skip_self_check:
        report_file = out_dir / "self-check.json"
        import os

        env = dict(os.environ)
        env["QT_QPA_PLATFORM"] = "offscreen"
        env.pop("QT_QUICK_BACKEND", None)
        env["APEIRON_HOME"] = str(out_dir / "verify-home")
        result = subprocess.run(
            [str(binary), "--self-check"], capture_output=True, text=True, env=env, timeout=600
        )
        start = result.stdout.find("{")
        payload = json.loads(result.stdout[start:]) if start >= 0 else {}
        report_file.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        if result.returncode != 0 or not payload.get("ok"):
            print(result.stdout[-3000:], file=sys.stderr)
            print(result.stderr[-3000:], file=sys.stderr)
            raise SystemExit("the built artifact failed its self-check")
        print(f"self-check: ok ({report_file})")
        # A distribution that cannot load the native extension is not usable.
        checks = payload.get("checks", {})
        if not checks.get("nativeLogCore", {}).get("loaded"):
            raise SystemExit("the native logging extension did not load in the artifact")
        if not checks.get("qml", {}).get("loaded"):
            raise SystemExit("QML did not load in the artifact")

    shutil.copy(ROOT / "DISCLAIMER.md", out_dir / "DISCLAIMER.md")
    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
