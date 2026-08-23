# 202604 — repo_id generation cleanup

> **Status as of 2026-08-04.** This plan was written in April 2026 and is still
> the working plan for kospex#94, but part of it has since been overtaken by
> PR #126 (merged 2026-07-23). **Line numbers throughout this document have
> drifted** — treat them as indicative and re-locate by symbol name.
>
> | Step | Status |
> |---|---|
> | 1 — fix `parse_repo_id` / `parse_org_key` for nested groups | **Not started.** `len(parts) != 3` and the TODO are still in `kospex_utils.py`. |
> | 2 — delete `repo_id_from_url_parts` | **Not started.** Still present in `kospex_git.py`, still the live path in `kospex_dependencies.py` (`get_repo_authors`). |
> | 3 — delete `extract_git_url_parts` | **Half done by #126.** `812f0d1` made it a one-line delegation to `parse_git_remote`, so the *behavioural* problem this step existed to solve — no SSH / Azure DevOps / on-prem Bitbucket support — is **fixed**, at both call sites. What remains is cosmetic: removing the deprecated wrapper and repointing its caller. Note the caller named in Step 3 below is stale; the live one is now in `kospex_dependencies.py`, not `extract_commits_from_repo`. |
> | 4 — replace the krunner concat | **Not started.** The manual `+ "~" +` is still in `extract_krunner_file_details`. The open question about krunner filename encoding is still open. |
> | 5 — delete dead `git_url_to_repo_id` | **Not started.** |
> | 6 — implement `/generate-repo-id/` | **Not started.** Still returns `TODO_IMPLEMENT_REPO_ID_GENERATION`. |
> | 7 — route ad-hoc `split("~")` through the parsers | **Not started** (was always optional). |
>
> **One item is missing from this plan.** PR #126 says a pre-existing
> `.git`-suffix-stripping bug in the Azure DevOps parser (it truncates some repo
> names) is "tracked in #94" — but it was never added to the issue body or to
> this document. It belongs in Step 3's scope, since the parser consolidation is
> where it would be fixed.

## Overview

The codebase currently has **multiple, divergent implementations** of building and parsing a `repo_id`. Some correctly handle GitLab nested groups (by encoding `/` as `~~` in the org segment); others silently produce broken IDs. A canonical format already exists (`generate_repo_id`), but several call sites bypass it with ad-hoc string concatenation or a duplicate method that omits the slash handling.

This change consolidates `repo_id` construction to a single canonical path and fixes the parser to round-trip GitLab-style nested groups.

## Canonical format (target state)

```
repo_id  = "{remote}~{org_encoded}~{repo}"   where org_encoded = org.replace("/", "~~")
org_key  = "{remote}~{org_encoded}"
```

Builder:   `KospexGit.generate_repo_id(remote, org, repo)` — `src/kospex_git.py:455`
Parser:    `kospex_utils.parse_repo_id(repo_id)` — `src/kospex_utils.py:659` (needs fix — see below)
URL → id:  `KospexGit.parse_git_remote(url)` + `KospexGit.generate_repo_id(...)` — both static

## Current state (audit results)

### Builders (divergent)

| Location | Behaviour | Disposition |
|---|---|---|
| `src/kospex_git.py:455` `generate_repo_id` | **Canonical.** `@staticmethod`, encodes `/` → `~~`. | **Keep.** Single source of truth. |
| `src/kospex_git.py:470` `repo_id_from_url_parts` | Instance method, f-string concat, **no slash handling** — broken for GitLab nested groups. | **Delete.** Replace one live caller. |
| `src/kospex_git.py:512` (commented f-string) | Dead. | **Delete the comment.** |
| `src/kospex_core.py:1195` `extract_krunner_file_details` | `details["git_server"] + "~" + details["org"] + "~" + details["repo"]` — bypasses builder. | **Replace** with `KospexGit.generate_repo_id(...)`. |
| `src/kospex_utils.py:644` `git_url_to_repo_id` | Different **slash-delimited** format (`domain/org/repo`). Only caller is a commented-out line at `src/kospex_cli.py:1048`. | **Delete.** Dead code. |

### URL parsers (divergent)

| Location | Coverage | Disposition |
|---|---|---|
| `src/kospex_git.py:208` `parse_git_remote` | `@staticmethod`. Dispatches: ADO → Bitbucket on-prem → SSH → GitLab nested → GitHub-style → Google/Go. | **Keep.** Single entry point for URL parsing. |
| `src/kospex_git.py:397` `extract_git_url_parts` | Instance method. GitLab + GitHub + Google only — **missing ADO, Bitbucket on-prem, SSH**. | **Delete.** Redirect its one live caller (`src/kospex_dependencies.py:996`) to `parse_git_remote`. |

### Parsers (broken for GitLab)

