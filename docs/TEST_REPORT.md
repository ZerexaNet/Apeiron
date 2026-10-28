# Test report

Recorded from the actual runs performed while implementing this change set.
Nothing below is projected, estimated or copied from a template.

## Environment

| Item | Value |
| --- | --- |
| Host | Linux x86_64 container (glibc), headless (`offscreen` / `Xvfb`) |
| Python | 3.12.3 (system interpreter; the managed runtime lacked C headers) |
| PySide6 / Qt | 6.9.3 / 6.9.3 (`PySide6-Essentials` + `PySide6-Addons`) |
| Compiler | gcc 13 (`-std=c11`) |
| Java | OpenJDK 21.0.10 (system); Temurin 17 download attempted, see note |
| Nuitka | 4.3.1 + patchelf 0.19.1 |
| Date | 2026-10-23 |

`PySide6-Addons` is required, not optional: the Material style's elevation
effect depends on `Qt5Compat.GraphicalEffects`, which ships only with Addons.
Without it, controls that request elevation fail to instantiate.

## 1. Native C core (standalone, no Python)

```bash
gcc -std=c11 -O2 -Wall -Wextra -pthread csrc/logcore.c csrc/test_logcore.c -o build/test_logcore
./build/test_logcore
```

```
44 checks, 0 failures
ALL C LOG CORE TESTS PASSED
```

Covers: write-before-init rejection, flush durability, level filtering, UTF-8
round-trip with Chinese text, rotation and retention, 6 threads × 150 records
with zero loss, redaction of `Authorization: Bearer …`, JSON tokens and
JWT-shaped runs, invalid UTF-8 rejection, 100 KB message truncation, idempotent
shutdown, and an unwritable path returning `APEIRON_ERR_IO` with a populated
`last_error`.

## 2. Python test suite

```bash
python setup.py build_ext --inplace
PYTHONPATH=src python -m pytest tests -q
```

```
172 passed in 14.35s
```

172 passed, 0 failed, 0 skipped. The count grew from 169 to 172 when three
regression tests were added for the client-jar parsing bug described in §5.

## 3. Static analysis

```bash
ruff check src tests tools packaging     # All checks passed!
ruff format --check src tests tools      # 65 files already formatted
mypy src/apeiron                         # Success: no issues found in 49 source files
python tools/build_translations.py --check   # translation catalogue is complete
```

`mypy` excludes `apeiron.ui.*` and `apeiron.__main__` by configuration: PySide6
exposes Qt properties as descriptors, so the bridge produces only false
positives under static analysis. That layer is covered by §4 instead.

## 4. Headless GUI (real QML, own process)

```bash
QT_QPA_PLATFORM=offscreen python tools/gui_smoke.py /tmp/gui-root
```

```
ok: True   checks: 33
```

33/33 checks passed: window created (1280×840, visible, title `Apeiron`), all
eight pages instantiated, default UI text in Simplified Chinese, switch to
English and back, offline account creation, instance create/select/delete with
directory assertions, theme persistence, About/legal payload (company, contact,
disclaimer), page navigation, and **zero Qt warnings** (any warning fails the
run).

## 5. Nuitka packaging

```bash
python packaging/build_nuitka.py --output-dir dist
```

```
Nuitka: Successfully created 'dist/entry.dist/Apeiron'
self-check: ok
done
EXIT=0                      # 294 s
```

| Artifact check | Result |
| --- | --- |
| Distribution size | 437 MB (`dist/entry.dist/`) |
| Entry point starts | `Apeiron 0.1.0` |
| Built-in `--self-check` | `ok: true` |
| Native extension inside artifact | `apeiron/_logcore.cpython-312-x86_64-linux-gnu.so` — `loaded: true` |
| QML inside artifact | `apeiron/ui/qml/Main.qml`, `Apeiron/Components/qmldir` — window loaded |
| Translation catalogues inside artifact | `apeiron_zh_CN.qm`, `apeiron_en_US.qm` |
| SQLite initialised in a fresh data root | `data/apeiron.db` created, integrity `ok` |
| Credential directory created | `data/credentials/` |
| Qt warnings during load | 0 |

