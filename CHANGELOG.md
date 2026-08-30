# Changelog

The format of this changelog is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)

## Unreleased

### Upgrade notes

**Reported numbers change in this release, in six ways.** Anything already
showing kospex output — dashboards, screenshots, exported reports — will disagree
with a post-upgrade run. None of this is a regression; the earlier figures were
wrong or incomplete.

1. **Per-author commit counts drop, substantially for maintainers.** Merge
   commits are no longer counted as authorship. 12.3% of commits on a 109-repo
   estate are merges, and the share reaches 96.3% for some authors — one
   `pallets/click` maintainer moves from 876 commits to 326 authored plus 550
   merges. `% commits` is computed on the authorship total, so the denominator
   shrinks too: **a developer who never merges can see their percentage rise
   while their own count is unchanged.** This is the largest mover in the
   release and the one most likely to be read as a regression.
2. **"Last commit" dates render in UTC** instead of the committer's local offset.
   Same instant, different presentation. Additionally, 7 of 109 repos in a
   109-repo estate reported the *wrong* last commit and now report the right one.
3. **`commit_files` gains rows for content introduced by merges.** Repos whose
   history contains conflict resolutions get file rows that never existed before
   (0.6%–8.5% of rows on the repos measured; **0** on repos with clean merges).
4. **`commits.parents` is populated for newly-synced commits only.** Existing
   rows stay NULL. Treat NULL as *unknown*, not as "not a merge".
5. **Go repos report more dependencies.** Transitive (`// indirect`) modules are
   now recorded where they were previously discarded, and repos whose `go.mod`
   uses single-line `require` directives gain dependencies that were never parsed
   at all — such a repo may have reported **zero** Go dependencies before. Filter
   on `package_use = 'direct'` to compare like with like against an earlier run.
6. **`krunner osi` reports Go and .NET dependencies for the first time, and Go
   rows change `package_type`.** `osi` previously skipped `go.mod` and `*.csproj`
   entirely — on a 109-repo estate that is **175 rows where there were none**
   (168 Go, 7 NuGet). Separately, Go rows now carry `package_type = "go"` rather
   than `"Go module"`; that column is part of the `dependency_data` primary key,
   so new rows do not collide with existing ones and an `osi` run demotes the old
   ones. Anything filtering on `package_type = 'Go module'` needs updating. Two
   smaller shifts come with it: `requirements.py` / `.rst` / `.lock` are no longer
   parsed as manifests (they never were manifests), and `requirements-wheel-*.txt`
   files now are.

**None of the fixes backfill.** Commit sync is incremental (`--since` the last
recorded commit), so existing rows keep their old values until a repo is dropped
and re-synced. `kreaper delete-repo -repo_id <id> -yes` clears every table
carrying a `_repo_id`, including `repos`, which resets the sync provenance so the
next sync walks full history, then re-sync with `kospex sync-directory <path>`.
Use `-dry-run` first, and back up `~/kospex/kospex.db` before starting.

