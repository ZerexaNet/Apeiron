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
   module, translations and `apeiron._logcore` as data files (the extension is a
   compiled C module, not Python source);
4. runs the resulting binary with `--self-check` and fails the build if the
   report is not `ok`.

Artifact layout:

```
dist/Apeiron/
  apeiron                      # entry point (apeiron.exe on Windows)
  apeiron/_logcore.<ext>       # native extension, loaded by the frozen app
  apeiron/qml/…                # QML module + Material components
  apeiron/i18n/*.qm            # compiled translations
  PySide6/…                    # Qt runtime and plugins
```

Platform notes:

* **Windows** — build on Windows with MSVC; the output is a folder that an
  installer (e.g. Inno Setup, not included here) can package.
* **macOS** — `--macos-create-app-bundle` produces `Apeiron.app`. Signing and
  notarisation require an Apple Developer ID; no certificate is available to
  this project, so releases are unsigned and Gatekeeper will warn.
* **Linux** — the standalone folder is self-contained; ship it as-is, in a
  tarball, or wrap it with AppImage/deb tooling.

## Verifying a build

```bash
dist/Apeiron/apeiron --self-check | python -c "import json,sys; d=json.load(sys.stdin); assert d['ok']"
```

`--self-check` loads the native extension, opens/migrates SQLite, constructs the
QML interface offscreen and reports Qt warnings. A build that cannot do this is
not a working build, regardless of whether the compiler succeeded.
