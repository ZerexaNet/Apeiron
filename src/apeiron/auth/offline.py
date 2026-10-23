"""Offline ("local") accounts, similar to PCL/HMCL offline profiles.

An offline profile is a *local identity only*: it is never presented as a
Microsoft account, never claims ownership of Minecraft, and cannot
authenticate against online servers.  Its UUID is derived deterministically
from the name using the same algorithm the vanilla server uses for offline
players, so single-player worlds and LAN games behave predictably.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass

from apeiron.core.errors import AccountError

NAME_PATTERN = re.compile(r"^[A-Za-z0-9_]{3,16}$")
OFFLINE_NAMESPACE = "OfflinePlayer:"
MAX_NAME_LENGTH = 16
MIN_NAME_LENGTH = 3


def validate_name(name: str) -> str:
    """Validate a player name and return it stripped.

    Rules mirror the vanilla client: 3–16 characters, ASCII letters, digits and
    underscore.  Rejecting other input up front avoids a confusing failure
    inside the game.
    """
    if not isinstance(name, str):
        raise AccountError("player name must be text")
    candidate = name.strip()
    if len(candidate) < MIN_NAME_LENGTH:
        raise AccountError(f"player name must be at least {MIN_NAME_LENGTH} characters")
    if len(candidate) > MAX_NAME_LENGTH:
        raise AccountError(f"player name must be at most {MAX_NAME_LENGTH} characters")
    if not NAME_PATTERN.match(candidate):
        raise AccountError("player name may only contain letters, digits and underscores")
    return candidate


def offline_uuid(name: str) -> uuid.UUID:
    """UUID v3 of ``OfflinePlayer:<name>`` (Java's offline player algorithm)."""
    digest = hashlib.md5((OFFLINE_NAMESPACE + name).encode("utf-8")).digest()
    # Force RFC 4122 version 3 bits, exactly like UUID.nameUUIDFromBytes.
    return uuid.UUID(bytes=digest, version=3)


@dataclass
class OfflineProfile:
    name: str
    uuid: str

    @property
    def uuid_hex(self) -> str:
        return self.uuid.replace("-", "")

    @property
    def short_uuid(self) -> str:
        return self.uuid_hex[:8]

    @property
    def avatar_seed(self) -> str:
        return self.uuid_hex


def create_offline_profile(name: str) -> OfflineProfile:
    validated = validate_name(name)
    return OfflineProfile(name=validated, uuid=str(offline_uuid(validated)))


def rename_profile(profile: OfflineProfile, new_name: str) -> OfflineProfile:
    """Renaming keeps the *same* identity only if the caller wants it to.

    Vanilla derives the UUID from the name, so a renamed offline profile gets a
    new identity by definition; we return the new profile with a freshly
    derived UUID and document that inventories tied to the old UUID are not
    carried over automatically.
    """
    validated = validate_name(new_name)
    return OfflineProfile(name=validated, uuid=str(offline_uuid(validated)))


SUPPORTED_NOTE = (
    "An offline profile is a local, unverified identity. It does not prove "
    "game ownership and will be rejected by online servers that require "
    "authentication (including Realms)."
)