Source-tree self-check, for comparison:

```
ok: True | qml: True | native: True | warnings: 0 | db schema: 3 | zh_CN translated
```

## 6. Real Minecraft end-to-end

```bash
PYTHONPATH=src python tools/e2e_minecraft.py \
    --version 1.20.1 --data-dir /tmp/apeiron-e2e \
    --java-path /usr/bin/java --report /tmp/e2e.json
```

```
PASS bootstrap     {'data_root': '/tmp/apeiron-e2e'}
PASS manifest      {'count': 920, 'latest': '26.3'}
PASS version_json  {'java': 17, 'libraries': 88}
PASS java_runtime  {'version': '21.0.10', 'major': 21, 'managed': False}
PASS install       {'seconds': 533.2, 'classpath': 89, 'assets': '5', 'problems': []}
PASS instance      {'id': 'e41681eed48c4a56b52c365a0f67f6a0'}
PASS process_started {'pid': 68583}
PASS game_running  {'signal': 'Setting user: E2ETester',
                    'still_running_before_stop': True, 'exit_code': 143, 'log_lines': 72}
PASS game_log      {'bytes': 32079}
ok: True
```

What this proves, end to end and without mocks:

- the official manifest (920 versions, current latest `26.3`) was fetched;
- the 1.20.1 version JSON was resolved (88 libraries, `javaVersion.majorVersion 17`);
- the full installation completed — client jar, libraries, natives, asset index
  `5` and all asset objects — and `installer.verify()` reported **no problems**;
- an offline profile and an isolated instance directory were created;
- the command was built from the version's own arguments, a JVM was selected,
  and the process started (PID 68583);
- the client reported **`Setting user: E2ETester`**, then loaded resources
  (`Reloading ResourceManager: vanilla`, `Found unifont_all_no_pua…`), i.e. it
  reached its menu rather than crashing at startup;
- it was still running when stopped deliberately, exited with 143 (SIGTERM),
  and the per-launch log captured 76 lines / 32 KB.

Non-fatal log noise in this environment: the narrator failed to load `flite`
(no speech library in the container) and no audio device exists. Neither affects
game startup; both are expected headless artefacts.

## 7. Failures found during development and fixed

Real defects uncovered by running things rather than reading them:

| # | Symptom | Cause | Fix |
| --- | --- | --- | --- |
| 1 | Libraries allowed on every OS | `rules_allow` defaulted to allow when a rule list existed | deny-by-default for explicit rule lists, matching the vanilla launcher |
| 2 | Child version lost its parent's Java requirement | `java_major` defaulted to 8 instead of "unspecified" | `0` sentinel + `required_java` property |
| 3 | Mod-loader library override not applied | library merge compared full coordinates; versions differ | merge by `group:artifact` |
| 4 | World restore wrote into a moved directory | backup restored into `saves/` after moving `saves` aside | move aside as a sibling of `saves` |
| 5 | **Every install failed: "declares no client download"** | `downloads.client` has no Maven `path`; `Artifact.from_dict` requires one | build the client and log4j artefacts explicitly; 3 regression tests |
| 6 | QML: `Material.onSurface`/`dialogColor` undefined | those are Material 3 names; Qt's Material style is M2 | explicit design tokens in `Theme.qml` |
| 7 | Component types failed to instantiate in pages | same directory imported through several relative paths → duplicate type registries | single URI module `Apeiron.Components` |
| 8 | `App is null` in every binding | Python-owned QObject exposed to QML was garbage-collected | C++ ownership + C++ parent + module-level reference |
| 9 | Language switch had no visible effect | cached `qsTr` bindings are not reliably re-evaluated by `retranslate()` | rebuild the component tree on language change |
| 10 | Material `ToolBar` unavailable | `Qt5Compat.GraphicalEffects` absent (Essentials only) and `QT_QUICK_BACKEND=software` forced | install Addons; stop forcing the software backend |
| 11 | GUI tests crashed in teardown | Qt destruction order inside a shared pytest process | GUI verification runs in its own process |

