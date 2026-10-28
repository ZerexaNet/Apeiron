"""BMCLAPI-compatible URL routing, limited to public Minecraft downloads.

Never rewrite authentication, CurseForge, Modrinth, or arbitrary pack URLs.
The official URL remains the last candidate; hashes always describe the
original bytes and are checked independently of the selected mirror.
"""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

MIRROR_ROOTS = (
    "https://bsmirror.hkmc.online",
    "https://mirror.hkmc.online",
    "https://bmclapi2.bangbang93.com",
)
MOJANG_HOSTS = frozenset(
    {
        "launchermeta.mojang.com",
        "launcher.mojang.com",
        "piston-meta.mojang.com",
        "piston-data.mojang.com",
    }
)


def download_candidates(url: str) -> tuple[str, ...]:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port:
        return (url,)
    host = parsed.hostname
    path = parsed.path
    if host in MOJANG_HOSTS:
        relative = path
    elif host == "resources.download.minecraft.net":
        relative = "/assets" + path
    elif host == "libraries.minecraft.net":
        relative = "/maven" + path
    elif host in {"maven.minecraftforge.net", "files.minecraftforge.net"}:
        relative = "/maven" + path.removeprefix("/maven")
    elif host == "maven.neoforged.net" and path.startswith("/releases/net/neoforged/"):
        relative = "/maven" + path.removeprefix("/releases")
    elif host == "meta.fabricmc.net":
        relative = "/fabric-meta" + path
    elif host == "maven.fabricmc.net":
        relative = "/maven" + path
    else:
        return (url,)
    suffix = urlunsplit(("", "", relative, parsed.query, ""))
    return (*(root + suffix for root in MIRROR_ROOTS), url)
