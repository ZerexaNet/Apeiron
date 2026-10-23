# Disclaimer policy

## Which file is authoritative

| File | Status |
| --- | --- |
| [`DISCLAIMER.md`](../DISCLAIMER.md) | **Authoritative.** English. The legal statement of the project. |
| [`DISCLAIMER.zh-CN.md`](../DISCLAIMER.zh-CN.md) | Reference translation only. Explicitly labelled as such in its own header. |

The Chinese file exists so that Chinese-speaking users can *understand* the
statement. It does not replace, extend, narrow or override the English text, and
it carries no independent legal effect. Where the two appear to differ, the
English text governs.

## Rules for maintainers

1. Any change to the disclaimer is made in `DISCLAIMER.md` first.
2. `DISCLAIMER.zh-CN.md` is updated in the same commit to keep the meaning
   aligned; the translation keeps its reference-only notice.
3. Ambiguity about meaning is escalated to Zerexa Ltd (`admin@zerexa.net`) for
   review. Maintainers must not resolve ambiguity by declaring the Chinese text
   authoritative, and must not describe the disclaimer as a guarantee that the
   project is lawful.
4. The disclaimer is a statement of facts and intent. It is not a tool for
   avoiding third-party terms: it cannot legitimise unauthorised redistribution
   of game files, circumvention of licensing, or misuse of offline profiles.

## Where the statement appears in the product

- `README.md` — short non-official notice near the top, linking to
  `DISCLAIMER.md`.
- **About Apeiron** page in the application — the same core statement
  (independent project, no affiliation with Mojang Studios or Microsoft,
  trademark ownership) plus the publisher and contact address, from the
  `about` payload in `src/apeiron/ui/backend.py` (`DISCLAIMER_SHORT`).
- Accounts page — offline profiles are labelled as unverified local identities,
  and the Microsoft sign-in card states that no password is requested.

## Release checklist

Before publishing a release:

- [ ] `DISCLAIMER.md` unchanged or reviewed by Zerexa Ltd.
- [ ] `DISCLAIMER.zh-CN.md` still marked as a reference translation.
- [ ] About page still shows publisher and contact.
- [ ] No Minecraft artwork, logo or proprietary asset added to the repository.
- [ ] Dependency licences re-checked (`docs/THIRD_PARTY_LICENSES.md`).
- [ ] No bundled game files in the artifact.