Full procedure, including the query for which repos are stale and the one thing
a re-sync does **not** fix: **[Refreshing data → Upgrading to
0.1.0](https://docs.kospex.io/refreshing-data#upgrading-to-010-re-syncing-after-the-ingest-fixes)**.

### Added
- **Every kospex, kgit, krunner and kreaper command now warns when the database
  is behind.** A banner on stderr reporting the pending count and
  `kospex upgrade-db -apply`. It is called from each Click group callback rather
  than from the database connect, so it appears only for subcommands that
  actually run — not on `--help` or shell tab-completion, which never reach the
  callback. `kospex --quiet` suppresses it. Nothing is blocked and no exit code
  changes: on a behind database the affected commands still fail with their own
  errors, and the banner is what connects those errors to the cause. stdout is
  untouched, so piped output such as `kospex list-repos -out file.csv` is safe.
  See `changes/202608-migrations-on-clean-install.md`.
- **AI coding agent files are now tagged in `file_metadata`.** Bumped
  `panopticas==0.0.16` → `0.0.17`, which detects the artifacts of 20 AI coding
  agent products via 60 path-based rules. Every recognised file gains three
  tags in `tech_type`: `AI`, the product brand, and the kind of artifact it is —
  so `CLAUDE.md` stores as `|AI|Claude|instructions|` and
  `.cursor/rules/style.mdc` as `|AI|Cursor|rules|`. Products are brand-level
  (`Claude` covers both Claude Code and Claude Desktop; `Gemini` covers the CLI
  and Code Assist), with pseudo-products `Agents`, `MCP` and `llms.txt` for
  vendor-neutral files like `AGENTS.md` and `.mcp.json`. Detection is
  path-based only — panopticas never opens a file to decide this.

  No kospex code change was needed: `kospex_git.py` already calls
  `get_filename_metatypes()`, and the existing `tech_type LIKE '%|tag|%'` query
  in `kospex_query.py` reaches the new tags unchanged. `tech_type LIKE '%|AI|%'`
  returns every AI file in a repo; substituting a product name narrows to one
  tool. Detected products: Claude, Copilot, Cursor, Gemini, Codex, Windsurf,
  Aider, Cline, Roo Code, Continue, Amazon Q, Junie, Goose, Augment, OpenHands,
  Kilo Code, Trae, plus `Agents`, `MCP` and `llms.txt`. See
  `changes/202608-panopticas-ai-tags.md`.

- **Error tracking by type for krunner scans.** New `krunner_utils.RunErrors`
  collects per-repo failures during a scan, logs each at ERROR level to
  `~/kospex/logs/krunner.log`, echoes it to the console, and prints a
  count-by-type summary table at the end of the run (nothing prints when the run
  is clean). Failures are tagged with kospex-level names — `MISSING_CLONE`,
  `GIT_ERROR` — rather than Python exception classes, so both the summary and the
  log read in kospex terms. Wired into `krunner branches`; the other repo-looping
  commands can adopt it as they're touched.
- **`krunner branches -strict`** exits non-zero if any repo errored, for CI and
  cron jobs that need failures to surface without parsing output. The default
  stays exit 0 so existing scripts are unaffected.
- **Repo page links to its dependencies.** `/repo/{repo_id}` gains a
  **Dependencies** link, placed first in the Quick Links bar ahead of Tech
  Landscape. The `/dependencies/{repo_id}` route already existed but nothing
  linked to it, so reaching a repo's dependency list meant typing the URL.

- **Content a merge introduced is now recorded.** `git log --numstat` reports no
  files for a merge, which is correct — a merge that only combines branches
  authored nothing, and attributing files to it double-counts work already
  credited to the branch commits. But it also hid an *evil merge* (git's own
  glossary term): a conflict resolution, or a file added while merging, exists
  in no parent and landed nowhere. Those rows now appear in `commit_files`.
  Clean merges still contribute nothing, and there is a test pinning that.
  Additions and deletions are counted against the closest parent rather than the
  first, which isolates what the merger actually typed — first-parent counts
  include the branch's own work and inflate merge churn 2.7x on some repos.
  Closes #121. See `changes/202608-merge-content-and-commit-delimiter.md`.
- **`commits.parents` is now populated.** The column has been declared since the
  Mergestat-derived schema and never written — 0 of 254,997 rows. It holds a
  count, so merge detection is `parents > 1`; never `== 2`, since octopus merges
  are real (nixpkgs has one with 16 parents). No migration: the column already
  existed. Existing rows stay NULL until a repo is re-synced, so consumers must
  read NULL as unknown rather than as "not a merge".
- **`kreaper delete-repo -dry-run`** reports the row counts per table and deletes
  nothing, so a destructive operation can be inspected first. It does not require
  `-yes`: the dry run writes nothing, and demanding the confirmation flag to see
  a preview only trains people to type it. Counts come from
  `Kospex.repo_id_row_counts()`, which walks the same table list the delete does,
  so the preview cannot drift from the deletion. See
  `changes/202608-kreaper-dry-run.md`.

- **A `merges` column in key-person output**, in the CLI table, the web table at
  `/key_person/{repo_id}`, and `krunner`. Merging is evidence of knowledge in its
  own right — the merger read and accepted those changes — so the count is
  surfaced rather than discarded. It makes visible the developer with few commits
  who integrates most of a subsystem: a continuity risk that is invisible either
  way round under a single number, since counting merges as commits makes them
  look like an ordinary prolific author and filtering merges out drops them off
  the table. **Comparable within a repository only** — squash-and-merge and
  rebase workflows produce no merge commits at all (18 of 88 repos with more than
  200 commits measured here are below 1%), so zero means "this project squashes",
  not "does not integrate". Both tables carry a footnote saying so.

### Changed
- **Raised the panopticas floor to `>=0.0.19`.** 0.0.19 adds a queryable tag
  vocabulary (`get_tags()`, `get_filetypes()`, `get_languages()`, derived from
  the detection rules so they cannot drift), `--json` on every panopticas
  command, and migrates its table output from prettytable to rich. **No tag was
  renamed or removed**, so stored `tech_type` values stay valid and no re-sync
  is required for correctness. `tests/test_panopticas_tag_contract.py` passes
  unchanged against 0.0.19, which is what that guard exists to confirm.

  Note the floor is not a delivery gate: since the pin was relaxed from an exact
  version, a new panopticas release already reaches fresh kospex installs
  without a kospex release. This bump states the minimum version kospex is
  tested against, and does not by itself change what users resolve.

- **The AI tags panopticas emits for `CLAUDE.md` and `GEMINI.md` have changed
  shape, and the old ones are gone.** `CLAUDE.md` was
  `|Claude|AI|Claude Code|` and is now `|AI|Claude|instructions|`; `GEMINI.md`
  was `|Gemini|AI|Gemini CLI|` and is now `|AI|Gemini|instructions|`. The
  `Claude Code` and `Gemini CLI` tags no longer exist. Nothing in kospex queries
  them, so no code change was required — but **any saved query, report or
  external consumer filtering on `Claude Code` or `Gemini CLI` will silently
  return nothing rather than erroring.**

  `tech_type` is cached at sync time, so **existing databases keep the old tags
  until their repos are re-synced.** The version-aware skip-guard in
  `Kospex.file_metadata` compares the recorded `last_panopticas_version` against
  the installed one, so the bump to 0.0.17 marks every repo for a
  `file_metadata` rebuild on its next sync — no manual reindex, but also no
  change until that sync happens.

- **Syncing a repo from a second clone is now refused instead of silently
  repointing it.** `repos._repo_id` is the primary key and every sync upserts
  `file_path` into that row, so syncing the same repo from a different directory
  used to overwrite the recorded path with no warning — losing which clone the
  existing data was built from. This is how a `KOSPEX_CODE` pointed at a
  throwaway directory could leave a repo permanently registered at a path that
  later disappeared. A sync now raises `RepoPathConflict` when the repo is
  already recorded against a **different path that still exists on disk**, and
  the check runs before any commits are ingested (they were previously written
  well before the row was repointed). A recorded path that no longer exists is
  treated as a genuine move and repoints with a WARNING, so stale rows
  self-heal. `kgit clone` checks before cloning, so nothing is downloaded for a
  repo the sync would refuse. Override with `kgit clone -force` or
  `kospex sync-directory -force`; bulk paths (`kgit clone -filename`,
  `kgit github`, `krunner git-pull`, `kospex sync-directory`) skip the
  conflicting repo and carry on. See `changes/202607-repo-path-conflict.md`.

- **`krunner todo` now needs `-save` to write to the DB.** It was the only one
  of the three observation-writing krunner commands with no opt-in — merely
  running it inserted a `GREP_TODO` row per match into `observations` (and reset
  `latest = 0` on matching prior rows). It now prints findings by default and
  writes only with `-save`, matching `branches` and `repo-size`, whose `-save`
  also defaults to off.

- **Commit-date aggregates now render in UTC.** `last_commit` and `first_commit`
  come back as `2026-07-10T07:45:56Z` rather than `2026-07-10T16:45:56+09:00` —
  the same instant, the same `days_ago`, the same status bucket, in a consistent
  zone. A column of dates in mixed offsets was never comparable by eye. The raw
  `committer_when` on each row keeps its original offset, so nothing is lost from
  the data. This is a **visible change to every "last commit" shown in the web UI
  and CLI tables**.
- **The commit log is parsed on a control-character delimiter.** The ingest
  format moved from `#`-separated to ASCII Unit Separator (`\x1f`), and the
  parser now asserts its field count instead of unpacking optimistically. See
  Fixed, below, for what the old delimiter did.
- **`KospexGit.parse_git_remote()` can now return `None`.** It previously matched
  any `scheme://host/path` and always returned a populated dict, so every
  `if not parts:` guard in the codebase was unreachable and junk URLs were given
  a plausible `repo_id`. Unrecognised URLs are now rejected. Verified against the
  live database: **0 of 109** existing repos change their `repo_id`.

### Removed
- **`KospexGit.sync_repo`** — an abandoned Oct 2025 "work in progress refactor"
  of `Kospex.sync_repo` with no callers anywhere. It could not have run (it
  called four methods that do not exist on `KospexGit`) and had drifted behind
  the live method in two ways that would have corrupted data if it ever were
  wired up: no `author_email`/`committer_email` lowercasing, and no
  `developer_stats` update.

- **`kospex_mergestat`** — 154 lines of dead code that shipped in every wheel.
  Mergestat was the prototype's git-query engine, replaced by direct git wrapping
  in `d12d3dc` (2023-12-03); nothing has imported it since, and the module's own
  header said so. Verified orphaned before deletion — no live imports, tests,
  docs or packaging references beyond its `py-modules` entry. See
  `changes/202608-remove-kospex-mergestat.md`.

### Fixed
- **The table-introspection cache served one in-memory database's tables to
  another.** `_db_key()` keyed in-memory databases by `id(db)` as a
  "per-instance key", but `id()` is unique only among *live* objects and CPython
  reuses addresses aggressively — 300 in-memory databases produced just 8
  distinct keys, and 292 of 300 lookups returned a dead database's table set.
  `KospexData` validates every table name against this cache before
  interpolating it into SQL, so a stale answer rejects tables that exist,
  raising `ValueError: Table '<name>' is not a known Kospex table`. File-backed
  databases key on their path and were never affected, so the CLI and web UI
  were correct; the defect was latent for anything building a database in
  memory, which `KospexQuery.create_memory_kospex_query()` — used by `krunner
  osi` — does. In-memory databases are no longer cached at all: the read costs
  ~1.1us against ~10us for a file-backed one, so there was nothing to protect,
  and a database built up table by table at runtime should not be cached anyway.
  Closes #184. See `changes/202608-introspect-in-memory-cache-key.md`.
- **`krunner osi` skipped Go and .NET dependency files entirely.** `osi` matched
  manifests with a substring chain while `kospex sca` used its own predicates, so
  `go.mod` and `*.csproj` were handled by one path and silently dropped by the
  other. Both now dispatch off the extractor registry — `classify()` selects the
  entry, `resolve_parser()` turns its `parse_ref` into a callable, and
  `package_type` comes from the entry rather than an `ecosystem` mapping that
  could drift. On the reference estate this is **175 dependency rows that `osi`
  previously produced none of** (168 Go, 7 NuGet). The registry's coverage-matrix
  test now asserts parity instead of recording the gap, and a second test fails
  any entry claiming only one scanner, so it cannot reopen quietly. Requirements
  matching also delegates to `panopticas.is_pip_requirements`, which is correct
  in both directions where kospex was not: it matches
  `requirements-wheel-*.txt` (excluded before by a second hyphen) and rejects
  `requirements.py` / `.rst` / `.lock`, which the substring check parsed as
  manifests. Closes #107, advances #180. See
  `changes/202608-osi-registry-dispatch-c1.md`.
- **NuGet dependencies were never persisted by any path.** `nuget_assess()` built
  its records, printed them as a table and fell off the end without returning
  them, and `assess()`'s dispatch discarded the result anyway — two independent
  bugs, so fixing either alone changed nothing. `.csproj` parsing now lives in
  `kospex/extractors/nuget.py`, shared by both scan paths, and is hardened against
  CWE-776 (uncontrolled XML entity expansion): manifests come from cloned
  third-party repositories, and Python's ElementTree expands internal entities —
  four nested levels reach 10,000 characters. A `DOCTYPE` is refused before
  parsing, which no legitimate MSBuild project file carries. Closes #107.
- **go.mod reported fewer dependencies than it declares, in three ways.** Single-line
  `require <module> <version>` directives were never parsed — `parse_go_mod_from_file()`
  gated every parse path behind an exact `require (` line, so the one-per-line form
  that `go mod init` plus a `go get` produces was skipped entirely, and a repo using it
  reported zero Go dependencies. `// indirect` modules were parsed and then discarded
  by `gomod_assess()`, so transitive Go dependencies never reached `dependency_data` at
  all; they are now recorded as `PACKAGE_USE_TRANSITIVE`, with the same deps.dev
  enrichment as direct modules. And a bare comment line inside a `require ( ... )` block
  was recorded as a module named `//`, because it satisfied the two-parts test. Direct
  modules now also carry `PACKAGE_USE_DIRECT`, which `gomod_assess()` never set.
  `exclude` / `replace` / `retract` directives remain excluded — previously only
  incidentally, now deliberately and with a test. **go.mod repos will report more
  dependencies after the next sync**: transitive rows appear where there were none, and
  repos using single-line requires gain dependencies they never had. Nothing is deleted.
  Closes #177 and #178. See `changes/202608-gomod-transitive-and-single-require.md`.
- **Merge commits were counted as authorship, inflating whoever merged.**
  `commit_stats()` counted every row with `COUNT(*)`, so a merge added one to the
  person who merged — typically a maintainer, lead or release manager, which is
  exactly the population `key_person()` exists to measure. A commit now counts as
  authorship unless it is a *clean* merge (`parents <= 1 OR _files > 0`); a merge
  carrying content of its own, such as a conflict resolution, exists in no parent
  and is real work by the merger, so it still counts. `parents` is NULL for
  commits synced before this release, so the `_files` clause carries those rows —
  the rule is evaluated per row and needs no migration flag on a partially
  re-synced estate, and no follow-up once `parents` is populated. Closes #170.
  See `changes/202608-merge-commits-and-authorship.md`.
- **A `#` in an author or committer email silently corrupted every later field.**
  The commit ingest split git's output on `#`, which is legal in both a name and
  an email. One `#` shifted every subsequent field and the last field absorbed
  the remainder, with no error. `NixOS/nixpkgs` has 97 commits authored
  `git#v1@kaction.cc`, which parsed as `author_email='git'` and
  `committer_name='v1@kaction.cc'` — and `author_email` is the primary developer
  identity key, so those commits attributed to a developer called `git`. The
  database could not reveal this: querying for `#` in any field returns zero rows
  because the parse consumed the delimiter before the insert. Closes #163.
- **`file_metadata.committer_when` was NULL for non-ASCII filenames.** git quotes
  such paths as `"caf\303\251.py"` unless `core.quotePath=false` is set, and the
  quoted form never matched the path panopticas walked. All git walks now disable
  quoting. Closes #116.
- **Repository and developer "last commit" dates could be wrong by up to 17
  hours.** Commit dates are stored as ISO-8601 text carrying each committer's
  local UTC offset, and text comparison of ISO-8601 is only valid when every
  value shares one offset — there are 33 distinct offsets across 254,997 commits.
  `MAX(committer_when)` therefore returned the wrong row whenever the true latest
  commit had a more westerly offset than a near tie. Measured against the live
  database: **7 of 109** repo `last_commit` values, **54 of 19,448** developer
  last-commits, and **411 of 218,101** per-file latests. Aggregates and ordering
  now run on the true instant. `KospexData` gains `select_latest_date()`,
  `select_earliest_date()` and `order_by_date()` so the policy lives in one
  place. No backfill needed — this changes how stored data is read, not what is
  stored. Closes #154. See `changes/202608-utc-date-ordering.md`.
- **A `file-metadata` rebuild spawned one `git log` per file.** `get_repo_files()`
  ran a subprocess for every file in the repo, so the cost scaled with the file
  count rather than with git's work — a run over ~105 repos was still on repo 21
  after 40 minutes and was abandoned, leaving the estate partially re-tagged. It
  now does a single walk per repo: **19x** faster on `pallets/click`, **57x** on
  `facebook/react`, **167x** on `babel/babel`. Closes #152. See
  `changes/202608-repo-files-single-git-walk.md`.
- **Remote URLs with embedded credentials wrote the password into `repo_id`.**
  `https://user:pw@host/o/r.git` produced `user:pw@host~~o/r` — and `repo_id` is a
  primary key across `commits`, `commit_files` and `file_metadata`, and is
  rendered in the web UI. Hosts are now normalised through `urlparse().hostname`,
  which drops credentials and the port. That also makes the SSH and HTTPS clone
  URLs for one Bitbucket Server or Azure DevOps repository resolve to the same
  `repo_id`, where they previously produced two.
- **A clean install never applied its database migrations.**
  `connect_or_create_kospex_db()` built the frozen v2 baseline schema and created
  an empty `schema_migrations` table, but nothing ran the migrations — only
  `kospex upgrade-db -apply` did, and no first-run command calls it. A brand-new
  database was therefore three migrations behind on first use, which broke two
  paths outright: `kgit clone` / `kgit pull` raised `no such column: last_fetch`,
  and a dependency save raised `table dependency_data has no column named
  resolution`. `kospex init` did not touch the database at all, so nothing in the
  normal setup path closed the gap. A fresh install now comes out at version 5
  with all three applied. The `new_db` test also treats a database file with no
  `kospex_config` table as new, which repairs one left behind by the kweb bug
  below — `os.path.isfile()` alone would have left it unmigrated permanently.
- **kweb crashed on a clean install, and poisoned the database for later runs.**
  `kweb2.py` had no startup hook and reached the database only through
  `KospexQuery`, which opens the path directly with sqlite_utils — creating an
  empty file when none exists. Running kweb first on a clean install therefore
  produced `sqlite3.OperationalError: no such table: kospex_config` on every
  request, and left a zero-table file behind that made `os.path.isfile()` true so
  the schema was never bootstrapped afterwards either. A FastAPI `lifespan` hook
  now builds and migrates the schema once per server boot, before any request.
  Startup failure is logged rather than fatal.
- **`kospex init` reported a broken setup as healthy.** It validated
  directories, permissions, environment variables and logging, but never the
  database — so on a clean install it printed `Overall Status: HEALTHY` over a
  database three migrations behind that would crash `kgit pull`. A behind or
  unwritable database is now a critical issue. `kospex init --validate` gained a
  `Database:` block reporting what the bootstrap actually did ("created during
  this invocation, 3 migration(s) applied"), which is honest about the fact that
  the module-level `Kospex()` creates the database at import — so "does it
  exist?" is always true by the time the command body runs. `kospex
  system-status` printed a `Database table version status` heading followed by
  nothing; it now shows version, applied count and any pending migrations.

- **A failed dependency save left a manifest with zero current dependencies.**
  `save_dependencies()` ran its demote (`UPDATE ... SET latest = 0` for every
  prior row of the `(_repo_id, file_path)` being rewritten) and its
  `upsert_all()` as two statements with no transaction. If the upsert failed,
  the demote was already committed and the replacement rows were never written,
  so every "current dependencies" view for that file read empty — silently,
  because the demote itself succeeded. The rows survived at `latest=0` (the
  demote is an UPDATE, never a DELETE), so the data was recoverable, but it was
  no longer reported. The two statements now run inside a single
  `Database.atomic()` transaction, so a failed upsert rolls the demote back and
  the previous dependency set stays current. `sqlite-utils>=4.1.1` is now
  declared in `pyproject.toml`, matching the existing `requirements.txt` pin:
  `atomic()` issues an explicit `BEGIN`, and sqlite3's `with conn:` does **not**
  roll the demote back here. See `changes/202608-save-dependencies-atomic.md`.
- **`kospex list-repos -db -repo_id` returned no rows.** `list-repos` defines
  `-repo_id` as an `is_flag` option meaning "add the Repo ID column to the
  output", but the whole kwargs dict is forwarded to
  `KospexData.set_params_by_id()`, which read `repo_id=True` as a scope value
  and emitted `WHERE _repo_id = 1` — matching nothing. The same dict also
  carries display-only keys such as `db=True`, which defeated the
  `any(id_params.values())` all-scope test and sent an unscoped call down an
  error branch that printed `ERROR: can't identify {...}` to stdout while
  correctly applying no filter. Scope resolution now considers only `repo_id`,
  `org_key` and `server`, and only when they hold a non-empty string; anything
  else means "all scope". Also removed a leftover `print(kwargs)` debug
  statement from `Kospex.list_repos()`, so `-db` output is no longer preceded
  by a raw params dict. Row counts for `-db`, `-db -repo_id` and
  `-db -server SERVER` now match the `repos` table exactly.

- **Renamed and removed dependencies stayed flagged as current forever.**
  `save_dependencies` demoted prior rows keyed on `(_repo_id, file_path,
  package_name)`, taking the name from the *incoming* record — so the demote
  only ran for names present in the batch. Two classes of row were left stuck
  at `latest = 1`. **Renamed packages:** `package_name` is part of the
  `dependency_data` primary key, so a parser change that derives a different
  name inserts a new row rather than updating the old one, and a demote keyed
  on the new name never reaches the predecessor — after the PEP 508 parser
  above began separating extras, `mkdocstrings[python]` (`package_not_found`)
  and `mkdocstrings` (`resolved`) were both current for the same file, so a
  repo reported 17 dependencies where 16 was correct. **Removed dependencies:**
  a package deleted from a manifest has no incoming record at all, so nothing
  ever demoted it — delete a package from `requirements.txt`, re-parse, and
  kospex kept reporting it as a current dependency. This second one predates
  the parser work. The demote is now keyed on `(_repo_id, file_path)`: a
  re-parse supersedes everything previously extracted from that manifest,
  whatever those packages were called.

  This does not grow the table — the demote is an `UPDATE`, never a `DELETE`,
  and row count is driven by the upsert primary key (chiefly `hash`). Only the
  `latest` flag changes. Existing stale rows need no migration; they are
  demoted by the next re-parse of their file. One behaviour change worth
  knowing: a *partial* parse now demotes packages it failed to re-extract,
  where previously they were left looking current. `krunner osi` accumulates
  every file and writes once, so it is not exposed to that.
- **Unpinned Python dependencies were silently dropped and never reached
  `dependency_data`.** `parse_pypi_package_declaration` substring-tested version
  operators against the whole declaration — *including the environment marker* —
  then split on the first one found. Four consequences: markers leaked into the
  package name (`requests; sys_platform == 'win32'` parsed as a package called
  `requests; sys_platform`); `>=` was tested before `~=`, so a `~=` spec split on
  the marker's operator; neither field was stripped, leaving `"hypothesis "` and
  `" 3.30"`; and unpinned declarations plus `<`, `<=`, `!=` and `===` specs
  returned `None`, which `parse_pip_requirements_file` discards. Measured on a
  synced copy of `theskumar/python-dotenv`, whose `requirements.txt` declares ten
  packages with nine unpinned: `dependency_data` held **one row**. Parsing now
  uses `packaging.requirements.Requirement` (PEP 508, already a dependency of
  this module); an unversioned declaration is a valid requirement and records an
  empty `package_version`, matching how the `pyproject.toml` path already handles
  it. `None` is now reserved for lines that are not requirements at all — URLs,
  `-e .`, `-r other.txt`. Declared names and multi-specifier text are preserved
  verbatim rather than canonicalised, because `package_name` and
  `package_version` are both part of the `dependency_data` primary key and
  rewriting either would insert duplicate rows on re-sync. Closes
  [#29](https://github.com/kospex/kospex/issues/29).

  **A re-parse is required, and the numbers will get worse.** The mangling
  happened at parse time, so existing databases cannot be repaired in place.
  Re-run whichever command populated `dependency_data` — `krunner osi -all` for
  the whole install, `krunner osi <repo_id>` for one repo, or `kospex deps` /
  `kospex sca` for a single file or repo. **`kospex sync` does not re-parse
  dependencies**; it populates commits and `file_metadata`, and nothing in
  `kospex_core.py` writes `dependency_data`. After the re-parse, dependency
  counts rise and freshness figures fall, because previously-dropped unpinned
  packages reappear as rows with no resolvable version. That is the parser
  reporting what was always there, not a regression.
- **Unresolved dependencies rendered as green / "Up to Date" on
  `supply_chain.html`.** PR #106 normalised `versions_behind` to `null` for
  unresolved dependencies, and in JavaScript `null <= 2` is *true* — so a package
  whose version could not be determined at all drew in the most reassuring state
  available. The same coercion affected size: `d3.scaleLinear()` treats `null` as
  0 and returns the range minimum, so those nodes also drew smallest, reading as
  "least versions behind". Adds an `isUnresolved()` guard applied at every site
  that consumed `versions_behind` — fill colour, status class, status text, size,
  hover tooltip and detail panel, the last two of which printed a literal
  `null` — plus a grey legend entry, "Unresolved — version could not be
  determined". A package that is both unresolved *and* vulnerable still shows as
  vulnerable, matching the precedence used elsewhere. Closes
  [#109](https://github.com/kospex/kospex/issues/109). This is the view-layer
  half of the same problem as #29 above: fixing the parser alone increases the
  number of `null` rows, which would have moved the misreporting from the data
  layer to the view layer.
- **`krunner todo` no longer records TODOs from git's own hook samples.** The
  grep had no path operand, so it recursed into `.git/` and logged four bogus
  `GREP_TODO` observations per repo from the `.git/hooks/*.sample` files git
  ships. It now passes an explicit pattern and path with `--exclude-dir=.git`,
  and runs in the repo via `cwd=` instead of relying on `set_repo_dir()` having
  chdir'd the process (with a hand-rolled `os.chdir` to undo it). krunner now
  contains no `os.chdir` and no `os.system` at all. See
  `changes/202607-krunner-todo-cwd.md`.
- **`krunner branches` no longer aborts the whole run on one missing clone.**
  `repos.file_path` records where a repo's clone lives on disk, and that path can
  stop existing at any time — the clone is deleted, moved, or was only ever a
  throwaway directory. `KospexGit.get_branches()` chdir'd into it with no guard,
  so a single stale row raised `FileNotFoundError` and killed the scan for every
  remaining repo (104 healthy repos skipped because of one). `get_branches()` now
  runs git with `cwd=` instead of `os.chdir()`, so neither a missing directory nor
  a failing git command can strand the process working directory, and `branches`
  skips unreadable repos and carries on. A path that exists but isn't a git repo
  is handled the same way. See `changes/202607-krunner-error-tracking.md`.

## 0.0.40 - 2026-07-27

### Added
- **Dependency resolution status — categorise & record why a deps.dev lookup
  failed**. When kospex enriches a dependency via deps.dev, the lookup can come
  back empty; previously that was flattened to `versions_behind = "Unknown"`
  (from `krunner osi`) or `""` (from `sca`) — one opaque bucket that lost the
  *reason*. A new nullable `dependency_data.resolution` column (migration `0005`)
  now classifies every lookup into one of six categories: `resolved`,
  `no_version`, `unresolved_spec` (a range/git-URL/`workspace:*`/`latest`, etc.),
  `version_yanked` (package exists on deps.dev but that version doesn't),
  `package_not_found` (typo / private / removed), or `lookup_error` (transient
  5xx/timeout). The classifier lives at the shared `depsdev_record` enrichment
  seam, so both DB-writing paths (`krunner osi` and `assess()`/`sca`) record it,
  and every non-resolved outcome is logged at INFO for a greppable audit trail.
  The `/dependencies/` page shows the category as a badge instead of the
  misleading "Up to date". Legacy rows keep `resolution = NULL` and render as
  before until re-synced (`krunner osi -all` refreshes everything).
  **Requires `kospex upgrade-db -apply` before the upgraded code writes
  dependencies** (a DB on the old schema fails with `no such column:
  resolution`). See `changes/202607-dependency-resolution-status.md`.
  PR [#106](https://github.com/kospex/kospex/pull/106).
- **`KospexQuery.url_request_with_status()`** — a status-aware variant of
  `url_request` returning `(content, status)`, so callers can tell an HTTP 404
  apart from a transient network error. `url_request` is now a thin wrapper over
  it (single copy of the cache/fetch/upsert logic; `headers` are now actually
  passed through to the request).
- **`/osi/` shows per-file extraction status**. The Open Source Inventory now
  marks each discovered dependency file with an **"Extracted?"** column — whether
  kospex has parsed and enriched its dependencies into `dependency_data` — and a
  "Dependency extraction coverage" callout groups the files with no extracted
  details by kind: types with no parser yet (package manifests, runtime versions,
  containers), kinds recognised but not scanned for packages (SCA config,
  lockfiles), and supported files not yet scanned. Built on a new
  `kospex.extractors.registry` classifier (`classify()`) that names each
  dependency-bearing file type and its scanner support. See
  `changes/2026-07-20-osi-extraction-status-design.md` and
  `changes/2026-07-17-extractor-registry-classifier-design.md`.
  PRs [#114](https://github.com/kospex/kospex/pull/114),
  [#117](https://github.com/kospex/kospex/pull/117).

### Changed
- **`versions_behind` normalised to integer-or-NULL**. The `"Unknown"` / `""`
  string sentinels are no longer written for unresolved dependencies — a
  resolved dependency stores an integer (0 = up to date) and every unresolved one
  stores `NULL`, paired with its `resolution` category (above). Downstream readers
  that assumed the sentinel should treat `NULL` as "not resolved".
- Azure DevOps and on-premise Bitbucket repos now clone to a flatter directory
  (`dev.azure.com/myorg-myproj/myrepo` instead of `dev.azure.com/myorg/myproj/_git/myrepo`),
  matching the `repo_id` that sync already generates. Existing clones under the old layout are
  orphaned on disk and can be deleted; no `repo_id` values change.
- `kgit clone` with no arguments prints help instead of exiting silently, and its help text
  documents SSH URL support.

### Fixed
- **Brace-less git renames are now parsed**. `git log --numstat` only uses the
  brace form when the old and new paths share a common leading directory or
  trailing component; with nothing in common it emits a bare `old => new` (e.g.
  `LICENSE.rst => LICENSE.txt`, or a root-level file moved into a subdirectory).
  `parse_git_rename_event` required braces, so the raw arrow string was stored
  as `commit_files.file_path` — a second, independent source of the
  `file_metadata.committer_when` NULL that surfaces as "last commit: None" on
  `/osi/`. The new path is now kept for the brace-less form. Existing rows do
  not self-heal (an incremental re-sync only reads commits newer than the last
  synced one, so the old rename commit is never re-parsed); see
  `changes/2026-07-21-rename-arrow-braceless-remediation.md` for the one-off SQL.
- **`/osi/` "last commit" no longer shows `None` for renamed files**. A
  directory-level rename (a file moved up or down a level) renders in
  `git log --numstat` with an empty brace side (e.g.
  `.github/{workflows => }/dependabot.yml`), and `parse_git_rename_event` left a
  doubled slash (`.github//dependabot.yml`). That malformed `commit_files` path
  never matched the working-tree path, so `file_metadata.committer_when` was
  left NULL and surfaced as "last commit: None". Repeated slashes are now
  collapsed after rename substitution. PR
  [#115](https://github.com/kospex/kospex/pull/115). Note: because `kospex sync`
  is incremental, rows already stored with the bad path do **not** self-heal —
  DBs synced before this fix can clear them with the one-off SQL in
  `changes/2026-07-21-commit-files-double-slash-remediation.md`. (The
  non-ASCII-filename variant of the same NULL is tracked in
  [#116](https://github.com/kospex/kospex/issues/116).)
- **`pypi_assess` no longer drops the version on multiple-specifier
  requirements**. A `requirements.txt` line with more than one version specifier
  (e.g. `requests>=1.0,<2.0`) was emitted with only `package_name` — the declared
  spec was lost and no `resolution` was recorded. It now routes through the same
  `depsdev_record` seam as every other line: the declared spec is retained as
  `package_version` and the row classifies as `unresolved_spec` (no deps.dev call,
  since the spec isn't a concrete version). Fixes
  [#108](https://github.com/kospex/kospex/issues/108).
- **`/package-check/upload` no longer 500s on unresolved dependencies**. The
  status-classification loop compared `versions_behind` with `> 6` / `> 2`; once
  unresolved rows began carrying an explicit `None` (from the `versions_behind`
  normalisation above), `None > 6` raised `TypeError` and turned the whole upload
  response into an HTTP 500 for any manifest containing a single unresolvable
  dependency (a git/URL dep, `workspace:*`, `*`, `latest`, a private/typo'd
  package, …). Fix: the classification moved into a pure `_classify_upload_status`
  helper that coalesces `versions_behind`/`advisories` to 0 before comparing and
  surfaces an honest status label (e.g. "Unresolved spec", "Not found") for the
  failure categories instead of silently reporting "Current".
- **Technology Landscape — `/tech/` drill-down link now URL-encoded**. On the
  `/landscape/` page, each technology row linked to `/tech/{Language}` using the
  raw language name, so names containing URL-special characters drilled down to
  the wrong technology (or nothing): `C#`/`F#` (the `#` started a URL fragment,
  so the server saw `/tech/C`/`/tech/F`), `C++` (the `+` decoded to a space),
  and `Visual Basic` (raw space). Fix: encode the value in
  `templates/landscape.html` with Jinja2's `urlencode` filter
  (`/tech/{{ row['Language'] | urlencode }}`); the `/tech/{tech}` FastAPI route
  auto-decodes the path param, so it receives the original language string.
  The Language name is now also a `/tech/` link (matching the repos column), so
  both columns drill into the per-technology view.
  See `changes/202606-landscape-tech-link-urlencode.md`.
- `kgit clone` now accepts scp-style SSH URLs (`git@host:org/repo.git`), completing the
  `kgit github -ssh-clone-url -out-repo-list` → `kgit clone -filename` pipeline. Previously
  every SSH URL failed with `ERROR with <url>`.
- Repo names containing dots (e.g. `dashboard.js`) no longer truncate when parsed from an SSH
  URL, which produced a wrong `repo_id` and a wrong on-disk directory.
- `clone_repo()` resolves the code directory through `HabitatConfig` instead of a bare
  `os.getenv`, so it no longer depends on a CLI entry point having populated `os.environ` at
  import. CLI behaviour is unchanged; this fixes a `TypeError` for library callers.

### Security
- Clone and pull no longer shell out via `os.system`, closing a command-injection path for
  repo URLs read from a file.
- Clone destinations are confined to the kospex code directory; a crafted Azure DevOps URL
  could previously resolve outside it.
- `/package-check/upload` now sanitises the client-supplied filename (`os.path.basename` plus a
  containment check on the resolved path), closing a path-traversal write/delete outside the
  temp directory (CWE-22).
- `krunner`'s secret-scanner commands (`trufflehog`, `gitleaks`, `semgrep`) and `krunner grep`
  now invoke their tools via argv lists instead of a shell, closing a command-injection path
  where a repository's remote-derived identifier (or the grep keyword) reached the shell
  (CWE-78). The scanners also now run against the intended repository directory rather than the
  directory kospex was launched from.

## 0.0.39 - 2026-06-14

### Fixed
- **Packaging — `templates/` and `static/` restored to the wheel**. The
  0.0.38 wheel shipped no template or static asset files (180 KB vs
  0.0.37's 526 KB; zero templates vs 50; zero static assets vs 8), so
  `kweb2`-rendered routes could not resolve any template — including
  `_entity_header.html`, surfacing as `TemplateNotFound` at
  `/repo/{repo_id}` and similar. The cause was the
  `[tool.setuptools.packages.find]` directive introduced in 0.0.38, whose
  `include = ["kospex*"]` filter restricted setuptools to packages whose
  name begins with `kospex` and excluded the sibling top-level
  `templates/` and `static/` directories from `package-data` attachment.
  Fix: extend the find directive to
  `include = ["kospex*", "templates*", "static*"]` and add
  `namespaces = true` so the data dirs are discovered as namespace
  packages without requiring `__init__.py` files. Verified by rebuilding
  the wheel and inspecting contents — 50 templates and 8 static assets
  present, including `templates/_entity_header.html`. Editable installs
  (`pip install -e .`) were unaffected (they bypass packaging), which is
  what hid the regression during local development.

## 0.0.38 - 2026-06-14

> **Note (2026-06-14)**: this release is structurally broken — the wheel
> ships no `templates/` or `static/` assets, so `kweb2`-rendered web pages
> cannot render. Use 0.0.39 or later. Root cause and fix in the 0.0.39
> §Fixed entry above.

### Added
- New `/org/{org_key}` organisation view mirroring the repo view — org Commit Summary, Developer Status, Technology Landscape, and a Repositories table linking back to each `/repo/{repo_id}`. See `changes/202605-repo-org-header-redesign.md`.
- Script-driven DB migration system at `src/kospex/db/`. Replaces the auto-`ALTER TABLE` `upgrade-db` command with numbered SQL migration files (`0003_<slug>.sql`) plus optional Python `up(db)` backfills, applied transactionally and tracked per-row in a new `schema_migrations` table. CLI: `kospex upgrade-db` (status / dry run) and `kospex upgrade-db -apply`. Framework only — no actual migration files shipped yet. See `changes/202605-db-migration-system.md`.
- `krunner osi` now parses `pnpm-lock.yaml` via the new `kospex.extractors.pnpm` module — pnpm projects produce SCA results instead of being skipped. Supports lockfile versions 5/6/9 (`direct` / `dev` / `resolved` classification). Required the `panopticas` pin bump to `0.0.16` (which added `pnpm-lock.yaml` file-type detection). PR [#101](https://github.com/kospex/kospex/pull/101).
- `kospex sca` and `kospex deps` now support `pnpm-lock.yaml` (lockfile versions 5, 6, 9)
- `package_use` field populated for pnpm packages (`direct`, `dev`, `transitive`) and npm `package.json` packages (`direct`, `dev`)
- `PACKAGE_USE_*` vocabulary constants added to `kospex_schema` for consistent cross-parser use

### Changed
- `kospex summary` now renders the per-repo table and the status-count table as Rich tables (matching `kospex orgs` / `kospex stats`) instead of PrettyTable, and prints a status legend below the summary describing the Active (≤90d) / Aging (91–180d) / Stale (181–365d) / Unmaintained (>365d) thresholds. Rendering-only change — status logic, CSV (`-out`) output, and the returned results are unchanged. See `changes/202605-summary-rich-status-legend.md`.
- Repo view (`/repo/{repo_id}`) header is now a `server / org / repo` breadcrumb with a bold title and entity label instead of the raw `repo_id`; the server and org segments are links (server → existing `/orgs/{server}`, org → the new org view). Malformed `repo_id`/`org_key` now return HTTP 404 instead of a generic 500. See `changes/202605-repo-org-header-redesign.md`.
- `KOSPEX_TABLES` / `REPO_TABLES` list constants in `kospex_schema.py` replaced by runtime introspection helpers (`get_kospex_tables(db)`, `get_repo_tables(db)`) in `src/kospex/db/introspect.py`. Eliminates "forgot to update the constant" as a class of bug — `kreaper delete-repo -repo_id` now auto-detects repo tables via PRAGMA. See `changes/202605-db-migration-system.md`.

### Removed
- Deprecated `kospex sync-dependencies` command removed — an obsolete CLI-era flow predating the repo-sync/web-UI model. Its `-file` path cloned and synced each dependency's source repo (superseded by `kospex sync` plus the `/osi/` and `/dependencies/` views); its `-repo` path was only a `NOT IMPLEMENTED!` stub. The repo-level dependency walk it gestured at is tracked for a future `kospex deps -repo`. See `changes/202605-osi-dependencies-pipeline.md`.
- Old auto-alter DB upgrade helpers (`generate_alter_table`, `apply_alter_table_commands`, `validate_square_brackets*`) — superseded by the script-driven migrator. About 250 LOC of dead code removed. See `changes/202605-db-migration-system.md`.

## 0.0.37 - 2026-05-10

### Added
- [`kgit bitbucket` Bitbucket API token support](https://github.com/kospex/kospex/issues/90) — new `BITBUCKET_API_TOKEN` env var (with optional `BITBUCKET_EMAIL` or existing `BITBUCKET_USERNAME`, mutually exclusive) ahead of Atlassian's app-password sunset. Atlassian disables all existing app passwords on **2026-06-09**; migrate before then. Accepts both unscoped Atlassian account API tokens and Bitbucket-scoped tokens (the latter need `read:project:bitbucket`, `read:repository:bitbucket`, `read:workspace:bitbucket`). See [Atlassian token management](https://support.atlassian.com/bitbucket-cloud/docs/api-tokens) and [auth recipes](https://support.atlassian.com/bitbucket-cloud/docs/using-api-tokens/). Legacy `BITBUCKET_USERNAME` + `BITBUCKET_APP_PASSWORD` still works with a stderr deprecation warning naming the cutoff; legacy code is scheduled for removal shortly after 2026-06-09. See `changes/20260507-bitbucket-api-token-support.md`.
- [`kospex commit-stats EMAIL` command](https://github.com/kospex/kospex/issues/78) — per-developer onboarding indicator reporting total commits, 90-day commits, tenure (years), and days to the Xth commit (default 11). Supports `-request_id` to scope by full `repo_id`, `org_key` (`server~owner`), or git server. Output rendered as a Rich table.
- `request_id` parameter on `KospexQuery.commits()` that dispatches to server / org_key / repo_id filters by tilde count, keeping scope filtering out of the CLI layer.
- [`-csv PATH` option on `kospex orphans`](https://github.com/kospex/kospex/issues/67) — writes orphaned repo results to a CSV file in addition to the existing on-screen PrettyTable output.
- [`krunner find-actions` command](https://github.com/kospex/kospex/issues/95) — extracts every `uses:` reference (step actions and job-level reusable workflows) from GitHub Actions workflow YAML across the repos kospex has metadata for. Outputs CSV with classification (`action_owner`, `action_name`, `pinned_version`, `pin_type` — `HASH`/`TAG`/`NONE`, and `github_action` — `yes` for `actions/*` and `github/*` owners). Useful for supply-chain audit. Introduces the new `kospex.extractors` package — first occupant of a planned family of file-type extractors.

### Changed
- `kospex orgs` output now renders as a Rich table (titled "Organizations") instead of PrettyTable, matching the style of `kospex stats`. The unused `KospexUtils.orgs_prettytable()` helper has been removed. See `changes/202605-orgs-rich-table.md`.
- Pinned `panopticas==0.0.15` (was `panopticas>=0.0.14`) — locks to a known-good release to prevent unexpected breakage from upstream changes.

### Fixed
- npm: bumped `postcss` to fix moderate XSS advisory [GHSA-qx2v-qp2m-jg93](https://github.com/advisories/GHSA-qx2v-qp2m-jg93) (was 8.5.8, transitive via tailwindcss).
- [Replaced hardcoded `VERSION` in `kospex_core.py` with `importlib.metadata`](https://github.com/kospex/kospex/issues/92) — `pyproject.toml` is now the single source of truth for the version, preventing future release version mismatches
- `kospex commit-stats` docstring was stale (copied from the deps.dev connectivity command) and now correctly describes the command.
- `krunner osi -all` no longer crashes on malformed `package.json` files (e.g. babel test fixtures) — the parser error is logged and the file skipped, letting the run complete and write `OSI-all.csv`. Also removed a spurious `ERROR: can't identify {'request_id': None}` message that appeared on startup. See `changes/20260420-krunner-osi-all-fix.md`.
- [`krunner osi` no longer crashes on malformed `pyproject.toml` files](https://github.com/kospex/kospex/issues/97) (repro: `github.com~pypa~build`, which ships `tests/packages/test-bad-syntax/pyproject.toml` as a deliberately broken fixture). `KospexDependencies.parse_pyproject_file` now catches `tomllib.TOMLDecodeError`, `OSError`, and `packaging.requirements.InvalidRequirement`, logs a warning, and returns `[]` so the run continues with the next file. See `changes/20260505-pyproject-parser-error-handling.md`.

## 0.0.36 - 2026-04-06

### Added
- [HabitatConfig centralized configuration class](https://github.com/kospex/kospex/issues/85) — new singleton in kospex namespace for all paths, config, and directory management, with 40+ unit tests
- [DuckDB git ingestion MVP](https://github.com/kospex/kospex/issues/86) — new GitDuckDB and GitIngest modules for scalable git commit and file storage
- [Commit history endpoint /history/](https://github.com/kospex/kospex/issues/84) — new web page for browsing commit history per repo
- `kospex stats REPO_ID` command — developer stats with Rich table output for key person analysis (#88)
- `developer_stats` database table for precomputed per-developer commit/file statistics with percentage columns
- `file_hotspots` database table schema
- `/summary2/` endpoint with horizontal stacked bar visualization (experimental)
- Developer with ID endpoint in kweb2
- [Assessments directory and assessment types](https://github.com/kospex/kospex/issues/87) — standardized output filenames and ~/kospex/assessments/ directory
- `kgit sync-repo` command for DuckDB sync (experimental, no UI available)
- Data schemas documentation (docs/data-schemas.md) for SQLite and DuckDB

### Changed
- [Upgraded FastAPI to 0.135.3, Starlette to 1.0.0, uvicorn to 0.43.0](https://github.com/kospex/kospex/issues/89) — migrated all 47 TemplateResponse calls to new Starlette 1.0.0 signature
- [Replaced PyGitHub with direct REST API calls](https://github.com/kospex/kospex/issues/83) — fewer dependencies, same functionality
- Renamed `kospex.py` to `kospex_cli.py` to avoid package namespace clash with the kospex package
- Bumped Python version to 3.12 in CI workflow and Docker
- Updated git import to lowercase `author_email` and `committer_email` for consistency
- Pinned `click==8.3.1`, `duckdb==1.4.3`, `prettytable==3.17.0`, `PyYAML==6.0.3` dependencies
- Auto-update developer stats after sync (`sync_repo`, `kgit clone`, `sync-directory`)
- Enhanced `KospexData.set_params_by_id()` to accept `request_id` string directly
- Added `@timer()` decorators on GitHub API methods for performance tracking
- Updated npm dependencies: chart.js 4.5.1, tailwindcss 3.4.19
- Updated `requirements.txt` from clean Docker install
- Added ChangeLog to `project.urls` in pyproject.toml

### Fixed
- Fixed UI bug on /summary/ page where headings didn't align with repo status bubbles
- Updated vulnerable version of urllib3
- Pinned `requests==2.33.0` to fix security vulnerability (GitHub Dependabot)
- [Removed accidental claude-code npm dependency and fixed npm audit vulnerabilities](https://github.com/kospex/kospex/issues/91)
- Fixed TemplateResponse breaking change with Starlette 1.0.0 — fresh installs would get 500 errors on all template-rendering endpoints

### Removed
- Flask and Werkzeug dependencies — completed FastAPI migration
- PyGitHub dependency — replaced with direct REST API
- Unused dependencies cleaned up

# 2025 Releases

## 0.0.35 - 2025-11-26

### Fixed
- [Fixed 404 by adding a favicon.ico](https://github.com/kospex/kospex/issues/76)
- [Add pyproject.toml to supported list in kospex sca]([htt](https://github.com/kospex/kospex/issues/81))
- [Implement key-person function in kospex and krunner](https://github.com/kospex/kospex/issues/82)

## 0.0.34 - 2025-10-27

### Fixed

- [Fixed a bug which crash the csv process in the krunner osi command](https://github.com/kospex/kospex/issues/76)

## 0.0.33 - 2025-10-26

### Fixed
- [Encoding type was UTF-8 can now handle UTF-16](https://github.com/kospex/kospex/issues/74)

### Work in Progress
- [Connectivity check for SSL and trusted CAs](https://github.com/kospex/kospex/issues/73)
We've done some initial work on implementing SSL CA checks.


## 0.0.32 - 2025-10-24

### Fixed
- Some issues relating to kospex dependencies with missing imports and referenced function calls

## 0.0.31 - 2025-10-24

### Added
- [In memory cache for kweb](https://github.com/kospex/kospex/issues/61)

The in-memory cache for kweb is now implemented using a simple dictionary data structure. This allows for faster lookups and reduces the need for disk I/O operations. It's only for a few endpoints (summary, developers) but can be reused for other endpoints as well.

- [Implemented MVP for krunner osi command](https://github.com/kospex/kospex/issues/72)

### Fixed
- [Repos by Tech didn't display properly](https://github.com/kospex/kospex/issues/45)

## 0.0.30 - 2025-10-06

### Added
- [Added krunner -year to developer-tech](https://github.com/kospex/kospex/issues/71)
- [Added -days to meta/author-domains](https://github.com/kospex/kospex/issues/70)

### Fixed
- [Krunner repo-size and branches don't display tables](https://github.com/kospex/kospex/issues/64)

### Work In Progress
- [Started some background code changes on email mapping](https://github.com/kospex/kospex/issues/69)

## 0.0.29 - 2025-10-03

### Added
- [Added krunner tenure command](https://github.com/kospex/kospex/issues/68)

## 0.0.28 - 2025-10-02

### Added
- [Added krunner dependencies command](https://github.com/kospex/kospex/issues/65)
- [Added krunner devs-by-tag command](https://github.com/kospex/kospex/issues/66)

## 0.0.27 - 2025-10-01

### Changed
- added a tenure field to the key_person function in KospexQuery
- bumped panopticas version to 0.0.13 for additional binary file type detection

### Added
- [krunner developer-tech function](https://github.com/kospex/kospex/issues/63)
- also created a method to load an in memory load of kospex_db and return akospex_query (create_memory_kospex_query)

## 0.0.26 - 2025-09-18

### Fixed
- [Missed a few more places to lowercase the email address](https://github.com/kospex/kospex/issues/55)

## 0.0.25 - 2025-09-17

### Added
- [Krunner file-metadata command to re-run metadata](https://github.com/kospex/kospex/issues/58)
This enables re-running metadata for files in Krunner, when there's new features in panopticas.

### Changed
- [Bumped panopticas to 0.0.12 for pipeline and CI file types](https://github.com/kospex/kospex/issues/59 )

### Fixed
- [Missed a couple of places to lowercase the email address](https://github.com/kospex/kospex/issues/55)

## 0.0.24 - 2025-09-16

### Changed
[Bumped panopticas to 0.0.11](https://github.com/kospex/kospex/issues/52)

### Added
- [Add branch observation in krunner](https://github.com/kospex/kospex/issues/53)
- [add repo size observation in krunner](https://github.com/kospex/kospex/issues/57)

### Fixed
- [Issues handling mixed cased emails in some queries](https://github.com/kospex/kospex/issues/55)
- [years active now displayed on repos page](https://github.com/kospex/kospex/issues/54)
- [Committed some files missed in email bot detection](https://github.com/kospex/kospex/issues/47)

## 0.0.23 - 2025-09-03

### Added
- [Handle Bitbucket on premise URLs for repo_id generation](https://github.com/kospex/kospex/issues/51)

### Changed
Nothing

### Fixed
Nothing

## 0.0.22 - 2025-09-02

### Added
- [Email analysis and bot detection](https://github.com/kospex/kospex/issues/47)
- [Add port and host binding for kweb, add docker awarenes](https://github.com/kospex/kospex/issues/48)
- [Minimal docker image for testing](https://github.com/kospex/kospex/issues/49)
- [Handle Azure DevOps clone urls](https://github.com/kospex/kospex/issues/50)
### Changed
Nothing

### Fixed
Nothing

## 0.0.21 - 2025-08-14

### Added
- [MVP version of logging](https://github.com/kospex/kospex/issues/22)
- [Add a collab graph link from the collab page](https://github.com/kospex/kospex/issues/42)
- [Added initial kweb testing](https://github.com/kospex/kospex/issues/46)
### Changed
- Added a leavers tenure view where we show how long they have been in an org or repo
- Removed kgit pull as we don't really need it anymore
### Fixed
- [Removed old Flask Kweb](https://github.com/kospex/kospex/issues/41)
- [Improve Sync time](https://github.com/kospex/kospex/issues/39)


## 0.0.20 - 2025-07-21

### Added
- Added a [network graph view of commit collaborators ](https://github.com/kospex/kospex/issues/38)
- Added metadata repos view to see last commit, last sync of the data
- [key person risk view in kweb based on the commit percentages](https://github.com/kospex/kospex/issues/40
### Changed
- Single developer view now displays the years active in repos and the technologies used.
- Updated [HTML views to use src/templates/_header.html](/changes/202507-template-header-updates.md)
### Fixed
- [kospex CLI exited with error code 2](/changes/click-exit-2-error.md) when no arguments are provided

## 0.0.19 - 2025-07-09

### Added
- Nothing

### Changed
- Nothing

### Fixed
- [Static files (js,css) not packaged for kweb](https://github.com/kospex/kospex/issues/37)

## 0.0.18 - 2025-07-07

## Major Changes (non breaking)

This version did a refactor from Bootstrap 4 to [TailwindCSS](https://tailwindcss.com/) and
now manages the CSS and JS files programatically and stores local versions
[Documentation for TailwindCSS web and JS assets](/web-assets.md)

Refactored from [Flask to FastAPI](https://github.com/kospex/kospex/issues/33)

### Added
- [Create local static css and js files and package management](https://github.com/kospex/kospex/issues/31)
- [Implemented Author Collaborator view](https://github.com/kospex/kospex/issues/32)
- Exposed the collaborator feature (/collab/) via the /repo/ page to show author / committer summary
- Exposed the tenure feature (/tenure/) via the /repo/ page to show stats on current commits and authors
- Better [single commit and commits per file view](https://github.com/kospex/kospex/issues/35)

### Changed
- Removed broken hotspots link from repo view

### Fixed
- Probably a few bugs here and there

## 0.0.17 - 2025-04-20

### Added
  - Implemented a pretty_table in the kospex_git class
### Changed
  - kospex --version now uses the version number in kospex_core.pyg
  - kgit --version now uses the version number in kospex_core.py
  - [Implemented PyGithub to remove some bespoke code](https://github.com/kospex/kospex/issues/28)
  - Initial work on [SSH clone issue](https://github.com/kospex/kospex/issues/7) However, still a WIP
### Fixed
  - Cleanup the requirements.txt from some experimental uses
  - Removed commented code in Git changes
  - removed some unnecessary switches in kgit github



## 0.0.16 - 2025-04-06

### Added
  - Added integration in [malicious package API in SCA](https://github.com/kospex/kospex/issues/25)
  - Added MVP [db migrations feature](https://github.com/kospex/kospex/issues/23)
### Changed
  - kospex sca now has a cut down version of kospex deps, plus a malware flag for malicious packages
  - tests/run-kdocker.sh now mounts a data directory for output, and the tests folder for scripts to run
  -
### Fixed
  - Removed some commented out dead and test code

## 0.0.15 - 2025-02-08

### Added
  - a static parse_ssh_git_url method
  - MVP sca method to eventually replace kospex deps with kospex sca
  - an Initial End Point for dependencies queries
  - kreaper can now remove all rows with repo_id from a table
  - initial [orphans feature](https://github.com/kospex/kospex/issues/20)
  - tenure functions and pages to show how long developers have worked

### Changed
  - Improved tests for Git URLs
  - Removed references to pygit2 (mostly commented out) as no longer used

### Fixed
  - Parsing of ssh urls like git@github.com:kospex/panopticas.git
  - parsing of git URLs with trailing slash which failed e.g. https://github.com/kospex/kospex/
  - kreaper can now delete a repo_id out of all tables
  - Shell escaped filenames to handle spaces in Git commands
  - Improved SCC testing so that only Git managed files are added to Metadata table



## 0.0.14 - 2025-01-15

### Added
  - a switch to orphans to allow a targe list of repos to assess
### Changed
  - KospexGit now has safer (handles "/" in org) repo_id generation when setting a repo_url

### Fixed
  - Bug fix when kospex metadata is run and not in a Git dir.


## 0.0.13 - 2025-01-06

### Added
  - Initial End Point for OSI (Open Source Inventory) queries
  - Architecture Decision Record are in /docs/adr/
  - New kospex CLI metadata function using Panopticas
  - [Use panopticas for base file detection](https://github.com/kospex/kospex/issues/11)

### Changed
  - KospexGit now uses Panopticas for the get_repo_files function

### Fixed
  - commented out the experimental graph-api which broke the workflow build, kweb kospex_query
  - Link to GitHub from the developer page when we know their GitHub handle [16](https://github.com/kospex/kospex/issues/16)

# 2024 Releases

## 0.0.12 - 2024-12-01

### Added
  - added Treemap graph which can be a toggled graph from bubble charts

### Changed
  - None

### Fixed
  - graph APIs for bubble and treemap dont dislay repos when showing developers
  - Fixed bug where landscape drilldown didn't work [Issue 15](https://github.com/kospex/kospex/issues/15)


## 0.0.11 - 2024-11-25

### Added
  - Added an <id> param to the /repos/ endpoint for easier linking
### Changed
### Fixed
 - Fixed commit slider so it works on bubble graph, removed reset button
 - Fixed bubble graph redraw overlap issue when commit slider is reduced.
 - Fixed npm parsing bug on absence of dependencies in a package.json
 - Fixed bug where repos by tech page didn't display

## 0.0.10 - 2024-11-04

### Added
  - a help section in the menu (available from /help/)
  - Intial start on header macro in jinja templates to make drilldown headings more repeatable
  - initial work on using panopticas for file type identification
  - added some static methods for generating and parsing repo_id in kospex_git
  - Implemented command on kospex CLI for feature request [kospex version command](https://github.com/kospex/kospex/issues/13)
  - Implemented [Krunner trufflehog capability to report only verified secrets](https://github.com/kospex/kospex/issues/10)

### Changed
  - Fixed how percentages and circles were created in summary view
  - added no_scc options to some commands



## VERSION - DATE

### Added
### Changed
### Fixed
