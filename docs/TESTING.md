# Testing

Four layers, each answering a different question.

| Layer | Command | Answers |
| --- | --- | --- |
| C core (standalone) | `cc … csrc/logcore.c csrc/test_logcore.c` | Does the native module behave without Python? |
| Python unit/integration | `pytest -q` | Do the domain rules hold? |
| Headless GUI | `python tools/gui_smoke.py` | Does the real QML interface load, translate and react? |
| Real game | `xvfb-run python tools/e2e_minecraft.py` | Can Apeiron actually download and start Minecraft? |

## C core

```bash
gcc -std=c11 -O2 -Wall -Wextra -pthread \
    csrc/logcore.c csrc/test_logcore.c -o build/test_logcore
./build/test_logcore
```

44 assertions covering: write-before-init, flush durability, level filtering,
UTF-8 round-trip (Chinese text), rotation and retention, 6 threads × 150 records
with no loss, redaction of `Authorization: Bearer …`, JSON tokens and
JWT-shaped runs, invalid UTF-8 rejection, oversized-message truncation,
idempotent shutdown, and an unwritable path surfacing `APEIRON_ERR_IO`.

## Python suite

```bash
pip install -e ".[dev]"
python setup.py build_ext --inplace
pytest -q
```

| Module | Focus |
| --- | --- |
| `test_native_logging.py` | extension API, filtering, threads, redaction, rotation, error codes |
| `test_core_config.py` | path sandboxing, settings clamping, corrupt file recovery, v1→v2 migration |
| `test_database.py` | migrations, idempotency, newer-schema rejection, FK cascades, transaction rollback |
| `test_downloader.py` | success, duplicate skip, checksum mismatch, retry, 404, timeout, cancel, disk-space, unwritable target, concurrency, atomicity |
| `test_version_rules.py` | OS/arch/feature rules, inheritance, argument expansion, Maven-base libraries, classpath de-duplication |
| `test_launch.py` | modern vs legacy command building, memory flags, token redaction, process I/O and stop |
| `test_accounts.py` | name rules, offline UUID vs OpenJDK, service CRUD, client-id requirement, no secrets in the DB |
| `test_security.py` | zip/tar traversal, absolute paths, symlinks, size bombs, URL policy, update checksums, log redaction |
| `test_instances.py` | isolation, copy, delete guards, backup/restore, export/import round-trip |
| `test_modpack.py` | `.mrpack` parsing, unsafe paths, overrides, import, CurseForge key gating, export |
| `test_i18n.py` | locale normalisation, fallback chain, manual override, `.qm` loading |
| `test_bedrock.py` | Appx JSON parsing, activation URI, unsupported-platform errors |

Markers: `network` (needs outbound access), `gui`, `slow`.

## Headless GUI

```bash
QT_QPA_PLATFORM=offscreen python tools/gui_smoke.py /tmp/gui-root
```

33 checks: window creation and size, all eight pages instantiated, default
Chinese strings, switching to English and back, offline account creation,
instance create/select/delete with directory assertions, theme persistence,
About/legal payload, page navigation, and **zero Qt warnings** (a warning fails
the run — this is what catches broken QML bindings).

It runs as its own process because Qt teardown inside a shared pytest process is
not deterministic; `tests/test_gui_smoke.py` invokes it and asserts the JSON.

## Real game

```bash
xvfb-run -a python tools/e2e_minecraft.py \
    --version 1.20.1 --data-dir /tmp/apeiron-e2e --report /tmp/e2e.json
```

Stages: manifest → version JSON → Adoptium JRE download/verify → full install
(client, libraries, natives, assets) → checksum verification → offline account →
instance → process start → evidence of a running client → clean stop → log file
present. Exit code 0 requires every stage, including the game actually starting.

Requires ~1 GB of disk, outbound HTTPS to Mojang/Adwaitium CDNs and an X server
(`xvfb-run`) with OpenGL. Without a display it cannot run; that is recorded as a
limitation rather than skipped silently.

## Static analysis

```bash
ruff check src tests tools
ruff format --check src tests tools
mypy src/apeiron
python tools/build_translations.py --check
```

`mypy` excludes `apeiron.ui.*` and `apeiron.__main__`: PySide6 exposes Qt
properties as descriptors, so static analysis of the bridge yields only false
positives. That layer is covered by the GUI smoke test instead.

## Failures you should expect, and why

| Symptom | Cause |
| --- | --- |
| `Type ToolBar unavailable` / `RectangularGlow is not a type` | `PySide6-Addons` missing, or `QT_QUICK_BACKEND=software` forced |
| `App is null` in QML warnings | backend object garbage-collected — check the ownership rules in ARCHITECTURE.md |
| `gui_smoke` reports untranslated strings | `.qm` not rebuilt after editing `.ts` |
| `e2e_minecraft` fails at `java_runtime` | Adoptium unreachable from the network |
