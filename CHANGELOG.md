# Changelog

All notable changes to Apeiron are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
semantic versioning.

## [0.1.0] — unreleased

First implementation of the launcher.

### Added

- **Native C11 logging core** (`csrc/logcore.c`, `apeiron._logcore`): bounded
  queue, dedicated writer thread, size-based rotation with retention, console and
  file sinks, timestamps with millisecond precision, PID/TID, exception context,
  UTF-8 validation and credential redaction. Includes a standalone C test harness
  (44 assertions) and Python integration tests.
- **Launcher core**: application paths with a sandboxed instance root, typed
  error taxonomy, settings with atomic writes and schema migration, HTTP client
  restricted to TLS with retries/backoff and `Retry-After` support.
- **SQLite persistence** with three append-only migrations: instances, accounts
  (metadata only), download tasks, instance content, launch history, app state.
- **Java Edition pipeline**: official version manifest with cache, version
  inheritance, rule evaluation, Maven-base library resolution, library/native/
  client/asset downloads with checksum verification, Java runtime discovery and
  Adoptium provisioning, per-version command construction (modern and legacy),
  process supervision with pipe draining and log capture.
- **Accounts**: Microsoft OAuth 2.0 device-code flow through Xbox Live and
  Minecraft services with an explicit ownership check; offline profiles using the
  vanilla offline-UUID algorithm; OS credential storage (DPAPI, Keychain,
  libsecret) with a flagged file fallback; `AuthProvider` interface for future
  providers.
- **Mod loaders**: Fabric and Quilt via official metadata profiles; Forge and
  NeoForge via their vendor installers with mandatory SHA-1 verification and
  explicit consent.
- **Modpacks and content**: Modrinth `.mrpack` import/export, CurseForge manifest
  import gated on a user-supplied API key, per-kind content management
  (mods/resource packs/shader packs/data packs) with enable/disable.
- **Instances**: create, copy, rename, delete with path guards, world backup and
  restore, archive export/import.
- **Windows Bedrock adapter**: Store package detection and `shell:appsFolder`
  activation, explicitly limited and experimental.
- **Material-style QML interface** with eight pages (home, library, accounts,
  downloads, content, settings, logs, about), light/dark/system themes, keyboard
  shortcuts and accessibility labels.
- **Localisation**: Simplified Chinese default with English, OS detection,
  persistent manual override, Qt Linguist `.ts`/`.qm` pipeline, 179 translated
  strings, and completeness checking in the build script.
- **Security controls**: safe archive extraction, download integrity checks,
  path-traversal guards, log redaction, HTTPS-only transport, update checksum
  verification.
- **Documentation and compliance**: README, English disclaimer (authoritative),
  Chinese reference translation, contributing guide, security policy, architecture
  / platform / account / build / test / i18n / licence / disclaimer documents.
- **CI**: workflow definitions for lint, type check, C tests, Python tests,
  i18n completeness, QML load and cross-platform Nuitka builds.

### Known limitations at this version

- Microsoft sign-in requires a user-registered Azure AD client id.
- Update manifests are checksum-verified but not cryptographically signed.
- macOS artifacts are not signed or notarised.
- Bedrock support is activation-only and unverified in CI.
- CurseForge installs require the user's own API key.
