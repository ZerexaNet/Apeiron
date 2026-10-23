"""Error taxonomy shared by the core, the UI bridge and the CLI.

Every error carries a stable machine code plus translation keys, so the QML
layer can render a localised message without knowing anything about the
originating subsystem.  Credentials must never be placed in ``detail``.
"""

from __future__ import annotations

from typing import Any


class ApeironError(Exception):
    """Base class for all expected Apeiron failures."""

    code: str = "apeiron.error"
    message_key: str = "error.generic"
    hint_key: str | None = None

    def __init__(
        self,
        message: str = "",
        *,
        detail: str | None = None,
        hint: str | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message or self.code)
        self.message = message or self.code
        self.detail = detail
        self.hint = hint
        self.cause = cause

    def to_dict(self) -> dict[str, Any]:
        """Serialisable form for the QML bridge and the diagnostic report."""
        return {
            "code": self.code,
            "message": self.message,
            "messageKey": self.message_key,
            "hint": self.hint,
            "hintKey": self.hint_key,
            "detail": self.detail,
        }


class ConfigError(ApeironError):
    code = "apeiron.config"
    message_key = "error.config"


class PathError(ApeironError):
    code = "apeiron.path"
    message_key = "error.path"


class NetworkError(ApeironError):
    code = "apeiron.network"
    message_key = "error.network"


class TimeoutError_(ApeironError):
    code = "apeiron.timeout"
    message_key = "error.timeout"


class DownloadError(ApeironError):
    code = "apeiron.download"
    message_key = "error.download"


class IntegrityError(ApeironError):
    code = "apeiron.integrity"
    message_key = "error.integrity"


class DiskSpaceError(ApeironError):
    code = "apeiron.disk_space"
    message_key = "error.diskSpace"


class PermissionError_(ApeironError):
    code = "apeiron.permission"
    message_key = "error.permission"


class CancelledError(ApeironError):
    code = "apeiron.cancelled"
    message_key = "error.cancelled"


class SecurityError(ApeironError):
    code = "apeiron.security"
    message_key = "error.security"


class LaunchError(ApeironError):
    code = "apeiron.launch"
    message_key = "error.launch"


class JavaError(ApeironError):
    code = "apeiron.java"
    message_key = "error.java"


class AuthError(ApeironError):
    code = "apeiron.auth"
    message_key = "error.auth"


class AuthRequiredError(AuthError):
    code = "apeiron.auth.required"
    message_key = "error.authRequired"


class AuthExpiredError(AuthError):
    code = "apeiron.auth.expired"
    message_key = "error.authExpired"


class OwnershipError(AuthError):
    code = "apeiron.auth.ownership"
    message_key = "error.ownership"


class AccountError(ApeironError):
    code = "apeiron.account"
    message_key = "error.account"


class InstanceError(ApeironError):
    code = "apeiron.instance"
    message_key = "error.instance"


class InstanceNotFoundError(InstanceError):
    code = "apeiron.instance.missing"
    message_key = "error.instanceMissing"


class LoaderError(ApeironError):
    code = "apeiron.loader"
    message_key = "error.loader"


class ModpackError(ApeironError):
    code = "apeiron.modpack"
    message_key = "error.modpack"


class ArchiveError(ApeironError):
    code = "apeiron.archive"
    message_key = "error.archive"


class UnsupportedError(ApeironError):
    code = "apeiron.unsupported"
    message_key = "error.unsupported"


class DatabaseError(ApeironError):
    code = "apeiron.database"
    message_key = "error.database"


class BedrockError(ApeironError):
    code = "apeiron.bedrock"
    message_key = "error.bedrock"


class UpdateError(ApeironError):
    code = "apeiron.update"
    message_key = "error.update"
