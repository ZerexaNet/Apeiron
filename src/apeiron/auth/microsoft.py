"""Microsoft account authentication using the official OAuth 2.0 device flow.

Flow (all endpoints are Microsoft/Xbox/Mojang official):

1. ``/devicecode``            → user code shown in the UI, no password ever typed into Apeiron
2. ``/token`` (poll)          → Microsoft access + refresh token
3. ``user.auth.xboxlive.com`` → Xbox Live token (RPS)
4. ``xsts.auth.xboxlive.com`` → XSTS token bound to ``rp://api.minecraftservices.com/``
5. ``login_with_xbox``        → Minecraft access token
6. ``/minecraft/profile``     → authoritative ownership check + skin/name

Apeiron never sees the user's Microsoft password and never scrapes the login
page.  A **client id registered for this application in Azure AD (public
client, device code flow enabled)** is required; none is shipped in the source
tree.  Set ``APEIRON_MS_CLIENT_ID`` or the value in Settings.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from apeiron.auth.base import Session
from apeiron.core.errors import (
    AuthError,
    AuthExpiredError,
    NetworkError,
    OwnershipError,
)
from apeiron.core.http import HttpClient
from apeiron.core.logging import get_logger

log = get_logger("microsoft-auth")

DEVICE_CODE_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/devicecode"
TOKEN_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
XBL_URL = "https://user.auth.xboxlive.com/user/authenticate"
XSTS_URL = "https://xsts.auth.xboxlive.com/xsts/authorize"
MC_LOGIN_URL = "https://api.minecraftservices.com/authentication/login_with_xbox"
MC_PROFILE_URL = "https://api.minecraftservices.com/minecraft/profile"
MC_ENTITLEMENTS_URL = "https://api.minecraftservices.com/entitlements/mcstore"

SCOPE = "XboxLive.signin offline_access"
RELYING_PARTY = "http://auth.xboxlive.com"
MC_RELYING_PARTY = "rp://api.minecraftservices.com/"

XERR_MESSAGES = {
    2148916233: ("This Microsoft account has no Xbox profile. Create one at xbox.com, then retry."),
    2148916235: "Xbox Live is not available in the country associated with this account.",
    2148916236: "This account needs age verification on xbox.com before signing in.",
    2148916237: "This account needs age verification on xbox.com before it can sign in.",
    2148916238: "This is a child account and must be added to a Microsoft family first.",
}

CLIENT_ID_ENV = "APEIRON_MS_CLIENT_ID"
GUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


@dataclass
class DeviceCodeInfo:
    device_code: str
    user_code: str
    verification_uri: str
    expires_in: int
    interval: int
    message: str = ""

    def public(self) -> dict[str, Any]:
        """Never expose the device_code to QML; the user_code is meant to be shown."""
        return {
            "userCode": self.user_code,
            "verificationUri": self.verification_uri,
            "expiresIn": self.expires_in,
            "interval": self.interval,
            "message": self.message,
        }


def resolve_client_id(explicit: str = "") -> str:
    candidate = (explicit or os.environ.get(CLIENT_ID_ENV, "")).strip()
    if not candidate:
        raise AuthError(
            "Microsoft sign-in needs an Azure AD application client id",
            hint=(
                "Register a public client application with the device code flow enabled, "
                "then set APEIRON_MS_CLIENT_ID or add the id in Settings. "
                "Apeiron does not ship a client id because the application must be "
                "owned by its publisher."
            ),
        )
    if not GUID_RE.match(candidate):
        raise AuthError("the configured Microsoft client id is not a valid application (client) id")
    return candidate


class MicrosoftAuth:
    """Device-code implementation of the official Minecraft authentication chain."""

    id = "microsoft"
    display_name = "Microsoft"
    requires_network = True

    def __init__(self, http: HttpClient, client_id: str = "") -> None:
        self.http = http
        self.client_id = client_id

    # -- step 1: device code ------------------------------------------------
    def begin(self) -> DeviceCodeInfo:
        client_id = resolve_client_id(self.client_id)
        payload = self._form_post(
            DEVICE_CODE_URL,
            {"client_id": client_id, "scope": SCOPE},
        )
        if "device_code" not in payload:
            raise AuthError("Microsoft did not return a device code", detail=str(payload)[:300])
        info = DeviceCodeInfo(
            device_code=str(payload["device_code"]),
            user_code=str(payload.get("user_code", "")),
            verification_uri=str(payload.get("verification_uri", "https://microsoft.com/link")),
            expires_in=int(payload.get("expires_in", 900)),
            interval=max(1, int(payload.get("interval", 5))),
            message=str(payload.get("message", "")),
        )
        log.info("device code issued, expires in %ds", info.expires_in)
        return info

    # -- step 2: poll for tokens -------------------------------------------
    def wait_for_token(
        self,
        info: DeviceCodeInfo,
        *,
        cancel: threading.Event | None = None,
        on_status: Callable[[str], None] | None = None,
    ) -> Session:
        client_id = resolve_client_id(self.client_id)
        deadline = time.monotonic() + info.expires_in
        interval = float(info.interval)
        while time.monotonic() < deadline:
            if cancel is not None and cancel.is_set():
                raise AuthError("sign-in cancelled")
            payload = self._form_post(
                TOKEN_URL,
                {
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "client_id": client_id,
                    "device_code": info.device_code,
                },
                allow_error_body=True,
            )
            error = payload.get("error")
            if not error:
                return self._session_from_microsoft(payload)
            if error == "authorization_pending":
                if on_status:
                    on_status("waiting")
                if _sleep(interval, cancel):
                    raise AuthError("sign-in cancelled")
                continue
            if error == "slow_down":
                interval += 5.0
                if _sleep(interval, cancel):
                    raise AuthError("sign-in cancelled")
                continue
            if error == "expired_token":
                raise AuthExpiredError("the sign-in code expired; start again")
            if error in ("authorization_declined", "access_denied"):
                raise AuthError("sign-in was declined")
            raise AuthError(
                f"Microsoft sign-in failed: {error}",
                detail=str(payload.get("error_description", ""))[:300],
            )
        raise AuthExpiredError("the sign-in code expired; start again")

    # -- refresh ------------------------------------------------------------
    def refresh(self, session: Session) -> Session:
        if not session.refresh_token:
            raise AuthExpiredError("the session cannot be refreshed; sign in again")
        client_id = resolve_client_id(self.client_id)
        payload = self._form_post(
            TOKEN_URL,
            {
                "grant_type": "refresh_token",
                "client_id": client_id,
                "refresh_token": session.refresh_token,
                "scope": SCOPE,
            },
            allow_error_body=True,
        )
        if payload.get("error"):
            raise AuthExpiredError(
                "the Microsoft session expired; sign in again",
                detail=str(payload.get("error_description", ""))[:300],
            )
        refreshed = self._session_from_microsoft(payload, previous=session)
        return refreshed

    def logout(self, session: Session) -> None:
        # Tokens are discarded by the caller; nothing else can be revoked
        # without an interactive Microsoft session.
        session.access_token = ""
        session.refresh_token = ""

    # -- internals ----------------------------------------------------------
    def _session_from_microsoft(
        self, payload: dict[str, Any], previous: Session | None = None
    ) -> Session:
        access_token = str(payload.get("access_token", ""))
        refresh_token = str(payload.get("refresh_token", "")) or (
            previous.refresh_token if previous else ""
        )
        if not access_token:
            raise AuthError("Microsoft returned no access token")
        expires_in = int(payload.get("expires_in", 3600))

        xbl = self._xbox_authenticate(access_token)
        xsts = self._xsts_authorize(xbl["token"])
        xuid = ""
        claims = xsts.get("claims") or {}
        xui = claims.get("xui") or []
        if xui:
            xuid = str(xui[0].get("xid", ""))
        uhs = str(xsts.get("uhs") or xbl.get("uhs") or "")
        if not uhs:
            raise AuthError("Xbox did not return a user hash")

        minecraft = self._minecraft_login(uhs, xsts["token"])
        mc_token = str(minecraft.get("access_token", ""))
        if not mc_token:
            raise AuthError("Minecraft did not return an access token")

        profile = self._fetch_profile(mc_token)
        if profile is None:
            raise OwnershipError(
                "this Microsoft account does not own Minecraft: Java Edition",
                hint=(
                    "Sign in with the account that purchased the game, "
                    "or use a local offline profile."
                ),
            )
        name = str(profile.get("name", ""))
        uuid = str(profile.get("id", "")).replace("-", "")
        if not name or not uuid:
            raise AuthError("Minecraft returned an incomplete profile")

        skin_model = "classic"
        for skin in profile.get("skins") or []:
            if skin.get("state") == "ACTIVE":
                skin_model = str(skin.get("variant", "classic")).lower()
                break

        log.info("signed in as %s (xuid present=%s)", name, bool(xuid))
        return Session(
            player_name=name,
            uuid=uuid,
            access_token=mc_token,
            user_type="msa",
            xuid=xuid,
            client_id=resolve_client_id(self.client_id),
            verified=True,
            expires_at=time.time() + expires_in,
            refresh_token=refresh_token,
            skin_model="slim" if skin_model == "slim" else "classic",
        )

    def _xbox_authenticate(self, microsoft_token: str) -> dict[str, Any]:
        body = {
            "Properties": {
                "AuthMethod": "RPS",
                "SiteName": "user.auth.xboxlive.com",
                "RpsTicket": f"d={microsoft_token}",
            },
            "RelyingParty": RELYING_PARTY,
            "TokenType": "JWT",
        }
        payload = self._json_post(XBL_URL, body)
        token = payload.get("Token")
        if not token:
            raise AuthError("Xbox Live rejected the Microsoft token")
        xui = ((payload.get("DisplayClaims") or {}).get("xui") or [{}])[0]
        return {"token": str(token), "uhs": str(xui.get("uhs", ""))}

    def _xsts_authorize(self, xbl_token: str) -> dict[str, Any]:
        body = {
            "Properties": {"SandboxId": "RETAIL", "UserTokens": [xbl_token]},
            "RelyingParty": MC_RELYING_PARTY,
            "TokenType": "JWT",
        }
        try:
            payload = self._json_post(XSTS_URL, body)
        except AuthError as exc:
            xerr = _extract_xerr(exc.detail or "")
            if xerr is not None:
                raise AuthError(
                    XERR_MESSAGES.get(xerr, f"Xbox authorisation failed (XErr {xerr})")
                ) from exc
            raise
        token = payload.get("Token")
        if not token:
            xerr = _extract_xerr(json.dumps(payload))
            if xerr is not None:
                raise AuthError(XERR_MESSAGES.get(xerr, f"Xbox authorisation failed (XErr {xerr})"))
            raise AuthError("Xbox did not return an XSTS token")
        claims = payload.get("DisplayClaims") or {}
        xui = (claims.get("xui") or [{}])[0]
        return {"token": str(token), "uhs": str(xui.get("uhs", "")), "claims": claims}

    def _minecraft_login(self, uhs: str, xsts_token: str) -> dict[str, Any]:
        body = {"identityToken": f"XBL3.0 x={uhs};{xsts_token}"}
        try:
            return self._json_post(MC_LOGIN_URL, body)
        except AuthError as exc:
            raise AuthError(
                "Minecraft rejected the Xbox identity",
                detail=exc.detail,
            ) from exc

    def _fetch_profile(self, minecraft_token: str) -> dict[str, Any] | None:
        try:
            payload = self.http.get_json(
                MC_PROFILE_URL, headers={"Authorization": f"Bearer {minecraft_token}"}
            )
        except NetworkError as exc:
            if "HTTP 404" in exc.message:
                return None
            raise
        return payload if isinstance(payload, dict) else None

    def entitlements(self, minecraft_token: str) -> list[str]:
        """Return owned entitlement names (diagnostics; profile is authoritative)."""
        try:
            payload = self.http.get_json(
                MC_ENTITLEMENTS_URL, headers={"Authorization": f"Bearer {minecraft_token}"}
            )
        except NetworkError:
            return []
        return [str(item.get("name", "")) for item in payload.get("items") or []]

    # -- HTTP helpers -------------------------------------------------------
    def _form_post(
        self, url: str, data: dict[str, str], *, allow_error_body: bool = False
    ) -> dict[str, Any]:
        body = urllib.parse.urlencode(data).encode("utf-8")
        try:
            response = self.http.request(
                url,
                method="POST",
                data=body,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                accepted_statuses=frozenset({400}) if allow_error_body else frozenset(),
            )
        except NetworkError:
            raise
        return response.json()

    def _json_post(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        try:
            response = self.http.request(
                url,
                method="POST",
                data=body,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
        except NetworkError as exc:
            raise AuthError(f"{url} rejected the request", detail=exc.message) from exc
        result = response.json()
        if not isinstance(result, dict):
            raise AuthError(f"unexpected response from {url}")
        return result


def _extract_xerr(text: str) -> int | None:
    match = re.search(r'"XErr"\s*:\s*(\d+)', text) or re.search(r"XErr\D{0,4}(\d+)", text)
    return int(match.group(1)) if match else None


def _sleep(seconds: float, cancel: threading.Event | None) -> bool:
    """Sleep, returning True if cancelled."""
    if cancel is None:
        time.sleep(seconds)
        return False
    return cancel.wait(seconds)
