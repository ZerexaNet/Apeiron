# Download sources

Minecraft downloads use this fixed priority, enabled by the application composition root:

1. `https://bsmirror.hkmc.online`
2. `https://mirror.hkmc.online`
3. `https://bmclapi2.bangbang93.com`
4. The original official URL.

Routing follows the [BMCLAPI compatibility documentation](https://bmclapidoc.bangbang93.com/):
Mojang metadata/client paths are preserved, assets use `/assets`, Maven libraries use
`/maven`, and Fabric metadata uses `/fabric-meta`. NeoForge release Maven paths strip
`/releases` before being placed under `/maven`. Quilt remains on its official endpoints
because BMCLAPI documents its Quilt API as unavailable. Java provisioning uses Adoptium's
official API and SHA-256 validation.

Only explicitly recognized public download hosts are rewritten. Microsoft/Xbox/Minecraft
account APIs, CurseForge API calls, Modrinth downloads and arbitrary modpack URLs retain
their original destinations. HTTPS is required, including after streaming redirects.

A failed or corrupt mirror advances to the next source. Each file attempt starts a new
`.part` file, verifies size and all supplied SHA-1/SHA-512 digests, and atomically commits
only verified bytes. Download progress counts successful bytes once and removes bytes
from discarded attempts. Independent batches are serialized so their cancellation and
progress state cannot overwrite one another. Version JSON is checked against the
manifest's SHA-1 before parsing or persistence.

Mirrors do not remove the need for a licensed account or change authentication.

## CurseForge configuration

Set `APEIRON_CURSEFORGE_API_KEY` in the launcher's environment to your own API key before
starting Apeiron. No shared key is embedded, persisted in settings, or sent to mirrors.
The UI reports this configuration path when an import requires a key.

## CI and release builds

CI validates pull requests and `main`; package builds run for pull requests affecting
application/build files, pushes to `main`, version tags and manual dispatch. All three
platform packages must pass their packaged self-check. Version tags (`v*`) publish the
three archives in a GitHub Release after packaging succeeds; ordinary PR builds only
upload workflow artifacts. Release permissions are granted only to the tag release job.

The executable is named `ApeironLauncher` (`ApeironLauncher.exe` on Windows). This avoids
the case-insensitive filesystem collision between an `Apeiron` executable and the
`apeiron/` extension/data directory on macOS. Nuitka collects `_logcore` as an extension
module, not as an untracked data file. Windows offscreen checks explicitly discover the
installed Windows fonts; QML warnings remain fatal.