| Location | Issue | Disposition |
|---|---|---|
| `src/kospex_utils.py:659` `parse_repo_id` | Splits on `~`, rejects anything that isn't exactly 3 parts. A GitLab id like `gitlab.com~group~~sub~repo` has 4 parts → returns `None`. TODO on line 663 already flags this. | **Fix** to reverse `~~` → `/` in the org segment and support nested groups. |
| `src/kospex_utils.py:679` `parse_org_key` | Similar: requires exactly 2 parts. | **Fix** alongside `parse_repo_id` for consistency. |

### Incidental `~`-splitting (reimplements `parse_repo_id` / `parse_org_key`)

All of these should go through `parse_repo_id` / `parse_org_key` once those handle nested groups:

- `src/kospex_core.py:582` — `org_key.split("~")`
- `src/kospex_core.py:1185` — `metadata.split("~")` on a krunner filename (note: this reads a filesystem-encoded name, so may need its own encoding decision — see *Open question* below)
- `src/kospex_query.py:152, 463, 974, 1986-1987, 2257` — `org_key.split("~")`
- `src/kweb2.py:655` — `org_key.split("~")`

### Web endpoint

- `src/kweb2.py:326` `generate_repo_id(url)` — stub returning `"TODO_IMPLEMENT_REPO_ID_GENERATION"`. **Implement** using `KospexGit.parse_git_remote` + `KospexGit.generate_repo_id`.

### Existing tests

- `tests/test_kgit.py:17` `test_repo_id` — covers `generate_repo_id` + `set_remote_url` across GitHub, GitLab nested (`gitlab.com~gitlab-org~~cloud-connector~gitlab-cloud-connector`), Bitbucket. Good baseline — do not regress.
- `tests/test_kgit.py:6` `test_parse_git_remote` — Google/Go URL only.
- `tests/test_web_endpoints.py:259` — stub endpoint test.
- No tests for `parse_repo_id`, `parse_org_key`, or `git_url_to_repo_id`.

## Plan

Execute in this order so each step leaves the tree green.

### Step 1 — Fix the parsers (additive, no call-site changes)

> **Corrected 2026-08-23 — the original recipe in this step was wrong.** It said to take
> "everything between `parts[0]` and `parts[-1]` as the org (joined back with `/`)". That does not
> reverse the encoding, because `~~` splits into an **empty element**:
>
> ```python
> 'gitlab.com~group~~subgroup~repo'.split('~')   # ['gitlab.com','group','','subgroup','repo']
> '/'.join(parts[1:-1])                          # 'group//subgroup'   ← doubled slash
> ```
>
> Implementing it as written ships a bug that **passes every flat-case test**. Use the form below,
> which peels the ends off the *string* rather than the split list.

- `src/kospex_utils.py` `parse_repo_id`: accept any id with at least two `~`, and decode the org by
  splitting off the first and last segments, then reversing `~~` → `/`:

  ```python
  server, rest    = repo_id.split("~", 1)      # 'gitlab.com', 'group~~subgroup~repo'
  org_enc, repo   = rest.rsplit("~", 1)        # 'group~~subgroup', 'repo'
  org             = org_enc.replace("~~", "/") # 'group/subgroup'
  ```

  Verified: nested → `('gitlab.com', 'group/subgroup', 'repo')`; flat `github.com~acme~svc` →
  `('github.com', 'acme', 'svc')`, unchanged. The returned dict keeps its current shape.
- `src/kospex_utils.py` `parse_org_key`: mirror it — `server, org_enc = org_key.split("~", 1)`
  then `org_enc.replace("~~", "/")`. Note this is a **string** split with `maxsplit=1`, not a list
  join; the same doubled-slash trap applies.
- Remove the TODO comment above the length check.
- Add tests in `tests/test_kospex_utils.py`: GitHub (`github.com~acme~repo`), GitLab nested
  (`gitlab.com~gitlab-org~~cloud-connector~gitlab-cloud-connector`), and the round-trip property
  `parse_repo_id(generate_repo_id(r, o, repo))["org"] == o`. **The nested case is the whole point** —
  a suite covering only flat ids passes the broken implementation above.

> **Open decision — which `org_key` form wins?** This step originally said `org_key` should be
> "rebuilt from the reconstructed encoded form so it round-trips", i.e. `gitlab.com~group~~subgroup`.
> But the SQL builds it from the columns as `_git_server || '~' || _git_owner`
> (`src/kospex_query.py`, three sites), and `_git_owner` holds the **decoded** org with a real slash
> (`src/kospex_git.py` `add_git_to_dict` sets it from `self.org`). So for a nested group the two
> disagree:
>
> | Source | `org_key` |
> |---|---|
> | `parse_repo_id` (as originally specced) | `gitlab.com~group~~subgroup` |
> | SQL over `_git_server` / `_git_owner` | `gitlab.com~group/subgroup` |
>
> An `org_key` from the parser would not match one from a query. Pick one form and make both
> produce it before implementing this step. Currently latent and untested — there are **no
> nested-group repos** in the reference database, which is why nothing has surfaced it.

