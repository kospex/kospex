# GitHub token environment variables — consistency

Design doc. No code yet.

kospex, its docs and the `gh` CLI disagree about which environment variable holds
the GitHub token. One of the documented names is never read at all.

## Current state

| where | says |
|---|---|
| `src/kospex_github.py` `get_env_credentials()` (the **only** read site) | `GITHUB_AUTH_TOKEN`, then `GH_TOKEN` |
| `kgit github` help + `-test-auth` messages (`src/kgit.py`) | `GITHUB_AUTH_TOKEN` (help also mentions `GH_TOKEN`) |
| `docs/kgit.md` | `GITHUB_AUTH_TOKEN` |
| `docs/troubleshooting.md` "GitHub Rate Limits" | `export GITHUB_TOKEN=...`, **never read** |
| `CLAUDE.md` (env vars, troubleshooting) | `GITHUB_TOKEN`, **never read** |
| `gh` CLI (`gh help environment`) | `GH_TOKEN`, then `GITHUB_TOKEN`; overrides `gh auth login` credentials |

So a user who follows `docs/troubleshooting.md` gets unauthenticated requests
(60/hour, public repos only) and no warning.

### Bug: an empty variable blocks the next one

```python
access_token = os.getenv(self.ENV_GITHUB_AUTH_TOKEN)
if access_token is None:
    access_token = os.getenv(self.ENV_GH_TOKEN)
```

`GITHUB_AUTH_TOKEN=""` (left by `export GITHUB_AUTH_TOKEN=` or an empty `.env`
line) is not `None`, so `GH_TOKEN` is never consulted, and the run is
unauthenticated even though a valid `GH_TOKEN` is set. Surrounding whitespace is
not stripped either, so a value pasted with a trailing space or a `\r` (from a
Windows-edited `.env`) is sent as-is and fails with 401.

## Proposal

Read three names. Keep the existing two in their current order, and add
`GITHUB_TOKEN` last:

1. `GITHUB_AUTH_TOKEN`: kospex-specific, so explicit intent wins
2. `GH_TOKEN`: gh's first choice
3. `GITHUB_TOKEN`: gh's second choice, and what GitHub Actions provides

This order is chosen so that **every combination that works today resolves to
the same token after the change**. Matching gh's order exactly (`GH_TOKEN`
first) would silently switch tokens for anyone who has `GITHUB_AUTH_TOKEN` and
`GH_TOKEN` set to different values, for example a kospex-specific org-scoped
token alongside their personal gh token.

Also:

- A value that is empty or whitespace-only counts as **unset**, so the next
  variable is tried. Surrounding whitespace is stripped. This fixes the bug above.
- `get_env_credentials()` records **which variable** supplied the token
  (`self.token_source`). `kgit github -test-auth` prints
  `Using token from GH_TOKEN`: the variable's name only, never the value or a
  prefix of it (CWE-532).
- The "not found" message names all three variables.
- One constant tuple, `TOKEN_ENV_VARS`, replaces the hard-coded names in
  `kgit.py` messages, so code and messages can't drift apart again.
- Docs: `docs/kgit.md`, `docs/troubleshooting.md`, `CLAUDE.md` all list the
  three names and the order.

Out of scope: git itself. `git clone https://...` doesn't read any of these
variables, because clone auth is the credential helper or SSH. That belongs to
#335 (`-ssh`, `-no-prompt`).

## Testing

The aim is to prove that **no combination that works today changes behaviour**,
and that the new ones work. There are three layers, and only the last needs a
real token.

### 1. Offline resolution matrix (pytest, always runs)

`tests/test_kospex_github_token_env.py`.

**Isolation first.** The developer running the suite very likely has `GH_TOKEN`
or `GITHUB_TOKEN` set in their own shell, because gh uses them. An autouse fixture
must `monkeypatch.delenv(..., raising=False)` all three before every test.
Otherwise results depend on whose machine runs them, and an "unset" case can
pass only because the real token is set.

Each variable gets a **distinct** dummy value (`tok-auth`, `tok-gh`, `tok-github`),
so the result shows which one won. Expected results are written as a **literal
table**, not computed by a precedence helper. A test that reimplements the
lookup proves nothing (see CLAUDE.md, "Two test shapes that prove nothing").

