# Platform support

This table states what the code implements and what has actually been verified.
A capability that exists in code but could not be exercised in a real environment
is marked **unverified**, not “supported”.

| Capability | Windows | macOS | Linux |
| --- | --- | --- | --- |
| Java Edition install + launch | implemented, **unverified on this platform** | implemented, **unverified on this platform** | implemented, **verified** (see TEST_REPORT) |
| Microsoft sign-in | implemented, **unverified** (no licensed test account) | implemented, **unverified** | implemented, **unverified** |
| Offline accounts | implemented | implemented | **verified** |
| OS credential store | DPAPI (implemented, unverified) | Keychain (implemented, unverified) | libsecret; **file fallback verified** |
| Fabric / Quilt loader install | implemented | implemented | **verified** (metadata fetch + profile validation) |
| Forge / NeoForge install | implemented (vendor installer + checksum) | implemented | **partially verified** — Forge Maven reachable, NeoForge Maven unreachable in the build network |
| Modrinth `.mrpack` import/export | implemented | implemented | **verified** (synthetic packs + code paths) |
| CurseForge import | implemented, requires user API key | same | **verified to fail clearly without a key** |
| Bedrock Edition | detection + activation, **unverified** | not supported | not supported |
| Nuitka packaging | config present, **unverified** | config present, **unverified** | **verified** (artifact built and self-checked) |

## Windows

* Java Edition uses the same code path as Linux; the OS-specific parts are path
  roots (`%APPDATA%\Apeiron`), the `java.exe` name, classpath separator, and
  process creation flags (`CREATE_NO_WINDOW`).
* **Bedrock** is a Microsoft Store (MSIX) application. Apeiron detects packages
  named `Microsoft.MinecraftUWP`, `Microsoft.MinecraftWindowsBeta` and
  `Microsoft.MinecraftEducationEdition` via `Get-AppxPackage`, then activates
  them with `explorer.exe shell:appsFolder\<PackageFamilyName>!App`.
  There is **no public launcher API**; activation delegates licensing and
  sign-in to Windows. Apeiron does not patch, repack or re-sign game files and
  cannot observe or control a running Bedrock session beyond `tasklist`.
  Consequence: the Bedrock path cannot be validated in CI (no Store-licensed
  Minecraft on hosted runners) and is therefore shipped as **experimental**.
* Installer generation is not included; the Nuitka output is a portable folder.

## macOS

* Java Edition only. `-XstartOnFirstThread` is already present in the official
  JVM arguments for macOS and is preserved.
* Credentials go to the login keychain via `/usr/bin/security`.
* `Apeiron.app` is produced with `--macos-create-app-bundle`. **Signing and
  notarisation are not implemented** because no Developer ID certificate is
  available; Gatekeeper will warn on first launch.
* Bedrock does not exist for macOS and is refused with an explicit error.

## Linux

* Java Edition. Runtime discovery looks at `JAVA_HOME`, `PATH`, `/usr/lib/jvm`,
  `~/.jdks` and `~/.sdkman`; the launcher never assumes the `java` on `PATH` is
  suitable — every candidate is executed with `-version` and parsed.
* Distribution dependencies: glibc, a working X11/Wayland session, OpenGL 3.2+
  (or a software rasteriser) and, for the credential store, `secret-tool`
  (package `libsecret-tools`) plus a running secret service. Without those,
  Apeiron falls back to a 0600 file and says so in the UI.
* The verified configuration is headless (`Xvfb`) + Mesa software rendering.

## Feature-level limitations that apply everywhere

| Limitation | Detail |
| --- | --- |
| Update signatures | Manifest + asset are checksum-verified over TLS; detached signatures are not implemented |
| Snapshot support | Listed and installable, but each snapshot is only as stable as Mojang's own build |
| NeoForge | Requires its Maven host to be reachable; the build network blocked it, so only the code path and error handling were exercised |
| CurseForge | Requires the user's own API key; Apeiron never mirrors those files |
| Bedrock control | Activation only; no launch arguments, no console capture, no instance management |
| Multi-account concurrency | One game process at a time per launcher instance |
