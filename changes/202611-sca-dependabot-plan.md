# `kospex sca -dependabot` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `kospex sca -dependabot IN.jsonl -out OUT.jsonl [-enrich]` copies each Dependabot alert line through with `sca_*` upgrade-distance and patched-version-advisory fields appended.

**Architecture:** A pure-ish module `src/kospex/dependabot_enrich.py` holds an `Enricher` (per-run caches over `KospexDependencies`) and `enrich_file()`. The `sca` Click command gets a thin `-dependabot` branch. Reuses `behind_fields()`, `version_detail()`, `release_list()`, `lookup_permitted()` and `kospex.dependencies.version_diff`; nothing is stored in the DB.

**Tech Stack:** Python 3.12, Click, pytest, deps.dev via `KospexDependencies`.

**Spec:** `changes/202611-sca-dependabot.md`

## Global Constraints

- Work in a worktree; run tests with `PYTHONPATH=$PWD/src pytest`.
- Output is JSONL: input keys unchanged and in order, then every `sca_*` key on every line (null when unknown).
- `-out` required. `-dependabot` is refused with `-json`, `-scope`, `-save`, `-package`, `-repo`, FILE_PATH (usage error).
- Lookups off (`KOSPEX_DEPS_ENRICH=false`) and no `-enrich`: exit 1, wording as `-package` ("... lookups are off (WHY). Add -enrich to look it up anyway.").
- deps.dev `cvss3Score: 0` without a vector is unscored: null, never 0 (`advisories()` already does this).
- SQL not involved. No DB migration. No CHANGELOG entry (silent feature). Docs page unlinked and not in `sitemap.xml`.
- No customer names, no mention of foundationx / deploy-kospex, no session URLs in any artifact or commit. Commits end with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- Never `split("~")` a `repo_id`; this feature does not parse it.

## Review Focus

- `current_version` null (a range): diff and behind fields null, patched advisories still filled, status `no_current_version`.
- `patched_version` null (no fix yet): patched fields null, behind fields filled, status `no_patched_version`.
- Ecosystem GitHub names that kospex has no registry entry for (`composer`, `actions`, `pub`, `swift`, `erlang`, `other`): row kept, `unsupported_ecosystem`, no network call.
- A package matching `KOSPEX_DEPS_PRIVATE`: row kept, `private_skipped`, no network call; invalid `KOSPEX_DEPS_PRIVATE` is an error for the run, not a per-row status.
- Blank line, non-JSON line, or JSON that is not an object in the input: error naming the 1-based line number; nothing half-written is left as a complete-looking file (write to the target only after a full read of the input is not required, but an error must exit non-zero and say the output is incomplete).
- Same package on many alerts: deps.dev asked once per package / version (assert on call counts).
- A `-out` path equal to the input path: refused, so the input is never clobbered.

---

### Task 1: Enricher (per-alert fields)

**Files:**
- Create: `src/kospex/dependabot_enrich.py`
- Test: `tests/test_dependabot_enrich.py`

**Interfaces:**
- Produces:
  - `ECOSYSTEM_MAP: dict[str, str]` GitHub name to kospex package_type: `pip→pypi, npm→npm, maven→maven, nuget→nuget, go→go, rubygems→rubygems, rust→cargo`. Keys lower-cased on lookup.
  - `SCA_FIELDS: tuple[str, ...]` in this order: `sca_status, sca_latest_version, sca_behind_level, sca_behind_major, sca_behind_minor, sca_behind_patch, sca_behind_releases, sca_current_age_days, sca_patched_diff_level, sca_patched_diff_major, sca_patched_diff_minor, sca_patched_diff_patch, sca_patched_version_vulnerabilities, sca_patched_version_max_cvss, sca_patched_ghsa_ids`
  - Status constants `OK, NO_CURRENT_VERSION, NO_PATCHED_VERSION, PRIVATE_SKIPPED, UNSUPPORTED_ECOSYSTEM, NOT_FOUND, LOOKUP_ERROR` (string values equal to the lower-case names).
  - `class Enricher: __init__(self, kdeps, now=None)`; `enrich(self, alert: dict) -> dict` (returns a new dict: alert keys then every SCA_FIELDS key; never mutates the input).