| `GITHUB_AUTH_TOKEN` | `GH_TOKEN` | `GITHUB_TOKEN` | expected | today |
|---|---|---|---|---|
| – | – | – | none | none |
| `tok-auth` | – | – | `tok-auth` | same |
| – | `tok-gh` | – | `tok-gh` | same |
| – | – | `tok-github` | `tok-github` | none (**new**) |
| `tok-auth` | `tok-gh` | – | `tok-auth` | same |
| `tok-auth` | – | `tok-github` | `tok-auth` | same |
| – | `tok-gh` | `tok-github` | `tok-gh` | same |
| `tok-auth` | `tok-gh` | `tok-github` | `tok-auth` | same |
| `""` | `tok-gh` | – | `tok-gh` | none (**bug fix**) |
| `"  "` | – | `tok-github` | `tok-github` | none (**bug fix**) |
| `""` | `""` | `""` | none | none |
| `" tok-auth\n"` | – | – | `tok-auth` | `" tok-auth\n"` (**fix**) |

For each row, assert all of these:
- `get_env_credentials()` return value, `access_token`, and the exact
  `Authorization` header, not just "a header exists"
- `token_source` names the expected variable (or `None`)
- no token → `headers` has no `Authorization` key, and `authenticated` is False

**Regression pin.** Rows marked "same" are the current behaviour. Add the test
file in a first commit **before** changing `get_env_credentials()`, with the
"new"/"fix" rows `xfail(strict=True)`. Then make the change and drop the
`xfail`s. The "same" rows passing on both commits is the proof that nothing
regressed. A strict `xfail` that starts passing early shows the test isn't
testing what it claims.

### 2. CLI messages (pytest, always runs)

`CliRunner` against `kgit github -test-auth`, with `KospexGithub.test_auth`
monkeypatched to return True/False (no network):

- token in each variable → output names that variable
- no token → exit 1, output names all three
- the dummy token value **never appears** in output, under any row

### 3. Live check with a real read-only token (opt-in, skipped by default)

This answers "would a read-only token set for each env var work?" Yes, and you
need **one** real token, not three. Pair it with a deliberately invalid one:

- Put the valid token in the variable that **should** win and `invalid-token` in
  the others → `test_auth()` must succeed (200).
- Swap, so the valid token is in a variable that **should lose** → must fail
  (401).

A pass/fail pair like that proves which variable was used against the real API.
A test that only checks "auth works with the token set" proves nothing when
all three variables hold the same token.

Token: **fine-grained PAT, "Public repositories (read-only)", no permissions,
7-day expiry.** `/user` and `/rate_limit` work with it, and it can read nothing
private, so a leak is low-impact.

Gate: `KOSPEX_TEST_GITHUB_TOKEN`. It's deliberately not one of the three
variables, so that it can't leak into the matrix it's testing. Skip with a clear
reason when it's unset. Each live case still `delenv`s all three before setting
its own.

```bash
KOSPEX_TEST_GITHUB_TOKEN=github_pat_... pytest tests/test_kospex_github_token_env.py -rs
```

Live rows (eight, each a pass/fail pair as above): each variable alone; each
pair, with the winner valid and then the loser valid; all three set.

Manual smoke test of the real CLI, with your shell's own tokens removed:

```bash
T=github_pat_...
for v in GITHUB_AUTH_TOKEN GH_TOKEN GITHUB_TOKEN; do
  env -u GITHUB_AUTH_TOKEN -u GH_TOKEN -u GITHUB_TOKEN "$v=$T" kgit github -test-auth
done
env -u GITHUB_AUTH_TOKEN -u GH_TOKEN -u GITHUB_TOKEN kgit github -test-auth   # expect exit 1
env -u GITHUB_AUTH_TOKEN GH_TOKEN=invalid GITHUB_TOKEN=$T kgit github -test-auth  # expect 401: GH_TOKEN wins
```

## Files

- `src/kospex_github.py`: `TOKEN_ENV_VARS`, empty/whitespace handling, `token_source`
- `src/kgit.py`: `-test-auth` and help text from `TOKEN_ENV_VARS`
- `tests/test_kospex_github_token_env.py`: new (there are no `KospexGithub` tests today)
- `docs/kgit.md`, `docs/troubleshooting.md`, `CLAUDE.md`
- `CHANGELOG.md`: note `GITHUB_TOKEN` is now read, and that empty values no longer block the next variable

## Decisions

- **Fallback to `gh auth token`: deferred, not in this change.** It's wanted
  later as an opt-in. The default would use a broad-scope token the user never
  handed to kospex.
- **GitHub Enterprise Server tokens** (`GH_ENTERPRISE_TOKEN`,
  `GITHUB_ENTERPRISE_TOKEN`): left out. `KospexGithub` hard-codes
  `api.github.com`, so they aren't relevant until GHES is supported.

## Open questions

1. **Run the live test in CI** with a repository secret? It would catch API-side
   changes, but adds a secret to manage and an external dependency to the run.