## 8. Not verified, and why

| Item | Status | Reason / evidence |
| --- | --- | --- |
| Microsoft sign-in, end to end | **not executed** | Requires an Azure AD public client id owned by the publisher plus a licensed test account. Implementation follows the official endpoints; `begin()` fails with an actionable error when no client id is configured (unit-tested). |
| Windows build and Bedrock activation | **not executed** | No Windows host or Store-licensed Minecraft available. Bedrock code is detection + `shell:appsFolder` activation only; macOS/Linux refusal is unit-tested. |
| macOS build, signing, notarisation | **not executed** | No macOS host and no Developer ID certificate. `--macos-create-app-bundle` is configured in the build script. |
| NeoForge installation | **not executed** | `maven.neoforged.net` was unreachable from this network. Forge's Maven and the promotions JSON were reachable. |
| Managed Temurin download | **partially executed** | Adoptium API lookup succeeded (package resolved); the CDN transfer was throttled to ~25 kB/s here, so the run used the system Java 21 via `--java-path`. The download/verify/extract path is unit-tested with a stub transport. |
| CurseForge pack with real files | **not executed** | Needs the user's own API key. The key-less path is verified to fail with a specific, actionable error. |
| Modrinth pack with real CDN downloads | **not executed** | Pack parsing, overrides extraction, path-traversal rejection and record creation were tested with synthetic `.mrpack` archives and a stub downloader; no live `cdn.modrinth.com` fetch was performed. |
| Remote CI runs | **not executed** | No GitHub Actions run existed before this PR. The workflows are validated locally (see §9). |
| Update download from the release feed | **not executed** | No published release exists yet; checksum, host allow-list and version comparison are unit-tested. |

## 9. Local CI-equivalent checks

| Workflow job | Locally reproduced |
| --- | --- |
| `lint` | `ruff check` + `ruff format --check` — clean |
| `types` | `mypy src/apeiron` — clean |
| `c-core` | gcc build + 44-assertion harness — passed |
| `tests` | `pytest` on Python 3.12 — 172 passed |
| `i18n` | `--check` passed; both `.qm` files generated with 179 translations each |
| `qml` | `gui_smoke.py` — 33/33 |
| `build` | `build_nuitka.py` on Linux — artifact built and self-checked |
| `workflow-lint` | both workflow files parse and declare jobs |

YAML validation of `.github/workflows/ci.yml` and `build.yml` was performed with
PyYAML (jobs present, no syntax errors). No remote Actions result is claimed.

## 11. Remote CI (actual GitHub Actions results)

Workflow `CI` on commit `2363f88` — **all 14 jobs succeeded**:

| Job | ubuntu-24.04 | macos-14 | windows-2022 |
| --- | --- | --- | --- |
| Lint and format | success | — | — |
| Type check | success | — | — |
| Translation completeness | success | — | — |
| QML load and UI smoke | success | — | — |
| Workflow syntax | success | — | — |
| C logging core | success | success | success |
| Tests (py3.10) | success | success | success |
| Tests (py3.12) | success | success | success |

Workflow `Build` on the same commit:

| Platform | Result |
| --- | --- |
| Ubuntu 22.04 | **success** — Nuitka artifact built, self-checked inside CI, uploaded |
| macOS 14 | **failure** — see below |
| Windows 2022 | **failure** — see below |

### What the CI loop actually caught

The first push was not green, and the failures were real:

