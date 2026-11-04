# GitHub token environment variables — consistency

## Status

**Built** in two PRs: the manual runner (#345, merged first, with a baseline
recorded against the old code), then the lookup change.

One deviation from the plan below: the offline tests were not committed first
as `xfail`. Every row asserts `token_source`, which doesn't exist before the
change, so on the old code the unchanged rows would have failed too and
proved nothing. The pre-change behaviour is pinned by the runner's
`--baseline` run against the real CLI and API instead.

Also fixed in the same change: the misleading `Found Github GITHUB_AUTH_TOKEN`
message printed whichever variable held the token, and request errors now
have the token redacted (CWE-532).

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

### Bug: a 401 is reported as a generic failure

`test_auth()` has a branch for 401 that prints `Invalid token`, but it is
unreachable: `elif response and response.status_code == 401`. A `requests.Response`
is falsy for any 4xx/5xx, so a rejected token prints `No data returned or request
failed`, the same as a timeout. This was found by running the manual runner (layer
4) against the real API. Fix: test `response is not None`. Until then the runner
recognises a 401 from the `401 Client Error` line `_make_request` prints.

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

### 4. Manual runner: the whole matrix against a real token (not CI)

**Decision: no live token test in CI.** Instead,
`tests/manual/github_token_matrix.py` runs every configuration against the real
API on demand. The name deliberately doesn't match `test_*.py`, so plain `pytest`
never collects it and CI never runs it.

```bash
python tests/manual/github_token_matrix.py                  # prompts for the token
python tests/manual/github_token_matrix.py --token-file ~/.kospex-test-token
python tests/manual/github_token_matrix.py --list           # print the rows, no network
```

**Getting the token.** It's read with `getpass` (prompt, not echoed), or from
`--token-file`, or from `KOSPEX_TEST_GITHUB_TOKEN`. It's never taken as a
command-line argument, because argv shows up in `ps` and shell history (CWE-214).
It's passed to child processes **only through their environment**.

**Each row** runs the real CLI, `kgit github -test-auth`, as a subprocess with a
constructed environment:

- start from `os.environ`, **remove all three** token variables, then set only
  that row's values
- `PYTHONPATH=<this checkout>/src`, so a worktree tests its own code rather than
  the main checkout's editable install (see CLAUDE.md, worktree testing)
- `GH_CONFIG_DIR` points at an empty temp dir, so a future `gh auth token`
  fallback can't quietly pick up your real `gh` login

The valid token goes in some variables and `invalid-token` in the others. That
way each row's 200 or 401 shows which variable was used.

| # | `GITHUB_AUTH_TOKEN` | `GH_TOKEN` | `GITHUB_TOKEN` | expect | source |
|---|---|---|---|---|---|
| 1 | valid | – | – | auth OK | `GITHUB_AUTH_TOKEN` |
| 2 | – | valid | – | auth OK | `GH_TOKEN` |
| 3 | – | – | valid | auth OK | `GITHUB_TOKEN` (**new**) |
| 4 | valid | invalid | – | auth OK | `GITHUB_AUTH_TOKEN` |
| 5 | invalid | valid | – | 401 | `GITHUB_AUTH_TOKEN` |
| 6 | valid | – | invalid | auth OK | `GITHUB_AUTH_TOKEN` |
| 7 | invalid | – | valid | 401 | `GITHUB_AUTH_TOKEN` |
| 8 | – | valid | invalid | auth OK | `GH_TOKEN` |
| 9 | – | invalid | valid | 401 | `GH_TOKEN` |
| 10 | valid | invalid | invalid | auth OK | `GITHUB_AUTH_TOKEN` |
| 11 | – | – | – | not found, exit 1 | – |
| 12 | `""` | valid | – | auth OK | `GH_TOKEN` (**bug fix**) |
| 13 | `" valid\n"` | – | – | auth OK | `GITHUB_AUTH_TOKEN` (**fix**) |

**What each row checks.** The exit code alone isn't enough, since a network
error also gives a failure. So:

- an "auth OK" row requires `Authenticated as:` in the output
- a "401" row requires `Invalid token`; any other failure is reported as
  **ERROR** (network, rate limit), not as a pass
- the `Using token from <VAR>` line must name the expected source (skipped with
  `--baseline`, see below)
- **leak check:** the token value must not appear anywhere in stdout or stderr

**Before and after.** Run it with `--baseline` against today's code first. Rows
3, 12 and 13 are expected to fail at that point, which shows the gap. Every
other row must pass, and that's the regression baseline. After the change, every
row passes without `--baseline`. Rows marked **new** or **fix** are the only ones
allowed to change result between the two runs.

**Then the live pytest cases.** The runner finishes by running
`pytest tests/test_kospex_github_token_env.py -m live -rs` with
`KOSPEX_TEST_GITHUB_TOKEN` set in the child environment, so layer 3 runs against
the same token in the same invocation.

**Output.** One table with each row's PASS / FAIL / ERROR and the variable used.
The exit code is non-zero if any row fails. The run makes about 15 calls to
`/user`.

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
- `tests/manual/github_token_matrix.py`: new manual live runner, not collected by pytest
- `docs/kgit.md`, `docs/troubleshooting.md`, `CLAUDE.md`
- `CHANGELOG.md`: note `GITHUB_TOKEN` is now read, and that empty values no longer block the next variable

## Decisions

- **Fallback to `gh auth token`: deferred, not in this change.** It's wanted
  later as an opt-in. The default would use a broad-scope token the user never
  handed to kospex.
- **GitHub Enterprise Server tokens** (`GH_ENTERPRISE_TOKEN`,
  `GITHUB_ENTERPRISE_TOKEN`): left out. `KospexGithub` hard-codes
  `api.github.com`, so they aren't relevant until GHES is supported.
- **No live token test in CI.** The live checks run only on demand through the
  manual runner (testing layer 4), so CI needs no secret and has no
  dependency on the GitHub API.