- Consumes: `KospexDependencies.release_list(type, name)`, `.behind_fields(type, name, version, releases)`, `.version_detail(type, name, version, releases)` returning `(detail|None, status)`, `.lookup_permitted(type, name)` returning `(bool, reason)`, `kospex.dependencies.version_diff.version_diff / change_level`.

- [ ] **Step 1: Write failing tests** in `tests/test_dependabot_enrich.py`, using a stub `kdeps` (small class with the four methods above, recording calls) rather than HTTP. Names and assertions:
  - `test_all_keys_present_and_input_keys_first`: output keys == input keys + SCA_FIELDS, in order; input dict unchanged.
  - `test_ok_row`: npm `cookie`, current `0.4.0`, patched `0.7.0`, stub latest `0.7.2`, patched advisories two ids (scores 5.3 and None) -> `sca_status=="ok"`, `sca_patched_diff_level=="minor"`, diff major/minor/patch `0/3/0`, `sca_patched_version_vulnerabilities==2`, `sca_patched_version_max_cvss==5.3`, `sca_patched_ghsa_ids` is the list of the two ids, `sca_latest_version=="0.7.2"`.
  - `test_raw_diff_preserved`: current `1.9.9`, patched `3.0.0` -> level `major`, diff `2/-9/-9`.
  - `test_current_version_null`: status `no_current_version`, diff and behind fields None, patched advisory fields filled.
  - `test_patched_version_null`: status `no_patched_version`, patched fields None, behind fields filled.
  - `test_unsupported_ecosystem`: `composer` -> `unsupported_ecosystem`, stub recorded zero calls.
  - `test_private_skipped`: stub `lookup_permitted` returns `(False, "maven:com.acme")` -> `private_skipped`, zero release_list calls.
  - `test_unknown_package`: `release_list` returns None -> `not_found`; if stub flags a URL error -> `lookup_error`.
  - `test_ecosystem_case_insensitive`: `"PIP"` maps to pypi.
  - `test_zero_advisories`: patched version clean -> count `0`, max_cvss None, ids `[]` (not None).
  - `test_caching`: 3 alerts for the same package and versions -> one `release_list`, one `version_detail` per distinct version.
