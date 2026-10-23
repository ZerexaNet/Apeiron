# Architecture

## Goals

1. **One launcher process.** No helper daemons, no separate log service. The only child process is the game itself.
2. **Explicit boundaries.** Network, filesystem and process code is isolated behind small interfaces so that failure modes are testable without a network or a game installation.
3. **Nothing silently invented.** Metadata comes from official endpoints; unsupported combinations fail with a specific error instead of a guess.

## Layers

```
┌──────────────────────────────────────────────────────────────┐
│ QML (Qt Quick Controls, Material style)      src/apeiron/ui/qml│
│   Main.qml · pages/*.qml · Apeiron/Components/*.qml           │
├──────────────────────────────────────────────────────────────┤
│ Bridge: ui/backend.py (QObject, context property "App")       │
│   slots → thread pool → queued signals                        │
├──────────────────────────────────────────────────────────────┤
│ Services: services/app_context.py (composition root)          │
│           services/launcher.py (install → launch → monitor)   │
├──────────────────────────────────────────────────────────────┤
│ Domain: mc/ (metadata, install, java, launch)                 │
│         auth/ (microsoft, offline, credential store)          │
│         loaders/ (fabric, quilt, forge, neoforge)             │
│         modpack/ (mrpack, curseforge)                         │
│         instances/ (lifecycle, backup, export/import)         │
│         bedrock/ (Windows adapter)                            │
├──────────────────────────────────────────────────────────────┤
│ Infrastructure: net/downloader.py · core/http.py              │
│                 db/ (SQLite + migrations)                     │
│                 core/(paths, config, errors, archive, updates)│
├──────────────────────────────────────────────────────────────┤
│ Native: csrc/logcore.c → apeiron._logcore (C11 extension)     │
└──────────────────────────────────────────────────────────────┘
```

Dependencies point downward only: QML knows the bridge, the bridge knows services, services know the domain, the domain knows infrastructure. Nothing in `mc/`, `auth/` or `modpack/` imports Qt.

## Threading model

| Work | Thread | Notes |
| --- | --- | --- |
| QML rendering, model updates | Qt GUI thread | All `Backend` slots run here |
| Network + disk (install, download, launch preparation) | `QThreadPool` worker | Results delivered via queued signals |
| Log writing | Dedicated pthread created by the C core | Bounded queue, never blocks the caller |
| Game I/O draining | One daemon thread per launch | Prevents child-process pipe blocking |

`Backend` never touches a service from the GUI thread if that call can block. Long operations are wrapped in `_Task` (a `QRunnable`) and their results marshalled back with `_Signals`.

### Lifetime rules learned the hard way

- A Python `QObject` exposed to QML is owned by Python. If the last Python reference disappears, the C++ object is destroyed and every binding reading it fails with `Cannot read property … of null`. `create_engine()` therefore calls `QQmlEngine.setObjectOwnership(…, CppOwnership)`, sets a C++ parent and keeps a module-level reference.
- Registering the backend with `qmlRegisterSingletonInstance` is *not* used: in the PySide6 build used here it corrupts the composite-type registry, making sibling components fail to instantiate. The context property is the verified route.
- Language changes rebuild the component tree (`clearComponentCache()` + `load()`), because cached `qsTr` bindings are not reliably re-evaluated by `retranslate()`.
- Background tasks must finish before the engine is destroyed (`Backend.shutdown()` waits on the pool).

## Data model

SQLite (`<root>/data/apeiron.db`) with append-only migrations tracked in `PRAGMA user_version`:

| Table | Purpose |
| --- | --- |
| `instances` | Instance metadata (version, loader, memory, account, tags, play stats) |
| `accounts` | Identity metadata only — **no tokens** |
| `download_tasks` | Task state for the downloads page and post-mortem diagnosis |
| `instance_mods` | Installed content with hashes and source URL (needed for export) |
| `launches` | Launch history: timings, exit codes, log paths |
| `app_state` | Small key/value UI state |
| `instance_files` | Reserved for per-file verification records |

Credentials live in the OS credential store, referenced by `accounts.credential_ref`.

Preferences are stored in `<root>/data/settings.json` rather than SQLite: they must remain readable when the database is being migrated or repaired, and a hand-edited language/theme choice should not require SQL. Unknown keys are preserved so a newer build's options survive a downgrade.

## Installation pipeline

`JavaInstaller.install(version_id)` is idempotent and content-addressed:

1. Load `versions/<id>/<id>.json`, resolving `inheritsFrom` recursively into a merged descriptor.
2. Evaluate rule lists per library for the current OS/arch/features; resolve `name` + Maven-base `url` entries into concrete artifacts.
3. Download libraries, natives (extracted with the declared `extract.exclude` filter), the client jar, the asset index and all asset objects — each verified by SHA-1.
4. Install the optional `log4j` configuration referenced by the version JSON.
5. Report progress through `InstallStage` dataclasses, never by parsing logs.

Every step skips files whose checksum already matches, so an interrupted install resumes by rerunning it.

## Launch

`build_command()` is a pure function of the merged descriptor, the installed layout, the chosen Java runtime and `LaunchOptions`:

- structured `arguments.jvm` / `arguments.game` are rule-filtered and placeholder-expanded;
- legacy versions (`minecraftArguments`) get synthesised JVM arguments plus explicit resolution flags;
- Apeiron's own flags (`-Xmx`, `-Djava.library.path`, encoding) always win, and exactly one `-cp … <mainClass>` pair is emitted;
- the access token is redacted before the command is written to the launch log.

`GameProcess` drains stdout/stderr on a thread, mirrors output into `instances/<id>/logs/game-<timestamp>.log`, keeps a bounded console buffer for the UI and reports a structured exit status.

## Security posture

| Risk | Mitigation |
| --- | --- |
| Token leakage in logs | C core masks keyword values, `Bearer` credentials and JWT-shaped runs before writing |
| Token theft at rest | DPAPI / Keychain / libsecret; file fallback is flagged `secure=false` in the UI |
| Malicious archives | Central `core/archive.py` rejects absolute paths, `..`, drive letters, symlinks and expansion bombs |
| Download tampering | SHA-1/SHA-256 verification, atomic replace, HTTPS-only with loopback exception for tests |
| Metadata substitution | Only official hosts; no mirror fallback; redirects logged and TLS downgrade refused |
| Unverified executables | Forge/NeoForge installer SHA-1 must match the published checksum before execution, and the call requires explicit consent |
| Path traversal in instances | Instance ids validated; deletion refuses anything not a direct child of the instance root |

## Known architectural limitations

- **Update signatures**: the update manifest is checksum-verified over TLS, but detached cryptographic signatures are not implemented yet. This is stated in `SECURITY.md` and `docs/PLATFORM_SUPPORT.md` rather than described as full verification.
- **Single interpreter**: the native log core keeps global state and is not sub-interpreter safe.
- **No plugin sandbox**: mod loaders run third-party code inside the game JVM by design; Apeiron does not attempt to sandbox the game.
