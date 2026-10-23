"""Offline profiles and the account service."""

from __future__ import annotations

from pathlib import Path

import pytest

from apeiron.auth.base import Session
from apeiron.auth.microsoft import DeviceCodeInfo, MicrosoftAuth, resolve_client_id
from apeiron.auth.offline import (
    create_offline_profile,
    offline_uuid,
    rename_profile,
    validate_name,
)
from apeiron.auth.service import AccountService
from apeiron.auth.store import FileCredentialStore
from apeiron.core.errors import AccountError, AuthError
from apeiron.db.models import Account


def test_validate_name_accepts_valid_names():
    assert validate_name("Steve") == "Steve"
    assert validate_name("  Notch_99  ") == "Notch_99"


@pytest.mark.parametrize("bad", ["", "ab", "a" * 17, "bad name", "naïve", "semi;colon"])
def test_validate_name_rejects_invalid(bad):
    with pytest.raises(AccountError):
        validate_name(bad)


def test_offline_uuid_matches_java_offline_player_algorithm():
    # Ground truth produced by java.util.UUID.nameUUIDFromBytes(
    #   "OfflinePlayer:Notch".getBytes(UTF_8)) on OpenJDK 21.
    assert str(offline_uuid("Notch")) == "b50ad385-829d-3141-a216-7e7d7539ba7f"
    assert str(offline_uuid("ApeironTest")) == "e853120a-59b2-3511-a31f-f8a577795c12"
    assert str(offline_uuid("Steve")) == "5627dd98-e6be-3c21-b8a8-e92344183641"


def test_offline_uuid_is_deterministic():
    assert offline_uuid("Same") == offline_uuid("Same")
    assert offline_uuid("Same") != offline_uuid("Different")


def test_create_and_rename_profile():
    profile = create_offline_profile("Alpha")
    assert profile.name == "Alpha"
    assert profile.uuid_hex == profile.uuid.replace("-", "")
    renamed = rename_profile(profile, "Beta")
    assert renamed.name == "Beta"
    assert renamed.uuid != profile.uuid  # identity follows the vanilla algorithm


def test_session_repr_never_leaks_token():
    session = Session(player_name="A", uuid="u", access_token="SECRET-TOKEN", verified=True)
    assert "SECRET-TOKEN" not in repr(session)
    assert "SECRET-TOKEN" not in str(session.redacted())


@pytest.fixture()
def service(repos, tmp_path: Path, fake_http):
    store = FileCredentialStore(tmp_path / "credentials")
    return AccountService(repos["accounts"], store, fake_http)


def test_add_and_list_offline_accounts(service: AccountService):
    account = service.add_offline("LocalGuy")
    assert account.kind == "offline"
    assert account.verified is False
    assert [a.name for a in service.list()] == ["LocalGuy"]

    with pytest.raises(AccountError):
        service.add_offline("LocalGuy")

    with pytest.raises(AccountError):
        service.add_offline("localguy")  # case-insensitive clash


def test_offline_session_is_marked_unverified(service: AccountService):
    account = service.add_offline("Player1")
    session = service.session_for(account.id)
    assert session.user_type == "legacy"
    assert session.verified is False
    assert session.access_token == "0"
    assert session.uuid == account.uuid.replace("-", "") or session.uuid == account.uuid


def test_rename_offline_account(service: AccountService):
    account = service.add_offline("OldName")
    renamed = service.rename_offline(account.id, "NewName")
    assert renamed.name == "NewName"
    assert renamed.uuid != account.uuid


def test_rename_microsoft_account_is_rejected(service: AccountService, repos):
    microsoft = repos["accounts"].add(
        Account(kind="microsoft", name="MS", uuid="ms-uuid", verified=True)
    )
    with pytest.raises(AccountError):
        service.rename_offline(microsoft.id, "Hacked")


def test_remove_account_clears_credentials(service: AccountService, tmp_path: Path):
    account = service.add_offline("ToRemove")
    service.store.set(f"account:{account.id}", {"access_token": "x"})
    service.remove(account.id)
    assert service.store.get(f"account:{account.id}") is None
    assert service.list() == []


def test_default_account_prefers_verified(service: AccountService, repos):
    service.add_offline("OfflineOne")
    repos["accounts"].add(Account(kind="microsoft", name="Verified", uuid="v-uuid", verified=True))
    assert service.default_account().name == "Verified"


def test_microsoft_requires_client_id(fake_http):
    auth = MicrosoftAuth(fake_http, client_id="")
    with pytest.raises(AuthError):
        auth.begin()


def test_microsoft_rejects_malformed_client_id(fake_http):
    auth = MicrosoftAuth(fake_http, client_id="not-a-guid")
    with pytest.raises(AuthError):
        auth.begin()


def test_client_id_from_environment(monkeypatch):
    good = "12345678-1234-1234-1234-123456789abc"
    monkeypatch.setenv("APEIRON_MS_CLIENT_ID", good)
    assert resolve_client_id("") == good


def test_device_code_public_payload_hides_device_code():
    info = DeviceCodeInfo(
        device_code="SECRET",
        user_code="ABCD",
        verification_uri="https://x",
        expires_in=900,
        interval=5,
    )
    payload = info.public()
    assert "SECRET" not in str(payload)
    assert payload["userCode"] == "ABCD"


def test_credentials_are_not_stored_in_database(service: AccountService, repos):
    service.add_offline("PlainPlayer")
    row = repos["accounts"].list()[0]
    for value in vars(row).values():
        assert "token" not in str(value).lower()
