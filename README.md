# Apeiron

**An independent third-party Minecraft launcher for Windows, macOS and Linux — bilingual (简体中文 / English), built with Python, PySide6 and Qt Quick.**

> Apeiron is not an official Minecraft product and is not affiliated with, endorsed by, or approved by Mojang Studios or Microsoft.
> Minecraft and related names, logos and assets belong to their respective owners. See [DISCLAIMER.md](DISCLAIMER.md) (the authoritative English text).

## What it is

Apeiron is a desktop launcher that manages isolated game instances, downloads and verifies official game files, launches Minecraft: Java Edition with the correct per-version arguments, and provides a first-class local ("offline") account mode alongside Microsoft sign-in.

- **Default UI language: 简体中文**, with English available; the choice follows the OS on first start and can be overridden permanently.
- **Java Edition** on Windows, macOS and Linux.
- **Bedrock Edition** detection and activation on Windows only (see limitations below).
- **Native C11 logging core** compiled into the application: asynchronous, rotating, and credential-redacting.

## Supported platforms and versions

| Platform | Minecraft: Java Edition | Minecraft: Bedrock Edition |
| --- | --- | --- |
| Windows 10/11 (x64) | Yes | Detection + activation only, **experimental** |
| macOS 12+ (Intel / Apple silicon) | Yes | Not supported |
| Linux (x64, glibc) | Yes | Not supported |

Java Edition support covers every release and snapshot published in the official Mojang version manifest, including legacy versions that predate the structured `arguments` format. Version inheritance (`inheritsFrom`) is honoured, so mod-loader profiles work.

**Bedrock limitations, stated precisely:** Microsoft does not publish a launcher API for Bedrock. Apeiron therefore only *detects* installed Store packages and *activates* them through `shell:appsFolder` / the `minecraft:` protocol, which hands control to Windows and the Store licence check. Apeiron never patches, repacks or re-signs game files, never bypasses entitlement checks, and cannot control a running Bedrock session. This path is not exercised in CI because no Windows runner with a Store-licensed Minecraft installation is available.

## Account modes

| Mode | Authentication | Proves game ownership | Online servers |
| --- | --- | --- | --- |
| Microsoft | Official OAuth 2.0 device-code flow + Xbox Live + Minecraft services | Yes (verified via the Minecraft profile endpoint) | Yes |
| Offline (local) | None — a local player name | **No** | Generally not; servers requiring authentication will reject it |

An offline profile is a local identity only. It is never presented as a Microsoft account, never claims a licence, and cannot be used to impersonate an authenticated player or bypass access control. See [docs/ACCOUNT_MODES.md](docs/ACCOUNT_MODES.md).

Microsoft sign-in requires an Azure AD application client id that **you** register (public client, device-code flow enabled), supplied via `APEIRON_MS_CLIENT_ID` or Settings. Apeiron deliberately ships no client id: the application must be owned by its publisher. Apeiron never asks for or stores your Microsoft password.

## Install and run

Release builds are produced by Nuitka (see [docs/BUILDING.md](docs/BUILDING.md)). From a checkout:

```bash
python -m venv .venv
. .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
python setup.py build_ext --inplace   # builds apeiron._logcore (C11)
python -m apeiron
```

Verify an installation without opening a window:

```bash
python -m apeiron --self-check
```

It prints a JSON report covering the native extension, SQLite schema/integrity, QML load status, translations and Qt warnings, and exits non-zero on any problem.

## Building

```bash
pip install -e ".[build]"
python tools/build_translations.py          # regenerate .ts and compile .qm
python packaging/build_nuitka.py            # --standalone Nuitka distribution
```

The Nuitka build embeds the QML module, the compiled translations and the native extension; the artifact is then verified with `--self-check` (see [docs/TESTING.md](docs/TESTING.md)).

## Testing

```bash
pytest -q                                   # unit + integration (169 tests)
gcc -std=c11 -O2 -pthread csrc/logcore.c csrc/test_logcore.c -o build/test_logcore && ./build/test_logcore
python tools/gui_smoke.py /tmp/gui-root     # headless UI, 33 checks
xvfb-run -a python tools/e2e_minecraft.py --version 1.20.1 --data-dir /tmp/e2e
ruff check src tests tools && ruff format --check src tests tools
mypy src/apeiron
```

Results of the most recent real run — including what could **not** be verified — are recorded in [docs/TEST_REPORT.md](docs/TEST_REPORT.md). Nothing in that report is aspirational.

## Reporting bugs

Open an issue at <https://github.com/ZerexaNet/Apeiron/issues> and attach the diagnostics file produced by **Logs & diagnostics → Export diagnostics**. It contains log statistics, recent launches and a redacted log tail; credentials are masked by the native logging core before anything is written to disk.

For security issues, follow [SECURITY.md](SECURITY.md) instead of opening a public issue.

## Documentation

| Document | Contents |
| --- | --- |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Module layout, data flow, threading model |
| [docs/PLATFORM_SUPPORT.md](docs/PLATFORM_SUPPORT.md) | Verified support matrix and known limitations |
| [docs/ACCOUNT_MODES.md](docs/ACCOUNT_MODES.md) | Microsoft flow, offline profiles, provider interface |
| [docs/BUILDING.md](docs/BUILDING.md) | Local and Nuitka builds, packaging layout |
| [docs/TESTING.md](docs/TESTING.md) | Test layers, fixtures, how to reproduce |
| [docs/TEST_REPORT.md](docs/TEST_REPORT.md) | Actual executed results |
| [docs/I18N.md](docs/I18N.md) | Language resolution rules and translation workflow |
| [docs/THIRD_PARTY_LICENSES.md](docs/THIRD_PARTY_LICENSES.md) | Dependency licences |
| [docs/DISCLAIMER_POLICY.md](docs/DISCLAIMER_POLICY.md) | How the two disclaimer files relate |

## Licence

Apeiron is distributed under the [Mozilla Public License 2.0](LICENSE). Third-party components keep their own licences; see [docs/THIRD_PARTY_LICENSES.md](docs/THIRD_PARTY_LICENSES.md).

Publisher: Zerexa Ltd · Contact: admin@zerexa.net
