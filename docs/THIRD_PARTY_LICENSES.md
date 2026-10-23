# Third-party components and licences

Apeiron itself is distributed under the Mozilla Public License 2.0 (`LICENSE`).

## Runtime dependencies

| Component | Version | Licence | Use |
| --- | --- | --- | --- |
| Python | 3.10+ | PSF License 2.0 | interpreter |
| PySide6 / PySide6-Essentials / PySide6-Addons | 6.9.x | LGPL-3.0 (with Qt commercial alternative) | Qt bindings, QML runtime, Qt5Compat effects |
| Qt 6 (QtCore, QtGui, QtQml, QtQuick, QtQuickControls2, Qt5Compat) | 6.9.x | LGPL-3.0 / GPL-3.0 (dual) | UI toolkit |
| Shiboken6 | 6.9.x | LGPL-3.0 | PySide6 binding generator runtime |
| SQLite | bundled with Python | Public Domain | local database |

**LGPL compliance note.** PySide6 and Qt are used as dynamically linked
libraries. The Nuitka distribution ships them as separate shared libraries in
the artifact; Apeiron does not statically link Qt, and users can replace the Qt
libraries in the distribution folder. Apeiron's own source is available under
MPL-2.0. Any distribution of binaries must keep the LGPL notices and licence
texts alongside the artifact.

## Build and test tooling (not shipped)

| Component | Licence |
| --- | --- |
| Nuitka | Apache-2.0 |
| zstandard | BSD-3-Clause |
| ordered-set | MIT |
| pytest, pytest-timeout, pytest-qt | MIT |
| ruff | MIT |
| mypy | MIT |

## External services and content

| Service | Terms |
| --- | --- |
| Mojang version manifest, assets, libraries | Mojang/Microsoft terms; Apeiron only downloads, never redistributes |
| Microsoft identity platform, Xbox Live, Minecraft services | Microsoft terms; device-code flow only, no password handling |
| Eclipse Adoptium (Temurin) | GPL-2.0 with Classpath Exception; downloaded at runtime, not bundled |
| Fabric / Quilt meta | respective project licences; only JSON metadata is fetched |
| Forge / NeoForge Maven | respective project licences; the vendor installer is downloaded, checksum-verified and executed with user consent |
| Modrinth | API terms; `.mrpack` files reference downloads, binaries are never re-hosted |
| CurseForge | API terms; requires the user's own API key, no mirroring |

## Game content

Minecraft, its assets, sounds, textures and libraries are the property of
Mojang Studios/Microsoft. Apeiron does **not** include or redistribute game
files. Clients, libraries and assets are downloaded from Mojang's official
servers at the user's request, under the user's own account or local profile.

## Assets shipped with Apeiron

The interface uses Unicode glyphs and colours defined in
`src/apeiron/ui/qml/Apeiron/Components/Theme.qml`. No icon font, brand logo or
Minecraft artwork is bundled. The name “Apeiron” and the ✦ mark belong to
Zerexa Ltd; “Minecraft” is a trademark of Microsoft and is used only descriptively.
