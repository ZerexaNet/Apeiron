"""Authentication provider interface.

A provider turns a user action into a :class:`Session` that the launch layer can
consume.  The interface exists so that additional *legitimate* providers can be
added later; it deliberately does not implement any unofficial protocol, and a
provider must not exist until its protocol documentation has been reviewed.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class Session:
    """A usable game session."""

    player_name: str
    uuid: str
    access_token: str = "0"
    user_type: str = "legacy"  # msa | legacy
    xuid: str = ""
    client_id: str = ""
    verified: bool = False
    expires_at: float = 0.0
    refresh_token: str = ""
    skin_model: str = "classic"
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def expired(self) -> bool:
        return bool(self.expires_at) and time.time() >= self.expires_at

    @property
    def expires_soon(self) -> bool:
        """True when the token expires within five minutes."""
        return bool(self.expires_at) and time.time() >= self.expires_at - 300

    def redacted(self) -> dict[str, Any]:
        return {
            "player_name": self.player_name,
            "uuid": self.uuid,
            "user_type": self.user_type,
            "verified": self.verified,
            "expires_at": self.expires_at,
        }

    def __repr__(self) -> str:  # never leak tokens through repr()
        return (
            f"Session(player={self.player_name!r}, verified={self.verified}, "
            f"expired={self.expired})"
        )


@runtime_checkable
class AuthProvider(Protocol):
    """Minimal contract implemented by account providers."""

    id: str
    display_name: str
    requires_network: bool

    def begin(self) -> Any:
        """Start an interactive flow and return its first step."""

    def complete(self, *args: Any, **kwargs: Any) -> Session:
        """Finish the flow and return a session."""

    def refresh(self, session: Session) -> Session:
        """Refresh an expired session, or raise AuthExpiredError."""

    def logout(self, session: Session) -> None:
        """Invalidate local state for ``session``."""