### Step 2 — Delete the broken builder duplicate

- `src/kospex_dependencies.py:996-998`: replace
  ```python
  parts = self.git.extract_git_url_parts(repo_url)
  if parts:
      repo_id = self.git.repo_id_from_url_parts(parts)
  ```
  with
  ```python
  parts = KospexGit.parse_git_remote(repo_url)
  if parts:
      repo_id = KospexGit.generate_repo_id(parts["remote"], parts["org"], parts["repo"])
  ```
  (Both are `@staticmethod`, no instance needed. Add `from kospex_git import KospexGit` if not already imported.)
- Delete `repo_id_from_url_parts` at `src/kospex_git.py:470-473`.
- Delete the commented duplicate at `src/kospex_git.py:512`.
- Delete the commented `extract_git_url_parts` / `repo_id_from_url_parts` calls at `src/kospex_dependencies.py:934, 936`.

### Step 3 — Delete `extract_git_url_parts`

- `src/kospex_git.py:630` (inside `extract_commits_from_repo`): swap `self.extract_git_url_parts(...)` for `self.parse_git_remote(...)` (or `KospexGit.parse_git_remote(...)` — it's static). Verify the returned dict is consumed identically (same keys: `remote`, `org`, `repo`, `remote_type`).
- Delete the method at `src/kospex_git.py:397`.

### Step 4 — Replace the krunner concat

- `src/kospex_core.py:1195`: replace the manual `+ "~" +` concat with `KospexGit.generate_repo_id(details["git_server"], details["org"], details["repo"])`. Import `KospexGit` if not already imported.
- **Open question**: `extract_krunner_file_details` parses a repo_id out of a *filename* (line 1185 splits the filename on `~`). If krunner writes filenames using the canonical encoded form (`~~` for slashes), this already works — but the code currently assumes exactly 3 segments (`repo_mash[0..2]`), which will break for GitLab. Verify how krunner names these files; if they use `~~` encoding, extend the split to handle `>= 3` parts the same way `parse_repo_id` does. If they use a different encoding, document it and leave alone. **Do not silently guess.**

### Step 5 — Delete the dead `git_url_to_repo_id`

- Delete `src/kospex_utils.py:644-657`.
- Delete the commented reference at `src/kospex_cli.py:1048` (check a few lines of context — the live line 1049 uses `kgit.get_repo_id()`, so the comment is truly dead).

### Step 6 — Implement the web endpoint

- `src/kweb2.py:326-340` `generate_repo_id`: use `KospexGit.parse_git_remote(url)` + `KospexGit.generate_repo_id(parts["remote"], parts["org"], parts["repo"])`. Return `{"url": url, "repo_id": repo_id}` on success, `400` with an error message if `parse_git_remote` returns `None`.
- Update `tests/test_web_endpoints.py:259` to assert real output for at least one GitHub URL and one GitLab nested URL.

### Step 7 — Route ad-hoc `split("~")` sites through the parsers (optional but recommended)

Lower priority — only pursue if time allows and it doesn't sprawl. The code works today; the value is preventing future drift:

- `src/kospex_core.py:582`, `src/kospex_query.py:152, 463, 974, 1986-1987, 2257`, `src/kweb2.py:655` — replace each `org_key.split("~")` with `parse_org_key(org_key)` and read from the returned dict.

This step can be a follow-up PR — bundle with Step 1-6 only if the diff stays small.

## Verification

- `pytest` — full suite must pass.
- `pytest tests/test_kgit.py -v` — confirms GitLab nested-group encoding still works.
- New tests in `tests/test_kospex_utils.py` confirm parser round-trips.
- Manual check: `python -c "from kospex_git import KospexGit; from kospex_utils import parse_repo_id; rid = KospexGit.generate_repo_id('gitlab.com', 'group/sub', 'repo'); print(rid, parse_repo_id(rid))"` should print the encoded id and a dict where `org == "group/sub"`.

## Files changed (summary)

- `src/kospex_git.py` — delete 2 methods, keep `generate_repo_id` + `parse_git_remote` as the canonical pair.
- `src/kospex_utils.py` — fix `parse_repo_id` + `parse_org_key` for nested groups; delete `git_url_to_repo_id`.
- `src/kospex_dependencies.py` — migrate `get_repo_authors` to the canonical pair; drop commented dupes.
- `src/kospex_core.py` — replace krunner concat; verify krunner filename encoding (open question).
- `src/kweb2.py` — implement `/generate-repo-id/` properly.
- `src/kospex_cli.py` — delete commented `git_url_to_repo_id` line.
- `tests/test_kospex_utils.py` — add parser tests.
- `tests/test_web_endpoints.py` — replace stub assertion.

## Non-goals

- Renaming the canonical format (the `~`/`~~` scheme stays — it's already used in the DB and too many places to change here).
- Migrating existing stored data — the format doesn't change, only the code paths that build it.
- Changing the `_repo_id` column name in the schema.