1. `packaging/` had never been linted locally (unused `noqa`, outdated version guard).
2. The translation completeness checker parsed `qsTr` line-by-line and produced
   false positives for lines containing two calls; it now uses a regular
   expression over each file (186 occurrences verified).
3. pip installs used backslash continuations, which are a parse error under
   PowerShell — the default shell on Windows runners.
4. Update-check tests hard-coded a `linux` asset entry, so the checker correctly
   found nothing on macOS/Windows; they now use `current_platform_key()`.
5. One launch test compared `argv[0]` with `/usr/bin/java`, which renders with
   backslashes on Windows.
6. The GUI smoke assumed Chinese output while the product default is "follow
   system" and runners are English; the run now pins a Chinese locale.
7. The UI requested font families that were absent on the macOS runner, making
   Qt build alias tables and warn; families are now filtered against the ones
   actually installed.
8. Nuitka could not see the package in a clean checkout; the build now performs
   an idempotent editable install first.
9. The build workflow looked for `__main__.dist` while Nuitka names the
   directory after the compiled entry script (`entry.dist`).
10. Nuitka copied the logging extension both as a data file and as a shared
    library, colliding on macOS with `NotADirectoryError`; that scan is excluded.

### Unresolved at the time of writing

* **macOS and Windows Nuitka packaging still fail in CI.** The macOS job
  progressed past the duplicate-copy crash but fails later in the Nuitka stage;
  the Windows job fails as well. Neither could be reproduced here because no
  macOS or Windows host was available, so the cause is **not diagnosed** and no
  artifact is claimed for those platforms. Linux packaging is verified both
  locally (§5) and in CI.
* **Windows GUI smoke** crashes the offscreen scene graph (0xC0000005) on the
  GitHub runner and is skipped there with an explicit reason; the CI `qml` job
  runs on Linux, where it passes. Windows GUI behaviour is therefore unverified.

## 12. Reproducing this report

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
python setup.py build_ext --inplace
python tools/build_translations.py
pytest -q
cc -std=c11 -O2 -pthread csrc/logcore.c csrc/test_logcore.c -o build/test_logcore && ./build/test_logcore
QT_QPA_PLATFORM=offscreen python tools/gui_smoke.py /tmp/gui-root
python packaging/build_nuitka.py --output-dir dist
xvfb-run -a python tools/e2e_minecraft.py --version 1.20.1 --data-dir /tmp/e2e
```

## 13. PR #1 review remediation (2026-10-28)

The follow-up repair covers the 35 inline review findings, including the previously
outdated artifact-directory finding. Regression coverage is in
`tests/test_review_regressions.py`; invalid historical modpack hash fixtures now contain
real digests and successful loader installation is explicitly stubbed.

Verified locally on Linux / Python 3.12 / PySide6 6.9.3:

- C11 harness: 44 checks, 0 failures.
- Ruff and mypy: clean after remediation.
- Mirror mapping, priority, corrupt-source fallback, SHA-512-only downloads, streaming
  downgrade rejection and OAuth HTTP 400 protocol states are exercised with controlled
  responses rather than relying on live network availability.
- Regression tests cover settings corruption, macOS classifier names, failed world
  restore, symlink export, bounded overrides, legacy asset layouts, loader rollback,
  content categories, CurseForge records, launch failures/session duration, managed Java
  persistence, file URL conversion and shutdown settings.

Historical sections above describe the previous revision. The latest macOS failure was
identified as `Apeiron`/`apeiron` filesystem collision; Windows failed its otherwise
successful package self-check because offscreen Qt did not discover the Windows fonts.
Both causes are addressed in this revision. Cross-platform package results must be read
from the follow-up PR's Actions runs; local Linux tests do not certify Windows/macOS.
Live mirror reachability and a licensed Microsoft sign-in are not certified by these
mocked protocol tests. The local network returned HTTP 503 for the bsmirror manifest
probe. See `docs/DOWNLOAD_SOURCES.md` for routing and configuration.
