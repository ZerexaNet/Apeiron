# Account modes

Apeiron supports two account types and reserves an interface for future,
properly documented providers.

## 1. Microsoft account

**Flow** (all endpoints are Microsoft/Xbox/Mojang official):

1. `POST https://login.microsoftonline.com/consumers/oauth2/v2.0/devicecode`
   → `user_code` + `verification_uri` shown in the UI. **No password is ever
   entered into Apeiron**, so there is nothing to phish or store.
2. Poll `…/oauth2/v2.0/token` with
   `grant_type=urn:ietf:params:oauth:grant-type:device_code`, honouring
   `authorization_pending`, `slow_down` (interval +5 s), `expired_token` and
   `authorization_declined`.
3. `https://user.auth.xboxlive.com/user/authenticate` (RPS, `d=<ms token>`).
4. `https://xsts.auth.xboxlive.com/xsts/authorize` with relying party
   `rp://api.minecraftservices.com/`. Known `XErr` codes are mapped to
   actionable messages (no Xbox profile, unsupported country, child account).
5. `POST https://api.minecraftservices.com/authentication/login_with_xbox`
   with `XBL3.0 x=<uhs>;<xsts>`.
6. `GET https://api.minecraftservices.com/minecraft/profile` — **the ownership
   check**. A 404 means the account does not own Java Edition; the login is
   rejected with `OwnershipError` and the account is never shown as verified.

**Token handling.** The Minecraft access token, refresh token and expiry are
stored as one blob in the OS credential store:

| Platform | Backend | Secure flag |
| --- | --- | --- |
| Windows | DPAPI (`CryptProtectData`, current-user scope) | yes |
| macOS | login keychain via `/usr/bin/security` | yes |
| Linux | `secret-tool` (libsecret / KWallet bridge) | yes |
| any (fallback) | `credentials.json`, mode 0600 | **no** — surfaced in the UI |

Refresh happens before launch when the token is within five minutes of expiry.
A failed refresh deletes the stored blob and raises `AuthExpiredError` so the UI
asks for a new sign-in instead of failing inside the game.

**Client id.** Sign-in requires an Azure AD application registered as a public
client with the device-code flow enabled, supplied via `APEIRON_MS_CLIENT_ID` or
Settings. Apeiron ships none: publishing someone else's application id would be
both a security and a terms-of-service problem. Without one, `begin()` raises an
error that explains exactly what to register.

## 2. Offline (local) account

* Name rules follow the vanilla client: 3–16 characters, `[A-Za-z0-9_]`.
* The UUID is `UUID.nameUUIDFromBytes("OfflinePlayer:" + name)` — version 3,
  computed identically to the Java implementation (verified against OpenJDK;
  see `tests/test_accounts.py`). Single-player worlds and LAN games therefore
  behave as users expect.
* Renaming produces a new identity, because the UUID is derived from the name;
  the UI states this rather than silently migrating worlds.
* Offline sessions use `access_token="0"`, `user_type="legacy"` and
  `verified=False`. The UI labels them **“Offline profile · unverified local
  identity”** and the accounts page explains the limits.

**What an offline profile is not:** proof of purchase, a licence, or
authentication. It cannot join servers that require authenticated accounts, and
using it to impersonate an authenticated player or bypass access control is
prohibited by the disclaimer and by the servers' own rules.

## 3. Third-party providers (interface only)

`auth/base.py` defines the `AuthProvider` protocol (`begin`, `complete`,
`refresh`, `logout`) and a `Session` value object whose `__repr__` never prints
tokens. No third-party provider is implemented: doing so requires verified
protocol documentation and an authorisation decision from Zerexa Ltd, and
guessing at an unofficial protocol is out of scope by design.

## Adding a provider (for maintainers)

1. Obtain and review the provider's published protocol and terms.
2. Implement `AuthProvider` in `src/apeiron/auth/<name>.py`; never log tokens.
3. Store credentials through `auth/store.py`, keyed by `account:<id>`.
4. Set `verified=True` only when the provider itself confirms entitlement.
5. Add tests covering expiry, refusal, cancellation and the “no ownership” path.
