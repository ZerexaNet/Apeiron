# Security policy

## Reporting a vulnerability

Email **admin@zerexa.net** with:

- affected version (`apeiron --version`) and platform,
- reproduction steps,
- impact assessment,
- whether a proof of concept exists.

Please do **not** open a public issue for an unpatched vulnerability. We aim to
acknowledge within 5 working days. Please allow time for a fix before disclosure.

## What Apeiron handles

| Data | Where it lives | Protection |
| --- | --- | --- |
| Microsoft / Minecraft tokens | OS credential store | DPAPI, Keychain, or libsecret; file fallback is 0600 and flagged insecure in the UI |
| Account names and UUIDs | SQLite | not secret |
| Settings, instance metadata | JSON + SQLite | not secret |
| Launch logs | `instances/<id>/logs/` | tokens redacted by the native core before writing |
| Game assets | shared cache | checksum-verified downloads |

Apeiron never receives or stores a Microsoft password: sign-in uses the OAuth 2.0
device-code flow, where the password is entered in Microsoft's own page.

## Controls implemented

- **Log redaction (C core):** keyword values (`access_token`, `refresh_token`,
  `password`, `authorization`, `bearer`, `secret`, `session`, …), `Bearer`
  credentials and JWT-shaped runs are masked before a record reaches disk.
- **Token hygiene:** `Session.__repr__` never prints tokens; the launch log
  records the command line with `--accessToken` masked; diagnostic exports run
  the log tail through the same redactor again.
- **Transport:** HTTPS only (plain HTTP permitted solely for loopback in tests),
  TLS 1.2 minimum, TLS-downgrade redirects refused.
- **Downloads:** SHA-1/SHA-256 verification, atomic replace, bounded size, disk
  space and writability pre-checks, no silent mirror fallback.
- **Archives:** central extractor rejects absolute paths, `..`, drive letters,
  symlinks, hard links and expansion bombs.
- **Executables:** the Forge/NeoForge installer is executed only after its
  published SHA-1 matches, and only with explicit user consent.
- **Instance isolation:** ids validated; deletion refuses anything that is not a
  direct child of the instance root, and never the root itself.
- **Updates:** HTTPS, host allow-list, SHA-256 manifest check, never auto-executed.

## Known gaps (honest list)

1. **No detached signatures on updates.** The manifest and asset are protected by
   TLS and a checksum from the same origin. A compromised origin could serve a
   matching payload. Code signing of releases and Ed25519-signed manifests are
   planned, not implemented.
2. **macOS builds are unsigned and not notarised** (no Developer ID available), so
   Gatekeeper warnings must be expected.
3. **File credential fallback** is obfuscation, not encryption. It is used only
   when no OS secret service exists, and the UI says so.
4. **Third-party mods are arbitrary code** executed inside the game JVM by design.
   Apeiron verifies download integrity, not the safety of mod behaviour.
5. **Microsoft sign-in requires a client id you register**, because shipping a
   third-party application id would be misuse of that registration.

## Scope

In scope: the launcher, its native extension, packaging, and the update path.
Out of scope: vulnerabilities in Minecraft itself, mod loaders, or mods; issues
that require an already-compromised machine; and the absence of features listed
above as known gaps.
