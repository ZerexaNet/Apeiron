# Building Apeiron

## Requirements

| Component | Version | Why |
| --- | --- | --- |
| Python | 3.10 – 3.12 | 3.13 is not yet validated with the pinned PySide6 |
| C compiler | gcc/clang (C11) or MSVC 2019+ | native logging extension |
| Qt | provided by PySide6 6.9.x | QML, Quick Controls, Material style |
| Nuitka | ≥ 2.4 | release builds |

`PySide6-Essentials` supplies QtCore/QtGui/QtQml/QtQuick/QtQuickControls2;
`PySide6-Addons` is **also required** because the Material style's elevation
effect uses `Qt5Compat.GraphicalEffects`, which ships only with Addons. Without
it, controls that request elevation fail with
`Type ToolBar unavailable` / `RectangularGlow is not a type`.

## Development build

```bash
python -m venv .venv
. .venv/bin/activate                 # Windows: .venv\Scripts\activate
pip install -e ".[dev]"              # runtime + test tooling
python setup.py build_ext --inplace  # compiles apeiron._logcore into src/apeiron/
python -m apeiron
```

The extension is compiled from `csrc/logcore.c` + `csrc/_logcore_module.c`.
On Windows no explicit `-std=c11` is passed (MSVC does not accept it); the code
sticks to C11 features that MSVC supports.

## Translations

```bash
python tools/build_translations.py
```

Requires `pyside6-lupdate` / `pyside6-lrelease` (installed with PySide6). The
`.ts` files are generated, filled from the table in the script and compiled to
`.qm`. Both are committed so that a plain `pip install` produces a fully
localised application.

## Release build (Nuitka)

```bash
pip install -e ".[build]"
python packaging/build_nuitka.py --output-dir dist
```

The script:

1. rebuilds the native extension in place;
2. compiles translations;
3. runs Nuitka with `--standalone --enable-plugin=pyside6`, including the QML
   module and translations as package data and `apeiron._logcore` as a native
   extension module;
4. runs the resulting binary with `--self-check` and fails the build if the
   report is not `ok`.

Artifact layout:

```
dist/entry.dist/
  ApeironLauncher             # entry point (ApeironLauncher.exe on Windows)
  apeiron/_logcore.<ext>       # native extension, loaded by the frozen app
  apeiron/ui/qml/…             # QML module + Material components
  apeiron/i18n/*.qm            # compiled translations
  PySide6/…                    # Qt runtime and plugins
```

Platform notes:

* **Windows** — build on Windows with MSVC; the output is a folder that an
  installer (e.g. Inno Setup, not included here) can package.
* **macOS** — the current build produces a standalone directory, not an `.app`
  bundle. Signing and notarisation are not configured. See runner selection below.
* **Linux** — the standalone folder is self-contained; ship it as-is, in a
  tarball, or wrap it with AppImage/deb tooling.

## Verifying a build

```bash
dist/entry.dist/ApeironLauncher --self-check | python -c "import json,sys; d=json.load(sys.stdin); assert d['ok']"
```

`--self-check` loads the native extension, opens/migrates SQLite, constructs the
QML interface offscreen and reports Qt warnings. A build that cannot do this is
not a working build, regardless of whether the compiler succeeded.


## macOS hosted runners and architecture

Routine PR, push and tag workflows use `macos-15-intel` for the macOS jobs.
This is GitHub's standard Intel runner and generates `apeiron-macos-x86_64`.
Intel applications require Rosetta 2 on Apple Silicon; this archive is not a
native ARM64 package.

GitHub's ARM64 runner pool can fail to acquire a job before any workflow step
starts (`The job was not acquired by Runner ...`, with capacity annotations).
Changing Python dependencies or retrying a step cannot repair that scheduling
failure, and changing `macos-14` to another ARM64 label still uses ARM64 capacity.

To build or test natively for Apple Silicon, open **Actions → Build** (or **CI**),
choose **Run workflow**, select the branch/ref and set `macos_arch` to `arm64`.
That macOS job uses `macos-15`, and Build uploads `apeiron-macos-arm64`.
The default input is `x86_64`. Build explicitly selects the matching Python
architecture and checks it before compiling, so archive names match the executable.
ARM64 manual jobs may still queue when GitHub has insufficient capacity.

Runner labels: https://docs.github.com/en/actions/reference/runners/github-hosted-runners
