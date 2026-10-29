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
    if host in {urlsplit(root).hostname for root in MIRROR_ROOTS}:
        # BMCL-compatible manifests may already contain mirror URLs. Recover
        # the upstream before routing so those links retain all four fallbacks.
        if path.startswith("/assets/"):
            upstream, path = "resources.download.minecraft.net", path.removeprefix("/assets")
        elif path.startswith("/fabric-meta/"):
            upstream, path = "meta.fabricmc.net", path.removeprefix("/fabric-meta")
        elif path.startswith("/maven/net/neoforged/"):
            upstream, path = "maven.neoforged.net", "/releases" + path.removeprefix("/maven")
        elif path.startswith("/maven/net/minecraftforge/"):
            upstream, path = "maven.minecraftforge.net", path.removeprefix("/maven")
        elif path.startswith("/maven/"):
            upstream, path = "libraries.minecraft.net", path.removeprefix("/maven")
            if path.startswith("/net/fabricmc/"):
                upstream = "maven.fabricmc.net"
        elif path.startswith("/v1/objects/"):
            upstream = "piston-data.mojang.com"
        elif path.startswith(("/v1/packages/", "/mc/game/", "/v1/products/")):
            upstream = "piston-meta.mojang.com"
        else:
            return (url,)
        return download_candidates(urlunsplit(("https", upstream, path, parsed.query, "")))
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