- [ ] **Step 2: Run** `PYTHONPATH=$PWD/src pytest tests/test_dependabot_enrich.py -v`. Expected: FAIL, ImportError.
- [ ] **Step 3: Implement `Enricher`** in `src/kospex/dependabot_enrich.py`. Order of checks: unsupported ecosystem, then `lookup_permitted`, then `release_list` (cache per package; None -> `not_found`/`lookup_error`). Then fill: `behind_fields` for `current_version` (skip if null); `version_detail` for `current_version` only for `sca_current_age_days`; `version_detail` for `patched_version` for the advisory fields (max of non-None `cvss3_score`; ids from each entry's `id`); offline `version_diff`/`change_level` when both versions present. Status after filling: `no_current_version` if current null, elif `no_patched_version` if patched null, else `ok`. Cache `release_list` per (type, name) and `version_detail` per (type, name, version) including negative results. A patched version deps.dev does not know leaves the advisory fields None and status `ok` is NOT claimed: use `not_found`.
- [ ] **Step 4: Run tests**; expect all PASS.
- [ ] **Step 5: Commit** `git add src/kospex/dependabot_enrich.py tests/test_dependabot_enrich.py` then `git commit -m "feat(sca): Dependabot alert enricher"`.

### Task 2: File runner

**Files:**
- Modify: `src/kospex/dependabot_enrich.py`
- Test: `tests/test_dependabot_enrich.py`

**Interfaces:**
- Consumes: `Enricher` (Task 1).
- Produces: `class DependabotInputError(Exception)` with `.line_no: int`; `enrich_file(in_path, out_path, enricher, on_progress=None) -> collections.Counter` (status -> count). `on_progress(done: int, status: str)` called per line when given.

- [ ] **Step 1: Failing tests**: `test_enrich_file_round_trip` (3 lines in, 3 lines out, same order, each valid JSON with all SCA_FIELDS, Counter matches); `test_blank_line_is_error_with_line_number` (`line_no == 2`); `test_non_object_json_is_error`; `test_malformed_json_names_line`; `test_out_equals_in_refused` (ValueError before anything is read, via `os.path.samefile`/resolved paths); `test_error_leaves_no_complete_looking_output` (on error the partial output file is deleted).
- [ ] **Step 2: Run**; expect FAIL.
- [ ] **Step 3: Implement** `enrich_file`: refuse same resolved path; stream line by line, write `json.dumps(row, ensure_ascii=False)` per line; on any error close and remove the output, re-raise `DependabotInputError(line_no, msg)`. Blank lines are errors (kgit never writes them).
- [ ] **Step 4: Run**; expect PASS.
- [ ] **Step 5: Commit** (`feat(sca): enrich a Dependabot JSONL file`).

### Task 3: CLI branch

**Files:**
- Modify: `src/kospex_cli.py` (the `sca` command, near line 1062-1160: add option `-dependabot` / `dependabot_path`, `click.Path(exists=True, dir_okay=False)`, and a `_sca_dependabot(...)` helper beside `_sca_package`)
- Test: `tests/test_sca_dependabot_cli.py`

**Interfaces:** Consumes `enrich_file`, `Enricher`, `DependabotInputError`.

- [ ] **Step 1: Failing tests** using `CliRunner` against the `sca` command with `KospexDependencies` patched to the Task 1 stub: `test_requires_out` (usage error), `test_refused_with_json_scope_save_package_repo_file` (parametrised; `-scope` only when given on the command line, as `-package` does via `ParameterSource`), `test_lookups_off_requires_enrich` (env `KOSPEX_DEPS_ENRICH=false` -> exit 1, message contains `Add -enrich`), `test_lookups_off_with_enrich_runs`, `test_env_true_without_flag_runs`, `test_bad_input_exit_1_names_line`, `test_summary_on_stderr_counts_statuses`.
- [ ] **Step 2: Run**; expect FAIL.
- [ ] **Step 3: Implement** the branch, placed after the `enrich_decision` / CA-bundle checks and before the `-package` branch's sibling checks; reuse `_sca_package`'s refusal pattern. Progress to stderr via the existing `Console(stderr=True)`; summary line per status count. `kospex_cli.sca` docstring gets a `-dependabot` paragraph (it is `--help` text, which is fine for a silent feature).
- [ ] **Step 4: Run** new tests plus `tests/test_first_release.py` and the existing sca tests; expect PASS.
- [ ] **Step 5: Commit** (`feat(sca): -dependabot option`).

### Task 4: Docs and finish

**Files:**
- Create: `docs/sca-dependabot.md` (unlinked; not in `sitemap.xml`, not linked from any page): usage, every field with meaning, the raw-diff caveat, behind-vs-patched distinction, statuses, the lookups-off rule, example line.
- Modify: `changes/202611-sca-dependabot.md` (status: built) and the project's silent-feature register if present in this repo (check; the register may live elsewhere, in which case say so in the final report rather than naming it in a commit).

- [ ] **Step 1:** write the doc; grep that `sca-dependabot` appears in no other file except the changes doc.
- [ ] **Step 2:** full run `PYTHONPATH=$PWD/src pytest -q -rs`; start a worktree kweb on a free port per project notes if the web tests need exercising; quote pass/skip counts.
- [ ] **Step 3:** manual run against a few lines of the real `docs/kgit-github-queryables.md` example with lookups on; paste the output line into the report.
- [ ] **Step 4: Commit** (`docs(sca): -dependabot notes (unlinked)`).

## Self-review

- Spec coverage: fields (T1), file I/O and errors (T2), flags and lookups-off rule (T3), silent docs (T4), no DB (none needed). Dedup and private skip are in T1.
- Names consistent: `Enricher.enrich`, `enrich_file`, `SCA_FIELDS`, `DependabotInputError` used identically across tasks.
- Decision made beyond the spec (mine, flag for review): a patched version deps.dev does not know is `not_found`, not `ok`; blank input lines are errors; `-out` equal to the input is refused.
