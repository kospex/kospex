# 20260507 — Bitbucket API token support for `kgit bitbucket`

Tracks: <https://github.com/kospex/kospex/issues/90>

## Overview

Atlassian is rolling out **API tokens** as the replacement for Bitbucket Cloud
**app passwords** ([docs][1]). The current `kgit bitbucket` command and the
underlying `KospexBitbucket` client only know about app passwords:

- `src/kgit.py:341` — the `bitbucket` subcommand reads `BITBUCKET_USERNAME` /
  `BITBUCKET_APP_PASSWORD` from the env and prints those names in help / error
  text.
- `src/kospex_bitbucket.py:9` — `KospexBitbucket` has `username` /
  `app_password` fields, hard-codes `auth = (self.username, self.app_password)`
  in `get_repos` and `test_auth`, and pulls `BITBUCKET_USERNAME` /
  `BITBUCKET_APP_PASSWORD` in `get_env_credentials`.

This spec plans how to add API-token authentication while keeping a clean
deprecation path for app passwords.

[1]: https://support.atlassian.com/bitbucket-cloud/docs/using-api-tokens/

## What changed upstream

From Atlassian's API tokens doc:

- **Auth scheme is still HTTP Basic** — only the credential pair changes.
- Valid pairs:
  - `{atlassian_account_email}:{api_token}` (email-based, the form Atlassian
    shows in their `curl` example)
  - `{bitbucket_username}:{api_token}` (works for both REST and `git clone`)
  - `x-bitbucket-api-token-auth:{api_token}` (static username, useful in CI
    where you don't want to encode an email or username)
- Tokens are **scoped at creation** — every API call requires a token whose
  scopes cover the operation. App passwords had a similar permission model but
  with looser defaults; tokens fail closed.
- **App password sunset (per Atlassian's Bitbucket UI, observed 2026-05-07):**
  - **2025-09-09** — app password creation disabled (already passed).
  - **2026-06-09** — all existing app passwords disabled.
  Migrations must complete before 2026-06-09.

### Two token types live at Atlassian

Empirically verified 2026-05-07 with the user's account:

1. **Atlassian account API token** — created at `id.atlassian.com → Security
   → API tokens`. **No scopes**, account-wide across Atlassian products. The
   user verified that this type returns `200` against
   `https://api.bitbucket.org/2.0/user` with `email:token` Basic auth.
2. **Bitbucket-scoped API token** — created inside Bitbucket (personal /
   workspace settings → API tokens). **Scopes required at creation.** This
   is the documented app-password replacement. Bitbucket's own
   token-creation UI states the wire-level convention:
   - Bitbucket REST APIs → authenticate as **`{email}:{api_token}`**
   - Bitbucket git commands → authenticate as **`{username}:{api_token}`**
   `kgit bitbucket` only hits the REST API, so for the scoped path we
   should default to `BITBUCKET_EMAIL`. The `BITBUCKET_USERNAME` slot is
   still supported (Atlassian docs say it works for REST), but the help
   text should lead with email for the scoped path to match Bitbucket's
   own guidance, and document username as the form needed if/when we
   later add token-based git operations.

Both token types authenticate identically over the wire; our code does
not need to distinguish them. Help text and error messages should explain
both options so users aren't pushed into ticking scopes when an account
token already covers their use.

### Scopes required for `get_repos` (scoped tokens)

For `kgit bitbucket -workspace <ws>`, the scoped-token path needs:

- `read:project:bitbucket`
- `read:repository:bitbucket`
- `read:workspace:bitbucket`

Sourced from the user's working scoped token, 2026-05-07. These three
strings are the literal values to quote in `--help` and the 403 error
message.

### Empirical findings from a real scoped token (2026-05-07)

Verified against the user's working scoped token after first implementation:

1. **`/2.0/user` is the wrong endpoint for `test_auth`.** It requires an
   account-level scope (e.g. `read:account` / `read:me:bitbucket`) that
   `get_repos` does not. Hitting `/2.0/user` from `-test-auth` with a
   correctly-scoped token gave a misleading 403 ("missing scopes") even
   though `get_repos` would succeed.
2. **`/2.0/workspaces` returns HTTP 410** for the user's token — the
   listing endpoint appears to be retired for token-based auth. So we
   can't use it as a generic auth probe either.
3. **Resolution:** `test_auth` now hits
   `/2.0/repositories/{workspace}?pagelen=1` — a one-page mirror of the
   exact call `get_repos` makes. This is the only way to verify "auth +
   scopes are sufficient for `get_repos`" with full fidelity. The
   tradeoff: `-test-auth` now requires `-workspace`. Acceptable, since
   the whole purpose of the command is to query a workspace.
   **Verified 2026-05-07:** `kgit bitbucket -test-auth -workspace WS`
   returns 200 with `BITBUCKET_API_TOKEN` + `BITBUCKET_EMAIL` and the
   three documented scopes.
4. **The static `x-bitbucket-api-token-auth` username does not work
   for REST.** Atlassian's docs imply it's a generic fallback; in
   practice `BITBUCKET_API_TOKEN` alone (no email, no username) gave a
   401 against every endpoint we tried. The fallback is kept in
   `_auth_tuple()` for completeness but the CLI help no longer
   advertises it as a primary recipe — users must set `BITBUCKET_EMAIL`
   or `BITBUCKET_USERNAME`.

Practical consequence: the API surface (`requests.get(url, auth=(u, p))`)
doesn't change. What changes is **which env vars we read**, **what we tell the
user when auth fails**, and **what scopes we suggest** for the scoped variant.

## Goals

1. Let users authenticate `kgit bitbucket` with a Bitbucket API token.
2. Keep `BITBUCKET_USERNAME` + `BITBUCKET_APP_PASSWORD` working (with a
   deprecation note) so existing users aren't broken.
3. Improve auth-failure diagnostics — today we say "check your USERNAME and
   APP_PASSWORD"; with tokens, the most common failure is **missing scopes**,
   which is a different fix.
4. Keep the `KospexBitbucket` client small and avoid forking it for two
   credential types.

## Non-goals

- No change to `kgit clone` URL handling for Bitbucket. Users embedding
  credentials in a clone URL is out of scope here (and Atlassian's guidance
  there is just "use the token where you'd have used the app password").
- No change to on-prem / Bitbucket Data Center handling
  (`KospexGit.parse_bitbucket_onpremise_url`) — those use different auth.
- No GUI / interactive prompt. Env-var only, like today.

## Proposed design

### New env vars

Read in this priority order in `KospexBitbucket.get_env_credentials`:

1. **`BITBUCKET_API_TOKEN`** (new) plus **at most one** of:
   - `BITBUCKET_EMAIL` — Atlassian account email, **or**
   - `BITBUCKET_USERNAME` — existing var, reused as the basic-auth username
   - If neither is set, fall back to the static `x-bitbucket-api-token-auth`
     username (matches Atlassian's "no email/username" recipe).
   - **Email and username are mutually exclusive.** If `BITBUCKET_API_TOKEN`
     is set together with *both* `BITBUCKET_EMAIL` and `BITBUCKET_USERNAME`,
     `get_env_credentials` returns a configuration-error mode and the CLI
     exits non-zero with a message like *"BITBUCKET_EMAIL and
     BITBUCKET_USERNAME are mutually exclusive when using
     BITBUCKET_API_TOKEN — set one or the other, not both."* This avoids
     silent picks and ambiguity over which one wins. (Note: the legacy
     branch still requires `BITBUCKET_USERNAME` — see #2 below — but only
     applies when `BITBUCKET_API_TOKEN` is absent.)
2. **`BITBUCKET_APP_PASSWORD`** + `BITBUCKET_USERNAME` (legacy path) — used
   only if no `BITBUCKET_API_TOKEN` is present. Emit a one-line deprecation
   warning to stderr (something like *"App passwords still work but Atlassian
   recommends API tokens — see kgit bitbucket --help"*).

This keeps the "set two env vars" UX, makes API tokens the default when both
are present, and lets users with only an API token skip the username entirely.

### Client shape

In `src/kospex_bitbucket.py`:

- Add `self.api_token` and `self.email` fields next to the existing
  `username` / `app_password`.
- Add `set_api_token(token)` and `set_email(email)` setters for parity with
  the existing setters.
- Replace the two `auth = (self.username, self.app_password)` sites with a
  single `self._auth_tuple()` helper that returns:
  - `(email or username or "x-bitbucket-api-token-auth", api_token)` if
    `api_token` is set, else
  - `(username, app_password)` for the legacy path.
- `get_env_credentials` returns a small dict / enum identifying which mode
  was selected (or a `CONFIG_ERROR` mode with a reason string for the
  email-vs-username collision), so the CLI can print accurate "Found X
  credentials in the environment" text, the deprecation warning when
  applicable, and a precise error when the env vars conflict.

This is a single surface change — `requests.get(url, auth=…)` stays untouched.

### CLI changes (`kgit bitbucket`)

In `src/kgit.py:349`:

- Update the docstring: list the new env vars first, mention legacy
  `BITBUCKET_APP_PASSWORD` works but is deprecated, link to **both**
  Atlassian docs pages (the user-facing token-management page Bitbucket's
  own UI references is
  <https://support.atlassian.com/bitbucket-cloud/docs/api-tokens>; the
  developer-focused auth-recipes page used to scaffold this spec is
  <https://support.atlassian.com/bitbucket-cloud/docs/using-api-tokens/>),
  and explain that **either** of these works:
  - an Atlassian account API token (`id.atlassian.com` → Security → API
    tokens) — no scopes required, and
  - a Bitbucket-scoped API token (Bitbucket settings → API tokens) — must
    include all three of `read:project:bitbucket`,
    `read:repository:bitbucket`, and `read:workspace:bitbucket`.
- For the scoped path, the docstring should lead with the email
  recipe (`BITBUCKET_EMAIL` + `BITBUCKET_API_TOKEN`) since Bitbucket's
  own UI calls that out as the API form. Note that `BITBUCKET_USERNAME`
  also works for REST and is the form needed for git commands.
- The "Found bitbucket credentials" / "Could not find" branch becomes:
  - "Found Bitbucket API token credentials" (token mode)
  - "Found Bitbucket app password credentials (deprecated — see --help)"
    (legacy mode)
  - "Could not find Bitbucket credentials. Set BITBUCKET_API_TOKEN (and
    optionally BITBUCKET_EMAIL or BITBUCKET_USERNAME), or the legacy
    BITBUCKET_USERNAME + BITBUCKET_APP_PASSWORD." (none mode)
- `-test-auth` failure path: distinguish 401 (bad credentials) from 403
  (auth ok, scope missing) and on 403 print the **literal scope strings**
  the token needs:
  - `read:project:bitbucket`
  - `read:repository:bitbucket`
  - `read:workspace:bitbucket`

  Same list as in `--help`. The 403 hint should also tell the user they
  can switch to an unscoped Atlassian account API token if they don't
  want to manage Bitbucket-scoped tokens. Today both error classes
  collapse to the same `test_auth() -> bool`; the new shape should
  return enough info to tell them apart, e.g. the status code or a
  typed exception, so the CLI can branch on it.

### Tests

Add `tests/test_kospex_bitbucket.py` covering:

- `get_env_credentials` mode selection (token-only, token+email,
  token+username, token+email+username → config error, legacy, none)
  using `monkeypatch.setenv` / `monkeypatch.delenv`.
- `_auth_tuple()` returns the expected pair for each mode.
- `test_auth` 200 → True, 401 → False with "bad credentials" reason, 403 →
  False with "missing scopes" reason. Mock `requests.get` with
  `responses` or `unittest.mock`.

No live Bitbucket calls in the test suite.

### Docs

- `CLAUDE.md` — add `BITBUCKET_API_TOKEN` / `BITBUCKET_EMAIL` to the
  *Environment Variables* section, mark `BITBUCKET_APP_PASSWORD` as legacy.
- Add the change to the 0.0.37 changelog (per memory: 0.0.37 is still
  unreleased and accumulating fixes — see open question 5).

## Files to change

| File | Change |
|---|---|
| `src/kospex_bitbucket.py` | Add token fields/setters, `_auth_tuple()`, new env-var reading, return mode from `get_env_credentials`, richer return from `test_auth` |
| `src/kgit.py` (`bitbucket` cmd) | Updated docstring, mode-aware status messages, deprecation warning, scope-aware test-auth output |
| `tests/test_kospex_bitbucket.py` | New file (see Tests above) |
| `CLAUDE.md` | Document new env vars |
| `changes/202604-release-0.0.37-plan.md` *(or whatever the 0.0.37 plan file is)* | Add a one-line entry |

## Risks / things to watch

- **Atlassian's exact scope names.** The doc says "tokens must have scopes"
  but doesn't enumerate them on the page we fetched. We should verify the
  scope strings against a real token-creation flow before hardcoding them
  into the help text — wrong scope names in error messages would actively
  mislead users.
- **CI users.** If anyone wires `kgit bitbucket` into CI today with
  `BITBUCKET_USERNAME` / `BITBUCKET_APP_PASSWORD`, they keep working. The
  deprecation message goes to stderr so it shouldn't break stdout-parsing
  pipelines, but worth a sanity check.
- **Atlassian sunset is published.** The Bitbucket UI states all existing
  app passwords are disabled on **2026-06-09** (~33 days from this spec).
  Cleanup is no longer "review in a year" — see *Cleanup reminder* below.

## Cleanup reminder

Atlassian disables all existing app passwords on **2026-06-09**. After that
date the legacy code path is dead — every request that takes it will 401.

Concrete plan:

- **Now (this PR):** keep the legacy path, with a stderr deprecation
  warning that names the 2026-06-09 cutoff so users see the deadline.
- **On or shortly after 2026-06-10:** remove the legacy code outright.
  Tasks:
  - Drop the `app_password` field, setter, and legacy branch in
    `_auth_tuple()` from `src/kospex_bitbucket.py`.
  - Drop the `MODE_LEGACY` branch and deprecation warning in the
    `bitbucket` cmd in `src/kgit.py`. Update the docstring to remove
    the legacy section.
  - Drop legacy-mode tests from `tests/test_kospex_bitbucket.py`.
  - Strip `BITBUCKET_APP_PASSWORD` from `CLAUDE.md`.
  - Add a brief CHANGELOG entry under whichever release contains the
    cleanup.
- Track as `changes/202606-bitbucket-app-password-removal.md` when the
  cleanup fires.

## Open questions

1. ~~**Backwards compat horizon**~~ — **Resolved 2026-05-07** (revised
   later same day after spotting Atlassian's published sunset): keep the
   legacy path with a stderr deprecation note that names the
   **2026-06-09** Atlassian cutoff. Remove the legacy code on or shortly
   after that date. See *Cleanup reminder* above.
2. ~~**Email vs username preference**~~ — **Resolved 2026-05-07**:
   email and username are mutually exclusive when using
   `BITBUCKET_API_TOKEN`. If both are set, the CLI errors out (see
   *New env vars* above). Atlassian basic auth only ever takes one
   username field, so accepting both would force us to silently pick
   one — better to fail loudly.
3. ~~**Static username fallback**~~ — **Resolved 2026-05-07**: yes,
   fall back to the literal `x-bitbucket-api-token-auth` when only
   `BITBUCKET_API_TOKEN` is set, so users can run with one env var.
4. ~~**Scope guidance in help text**~~ — **Resolved 2026-05-07**:
   user created a scoped token with `read:project:bitbucket`,
   `read:repository:bitbucket`, and `read:workspace:bitbucket`. These
   three are the literal strings to quote in `--help` and the 403
   error. (Empirical confirmation that `get_repos` succeeds against a
   workspace with exactly these three scopes is recommended at
   implementation time but not gating — all three are clearly relevant
   to listing workspace repos with project info.) Bitbucket's own
   token-creation UI also documents the wire-level convention (email
   for REST APIs, username for git), captured under
   *Two token types live at Atlassian*.
5. ~~**Release scope**~~ — **Resolved 2026-05-07**: land in 0.0.37
   (still unreleased). Changelog entry added.
6. ~~**Scope of this change**~~ — **Resolved 2026-05-07**: this PR
   covers `kgit bitbucket` and the `KospexBitbucket` client tweaks it
   needs. `repo_sync.py` is not currently in use, so it's out of scope.
   If/when it's reactivated it will inherit the new client behaviour
   automatically.
7. ~~**Error UX on 403**~~ — **Resolved 2026-05-07**: print the
   literal scope strings the token needs (both in the 403 error
   message and in `kgit bitbucket --help`). Blocked on Q4 supplying
   the real scope names.
