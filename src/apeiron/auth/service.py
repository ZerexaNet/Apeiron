"""Account service: the only place that talks to providers and storage.

Responsibilities

* create/rename/remove local offline profiles;
* run the Microsoft device-code flow and persist the resulting session in the
  OS credential store (never in SQLite, never in logs);
* hand a valid :class:`Session` to the launcher, refreshing it when needed and
  failing loudly when refresh is impossible.

The service distinguishes *verified* (Microsoft) from *unverified* (offline)
identities everywhere, so the UI cannot accidentally present an offline
profile as a licensed account.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from apeiron.auth.base import Session
from apeiron.auth.microsoft import DeviceCodeInfo, MicrosoftAuth
from apeiron.auth.offline import create_offline_profile, rename_profile, validate_name
from apeiron.auth.store import CredentialStore, StoreInfo, store_info
from apeiron.core.errors import AccountError, AuthExpiredError
from apeiron.core.http import HttpClient
from apeiron.core.logging import get_logger
from apeiron.db.models import Account
from apeiron.db.repositories import AccountRepository

log = get_logger("accounts")

ACCOUNT_KEY_PREFIX = "account:"


@dataclass
class PendingLogin:
    """Opaque handle for an in-flight device-code sign-in."""

    info: DeviceCodeInfo
    cancel: threading.Event

    def public(self) -> dict[str, Any]:
        return self.info.public()


class AccountService:
    def __init__(
        self,
        repo: AccountRepository,
        store: CredentialStore,
        http: HttpClient,
        *,
        microsoft_client_id: str = "",
    ) -> None:
        self.repo = repo
        self.store = store
        self.http = http
        self.microsoft = MicrosoftAuth(http, microsoft_client_id)

    # -- queries ------------------------------------------------------------
    def list(self) -> list[Account]:
        return self.repo.list()

    def get(self, account_id: str) -> Account:
        return self.repo.get(account_id)

    def backend(self) -> StoreInfo:
        return store_info(self.store)

    def default_account(self) -> Account | None:
        accounts = self.list()
        if not accounts:
            return None
        microsoft = [a for a in accounts if a.kind == "microsoft" and a.verified]
        return microsoft[0] if microsoft else accounts[0]

    # -- offline accounts ---------------------------------------------------
    def add_offline(self, name: str) -> Account:
        profile = create_offline_profile(name)
        existing = self.repo.find_by_uuid(profile.uuid)
        if existing is not None:
            raise AccountError(f"an offline profile named {profile.name} already exists")
        for account in self.list():
            if account.name.lower() == profile.name.lower():
                raise AccountError(f"the name {profile.name} is already used by another account")
        account = Account(
            id=uuid.uuid4().hex,
            kind="offline",
            name=profile.name,
            uuid=profile.uuid,
            verified=False,
            credential_ref="",
        )
        self.repo.add(account)
        log.info("created offline profile %s", profile.name)
        return account

    def rename_offline(self, account_id: str, new_name: str) -> Account:
        account = self.repo.get(account_id)
        if account.kind != "offline":
            raise AccountError(
                "Microsoft account names are managed by Microsoft and cannot be changed here"
            )
        validated = validate_name(new_name)
        for other in self.list():
            if other.id != account_id and other.name.lower() == validated.lower():
                raise AccountError(f"the name {validated} is already used by another account")
        profile = rename_profile(
            type("_P", (), {"name": account.name, "uuid": account.uuid})(),  # type: ignore[arg-type]
            validated,
        )
        account.name = profile.name
        account.uuid = profile.uuid
        self.repo.update(account)
        log.info("renamed offline profile to %s", profile.name)
        return account

    def remove(self, account_id: str) -> None:
        account = self.repo.get(account_id)
        self._delete_credentials(account)
        self.repo.delete(account_id)
        log.info("removed account %s (%s)", account.name, account.kind)

    # -- Microsoft ----------------------------------------------------------
    def begin_microsoft_login(self) -> PendingLogin:
        info = self.microsoft.begin()
        return PendingLogin(info=info, cancel=threading.Event())

    def complete_microsoft_login(
        self,
        pending: PendingLogin,
        *,
        on_status: Callable[[str], None] | None = None,
    ) -> Account:
        session = self.microsoft.wait_for_token(
            pending.info, cancel=pending.cancel, on_status=on_status
        )
        existing = self.repo.find_by_uuid(session.uuid)
        if existing is not None:
            account = existing
            account.name = session.player_name
            account.verified = True
            account.skin_model = session.skin_model
            self.repo.update(account)
        else:
            account = Account(
                id=uuid.uuid4().hex,
                kind="microsoft",
                name=session.player_name,
                uuid=session.uuid,
                microsoft_id=session.xuid,
                skin_model=session.skin_model,
                verified=True,
                credential_ref=f"{ACCOUNT_KEY_PREFIX}{uuid.uuid4().hex}",
            )
            account.credential_ref = f"{ACCOUNT_KEY_PREFIX}{account.id}"
            self.repo.add(account)
        self._save_session(account, session)
        self.repo.touch(account.id)
        return account

    def refresh(self, account_id: str) -> Session:
        account = self.repo.get(account_id)
        if account.kind != "microsoft":
            return self._offline_session(account)
        stored = self.store.get(self._key(account))
        if not stored:
            raise AuthExpiredError("no stored session for this account; sign in again")
        session = Session(
            player_name=str(stored.get("player_name", account.name)),
            uuid=str(stored.get("uuid", account.uuid)),
            access_token=str(stored.get("access_token", "")),
            user_type="msa",
            xuid=str(stored.get("xuid", account.microsoft_id)),
            client_id=self.microsoft.client_id,
            verified=True,
            expires_at=float(stored.get("expires_at", 0)),
            refresh_token=str(stored.get("refresh_token", "")),
            skin_model=str(stored.get("skin_model", "classic")),
        )
        if not session.expires_soon and session.access_token:
            return session
        try:
            refreshed = self.microsoft.refresh(session)
        except AuthExpiredError:
            self._delete_credentials(account)
            raise
        self._save_session(account, refreshed)
        return refreshed

    def session_for(self, account_id: str) -> Session:
        account = self.repo.get(account_id)
        if account.kind == "offline":
            self.repo.touch(account.id)
            return self._offline_session(account)
        session = self.refresh(account_id)
        self.repo.touch(account.id)
        return session

    def logout(self, account_id: str) -> None:
        account = self.repo.get(account_id)
        if account.kind == "microsoft":
            self._delete_credentials(account)
            account.verified = False
            self.repo.update(account)
        log.info("logged out %s", account.name)

    # -- internals ----------------------------------------------------------
    @staticmethod
    def _offline_session(account: Account) -> Session:
        return Session(
            player_name=account.name,
            uuid=account.uuid,
            access_token="0",
            user_type="legacy",
            verified=False,
            skin_model=account.skin_model or "classic",
        )

    @staticmethod
    def _key(account: Account) -> str:
        return account.credential_ref or f"{ACCOUNT_KEY_PREFIX}{account.id}"

    def _save_session(self, account: Account, session: Session) -> None:
        self.store.set(
            self._key(account),
            {
                "player_name": session.player_name,
                "uuid": session.uuid,
                "access_token": session.access_token,
                "refresh_token": session.refresh_token,
                "expires_at": session.expires_at,
                "xuid": session.xuid,
                "skin_model": session.skin_model,
            },
        )

    def _delete_credentials(self, account: Account) -> None:
        try:
            self.store.delete(self._key(account))
        except Exception as exc:
            log.warning("could not delete stored credential: %s", exc)


def describe(account: Account) -> dict[str, Any]:
    """Stable, UI-friendly projection with an explicit verification flag."""
    return {
        "id": account.id,
        "name": account.name,
        "kind": account.kind,
        "uuid": account.uuid,
        "verified": account.verified,
        "skinModel": account.skin_model,
        "statusKey": "account.verified" if account.verified else "account.offlineLocal",
    }
