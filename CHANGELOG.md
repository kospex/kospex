# Changelog

The format of this changelog is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)

## Unreleased

### Upgrade notes

**No data changes are required in this release — unlike 0.1.0, nothing needs
re-syncing or normalising — but there are four schema changes, and one optional
clean-up.** An existing database needs `kospex upgrade-db -apply`; newly created
ones pick all four up automatically. The optional clean-up clears the stored
`''` described under *Fixed* for [#258](https://github.com/kospex/kospex/issues/258);
it is not needed for kospex to work, and
[`docs/refreshing-data.md`](docs/refreshing-data.md) has the SQL and the reason
the figures depend on `latest`.

- **`0007`** adds an index to `file_metadata`. Index only: no rows are read,
  rewritten or deleted, and a database left un-upgraded keeps working, just
  without the speed-up.
- **`0008`** adds the `osi_runs` table and its index, used by `krunner osi -next`
  for per-run diagnostics. A new empty table: nothing existing is touched, and a
  database left un-upgraded keeps working — `-next` logs that it could not record
  diagnostics and carries on scanning, because the scan is the product and the
  diagnostics are not.
- **`0009`** adds `is_dev`, `is_optional` and `runs_install_script` to
  `dependency_data`, for the lockfile install state described under *Added*
  below. Columns only, defaulting to NULL, which means *unknown* rather than
  false: nothing existing is rewritten, and a database left un-upgraded keeps
  working — the scanners record name and version as before and simply store no
  install state. Re-running `krunner osi` after the upgrade is what populates
  them for lockfiles already scanned.
- **`0010`** adds `finished_at` to `osi_runs`, because `started_at` was recording
  the finish (see *Fixed*). A column only, defaulting to NULL: nothing existing is
  touched, and a database left un-upgraded keeps working — `-next` logs that it
  could not record diagnostics and carries on scanning. Rows written before the
  upgrade keep a `started_at` that is really a finish time and are deliberately
  not backfilled, because `duration_ms` excludes time the machine spent asleep, so
  the true start cannot be recovered by subtraction. `DELETE FROM osi_runs` is the
  clean option if you would rather start the diagnostics over.

Otherwise what changes is how kospex *responds* when it cannot answer a
question. In four places it previously returned something that looked like an
answer; it now says it cannot. Each is a fix, but each can break a caller that
was relying on the old, wrong success.

1. **Three CLI commands now exit 1 for a repo that is not in the database.**
   `developers`, `tech-landscape` and `hotspot`, when scoped with **`-repo`**
   (`-repo_id` is unaffected), previously printed an empty result and exited 0
   for a repository kospex had never synced — indistinguishable from one that is
   synced and genuinely dormant. They now print what is wrong and exit 1.
   **A script that ran these and checked the exit code will now see a failure
   where it previously saw success.** That is the point — the old success was
   reporting "no developers" for repositories with recent commits — but a
   pipeline that treats any non-zero as fatal will stop where it used to
   continue. `tech-landscape` is the sharpest change: it previously printed an
   empty table with no message at all.

2. **`/dependencies/{id}` and `/osi/{id}` return 400 for an author email.**
   A base64-encoded author email in the path is a scope these pages cannot
   apply. They previously ignored it and rendered **every row in the table** —
   852 rows on a 111-repo estate, identical to no filter at all. Any bookmark,
   link or script hitting those URLs with an author id now gets a 400 instead of
   a page full of unfiltered data.

3. **`/collab/{repo_id}` returns 404 for an unknown or malformed id.**
   It previously rendered an empty table, which reads as "this repository has no
   collaborators". Both an unparseable id and a well-formed id for a repository
   not in the database now 404, with different messages.

4. **Routes now return their real status code instead of 500.** A route raising
   a deliberate 404 or 400 had it converted to a 500 by its own error handler.
   **If you alert on 5xx, expect that signal to drop** — some of what was
   reported as kospex failing was a malformed request. `/file-collab/` without a
   `file_path` is the clearest example: it returned 500 and now returns 400.

5. **`kospex sca` no longer saves by default, and its output and flags changed**
   ([#237](https://github.com/kospex/kospex/issues/237)). **A script calling
   `kospex sca FILE` that relied on it writing `dependency_data` must now pass
   `-save`** — and `-save` refuses (exit 1) unless the repository and its HEAD
   commit are synced and no tracked file is modified. `-dev` is gone, replaced
   by `-scope` (default `direct`, so dev dependencies are no longer shown by
   default). The `-out` CSV, which previously wrote nothing, now writes the
   table's columns: **no `authors` or `malware` column, and `days_ago` is a
   whole number**. `-malware` is accepted but ignored. Details under *Changed*.

6. **`kweb` exits with code 3 when it can't start**, for example when its port
   is already in use. uvicorn 0.50 made 3 its exit code for every startup
   failure, and kospex now requires uvicorn 0.52 or later. A service manager or
   script that checks for a particular non-zero code should accept 3.

### Added

- **Lockfile install state is recorded: `is_dev`, `is_optional`,
  `runs_install_script` (migration `0009`).** Lockfiles state per entry whether a
  dependency reaches production, whether it installs at all, and whether it
  **executes code at install time**. kospex read the name and version and discarded
  the rest. `requirements_type` cannot carry these — its `dev` value means
  "declared as a devDependency", and a transitive entry has no declaration of its
  own, so on one real repository 870 dev-only transitive packages were recorded
  identically to the 1169 production ones. `runs_install_script` is the notable
  one: it answers "which of these executes during install", which is what
  distinguishes a compromised package that ran from one merely present in a tree.

  **NULL means unknown, not false.** A manifest declares intent, not install state
  — `requirements.txt` cannot say whether a package runs an install script — and
  `lockfileVersion: 1` `package-lock.json` files record no flags at all. Claiming
  `false` there would assert something the file does not say. Same distinction
  `resolution` draws for the resolve layer and `last_checked` for staleness.

  Populated by the pnpm extractor today; named format-neutrally so the parsers in
  #229 land in the same columns rather than inventing their own.

  **`declared_scope` holds the raw string the file used**, verbatim, in that
  format's own vocabulary — `dev`, `optional,requiresBuild`,
  `snapshots:optional`, and later Maven's `provided` or a Python extra's name.
  The booleans are a projection of it. The column exists because every mapping
  error in building them happened at write time, where it is unrecoverable:
  pnpm's `requiresBuild` is not npm's `hasInstallScript` (it additionally covers
  native modules with no scripts), and an absent key means false in pnpm v6 but
  unknown in v9. With the declared string stored, a mapping that turns out wrong
  is a query to correct rather than a re-parse of every lockfile in an estate.
  `docs/dependency-flags.md` is the column's reference page, carrying each
  format's own definition, quoted and linked.

  **Install state is read per lockfile version, because where it lives is a
  version fact, not a pnpm fact.** v5/v6 put all three flags in `packages:` and
  omit a key when false, so absence there is a genuine no. v9 moved the resolved
  graph to a separate `snapshots:` section and records none of them in
  `packages:` — so v9 reads `optional` from `snapshots:` and leaves `is_dev` and
  `runs_install_script` NULL, dev-ness being derivable only by walking the graph
  from each importer's `devDependencies` and built packages not being tracked in
  the lockfile at all. A package counts as optional only if **every** resolution
  path to it is optional: peer-dependency suffixes put one `name@version` in
  `snapshots:` several times and the variants can disagree, and one required path
  means it installs.

- **`krunner osi -next N` — batched, cron-friendly dependency scanning.** `osi -all`
  is a single sweep that takes hours on a large estate, so it cannot run from a
  scheduler and is all-or-nothing when it fails. `-next N` processes the N
  repositories least recently examined, so a schedule of small runs converges on
  full coverage. Composes with a scope: `-next 10 github.com~acme`.

  Three things make it safe to schedule. It **exits 0 when nothing is due**, where
  `-all` exits 1 on "No results" and would report a failure on every tick once the
  estate is current. It takes a **run lock** and exits 0 if another run holds it,
  since overlap is normal under a short interval; a lock older than 6 hours is
  taken over, because one crashed run blocking the schedule forever is worse than
  a double scan. And it **writes no CSV by default** — `-csv` opts in, writing one
  `OSI-<repo_id>.csv` per repository to the assessments directory only, so a
  scheduled run cannot overwrite a full `OSI-all.csv` export.

- **`-max-seconds` bounds a batch by time as well as by count.** Measured per-repo
  cost spans four orders of magnitude — 2ms for a repository with no dependency
  files against 109s for a monorepo with 215 of them and 1101 packages — so a
  fixed `N` gives no predictable tick duration. The budget is checked *between*
  repositories: one in flight always finishes, because abandoning it would either
  save rows with no outcome recorded or record an outcome for work not done. A tick
  can therefore overshoot by the cost of one repository, sometimes greatly (a 10s
  budget produced a 109s tick), and the run says which repository did it.

- **Extraction outcomes are recorded ([#148](https://github.com/kospex/kospex/issues/148)).**
  A manifest producing zero dependency rows used to mean several different things
  with nothing recording which — most sharply `empty` (genuinely declares nothing,
  fine) against `parse_error` (the parser raised and was silently skipped, a
  defect). Six outcomes now distinguish them: `extracted`, `empty`, `unsupported`,
  `unclassified`, `not_a_manifest`, `parse_error`, modelled on
  `dependency_data.resolution`, which already does this one layer later. Stored
  per repository in `observations`, no schema change. Classifying the 12,666
  dependency-tagged files on a few-hundred-repo estate gives 85.6% `extracted`,
  9.1% `empty`, 3.0% `not_a_manifest` (`go.sum`, `dependabot.yml`, `Dockerfile`,
  runtime pins), 2.0% `unsupported`, 0.2% `unclassified` (`setup.py`, `setup.cfg`
  — [#137](https://github.com/kospex/kospex/issues/137)) and 0.06%
  `parse_error`, all eight of which are deliberately malformed test fixtures.
  Three formats remain `unsupported` after the parsers added in this release:
  `yarn.lock`, `build.gradle` and `pom.xml`. Separating `not_a_manifest` out
  makes the `unsupported` figure an honest parser backlog rather than an inflated
  one.

  The distribution was measured before the `uv.lock` parser below landed, so the
  24 `uv.lock` files it counts under `unsupported` now extract.

- **Per-run diagnostics in a new `osi_runs` table (migration `0008`).** One row
  per `(run_id, repository)` with duration, files, packages, deps.dev requests
  actually made, and outcome. The log lines cannot answer what only exists across
  runs: what `N` fits an interval, whether a repository is getting slower, whether
  the response cache is working, whether the schedule is completing passes.
  `observations` cannot hold it — its primary key includes `latest`, so it keeps
  one current row per key. `lookups` counts cache misses separately from
  `packages` written, which makes cache effectiveness measurable: a first real run
  shows 76% of babel's 1101 package lookups served from cache.

- **`package-lock.json` is now parsed** ([#229](https://github.com/kospex/kospex/issues/229)).
  The extractor registry has classified npm lockfiles since July 2026 without
  being able to read one, so a repository whose only manifest was a
  `package-lock.json` produced no dependency rows at all. All three lockfile
  versions are read: v3 and v2 from `packages:`, v1 from its nested
  `dependencies:` tree. A v2 file's duplicate `dependencies:` tree is ignored,
  since it describes the same closure.

  The four install-state columns from migration `0009` are populated —
  `is_dev`, `is_optional`, `runs_install_script` and `declared_scope` — so npm
  rows now answer the same questions as pnpm rows. Three npm-specific points
  are worth knowing before querying them:

  - **`devOptional` records `is_dev=1` with `is_optional` NULL.** It is npm's
    third state rather than the union of the other two: it never co-occurs with
    `dev` or `optional`, and means "reachable only via a dev path *or* an
    optional path" without saying which. The package does not reach production,
    but whether it may fail to install is genuinely unstated, so NULL.
  - **An npm lockfile kospex cannot read is reported as `parse_error`, not
    `empty`** (CWE-703). `empty` means "examined, nothing to find", so a file
    whose dependencies were never read must not claim it. An unsupported
    `lockfileVersion`, a missing one or malformed JSON now raise, and
    `lockfileVersion` itself is read liberally so `"3"` works as well as `3`.
    `kospex sca` previously called parsers with no guard and would have crashed
    on this; it now reports the file and continues, which is what the `krunner
    osi` path has done since #148. Genuinely empty lockfiles still report
    `empty`.
  - **An aliased dependency is recorded as the package it actually is.**
    `npm i foo@npm:bar` installs *bar* at `node_modules/foo`, and npm names the
    real package in a `name` field on that entry. Reading the install path
    instead would record a package that is not on the registry, so its deps.dev
    lookup returns nothing and the row shows no advisories for something that is
    genuinely installed. 30 entries on a reference estate are aliases. The alias
    is still matched when deciding `direct` / `dev`, since a project declares an
    aliased dependency by its alias. v1 encodes the same thing as
    `version: "npm:bar@1.2.3"`, which is parsed rather than stored verbatim.

    Because a lockfile is untrusted input and no field of it corroborates
    another (CWE-807), **every identity an entry asserts is recorded** — the
    install path, the `name`, and the package named in the `resolved` URL — with
    none treated as authoritative. They almost always agree, so this is one row
    per package in practice; across a reference estate it adds 32 rows out of
    13,739. A row named for an install-path alias may not be a real registry
    package and will carry no advisory data; the row under the real name is the
    one that answers the security question.
  - **A package reached by several install paths is reconciled, not
    overwritten.** `packages:` keys are install paths and the primary key has
    no path column, so the same `name@version` at two depths collapses to one
    row. `dev` / `optional` / `devOptional` are true only when *every* path
    agrees — one production path means the package ships — while
    `hasInstallScript` / `peer` / `inBundle` are true if any path says so.
  - **`lockfileVersion: 1` rows are all `transitive`.** v1's top level is the
    hoisted install set rather than the declared set, so a direct dependency
    cannot be told from a transitive lifted to the root. `is_dev` is still
    populated; `runs_install_script` is NULL, because v1 has no
    `hasInstallScript` field.

  Expect a larger first scan. On a reference estate this adds ~13,700 rows from
  ~7,400 distinct packages, around 5,900 of which have never been looked up, so
  the first sweep does that many deps.dev calls.

  `docs/dependency-flags.md` carries the measured per-key inventory and the
  per-version detail behind all of this.

- **`uv.lock` is now parsed** ([#229](https://github.com/kospex/kospex/issues/229)).
  Registered since July 2026 but unreadable, so every `uv.lock` reported
  `unsupported` — and it reaches more repositories than any other unread format,
  at one file per project. The full locked closure is read: **2,406 rows from 24
  files**, being 2,434 `[[package]]` entries less the 28 that are the project
  itself and its workspace members.

  `is_dev`, `is_optional`, `declared_scope` and `requirements_type` are populated
  by walking the dependency graph from the project, because `uv.lock` states
  install state nowhere per package — it is a graph, not a flag list. The flags
  were validated against uv's own output, keyed by `(name, version)`: all 2,406
  rows agree with what uv's runtime, `--all-extras` and `--all-groups` exports
  imply (388 runtime, 430 optional, 1,446 dev, 142 both).

  - **The format gates were measured against uv 0.11.11**, by running
    `uv export --frozen` over perturbed copies of a real lockfile rather than by
    reading a spec. uv refuses `version` ≠ 1, a non-integer `revision`, an empty
    file and UTF-16, and so does the parser. uv *reads* `revision = 99`, a
    missing `revision` and a UTF-8 BOM, and so does the parser. **This
    deliberately departs from the `package-lock.json` precedent** of refusing an
    unknown version field: here, refusing would be a false positive against the
    tool that owns the format.
  - **An unreadable `uv.lock` raises rather than returning `[]`** (CWE-703), so
    it reports `parse_error` and not `empty`. `[]` is reserved for a lockfile
    that genuinely locks nothing but the project itself.
  - **First-party entries produce no record.** `source = { editable }` and
    `{ virtual }` are the project and its workspace members, matching how pnpm
    treats local workspace directories. All 13 version-less entries on the
    estate are first-party, so no version fallback is needed.
  - **`declared_scope` over-lists on 15 of 2,406 rows.** The walk ignores
    environment markers, so it lists a group whose path uv prunes as impossible —
    a Python-version contradiction (13 rows) or a `conflicts` declaration (2). It
    never under-lists and never changes a flag. Documented rather than fixed:
    marker-aware walking needs an environment enumeration that could *under*-list,
    which is the worse failure.

  Git-sourced entries are recorded, since deps.dev answers by name and version
  rather than by source — a git fork resolves against the registry's release of
  the same version, so its advisories are upstream's. The source itself is not
  stored.

  `docs/dependency-details.md` carries the per-decision detail and the drift watch
  for what is deliberately not read.

### Changed

- **`kospex advisory-history` is disabled until its redesign**
  ([#282](https://github.com/kospex/kospex/issues/282)). It now exits 1 with a
  notice on stderr instead of running. It crashed with `IndexError` on the
  first scoped npm or Maven version (`@babel/core`,
  `org.apache.logging.log4j:log4j-core`), fetched every version rather than the
  "previous 10" its help promised, in deps.dev's alphabetical order rather than
  release order, and printed each version's raw deps.dev response. Its options
  are kept, so existing invocations get the notice rather than a usage error.
  The redesigned command — a window of recent versions ordered by publish date,
  a table, and `-json` — is planned for Q4 2026.
- **Dependency minimums raised.** Each minimum is now one of the three newest
  releases, or a release from the last three months: click 8.4.1, packaging
  26.1, python-dotenv 1.2.2 (clears GHSA-mf9w-mj56-hr94), requests 2.34.0,
  rich 14.3.3, fastapi 0.140.0 and uvicorn 0.52.0. click, requests, uvicorn,
  prettytable and PyYAML were exact pins and are now ranges. Tested at both
  the minimum and the newest version allowed.
- **`kospex sca` output, scope and saving reworked**
  ([#237](https://github.com/kospex/kospex/issues/237)). sca is an analyst's look
  at a dependency file, not a way to record work in progress, so it now behaves
  like one:
  - **Output.** The file path, one tab-separated line per package (name, declared
    version, release date), then a rich table, then one line naming the scope
    shown and the other scopes the file holds with their counts. The Python
    dict dump of every enriched record — printed twice for a file inside a git
    repository — is gone. Missing values show as `-`, not `None`.
  - **`-scope`** replaces `-dev`, which could not be turned off. A comma-separated
    list of `direct`, `dev`, `peer`, `optional`, `transitive`, `unknown` (no scope
    recorded) or `all`; default `direct`. It filters what is shown — the table,
    `-json` and `-out` — and never what is extracted or saved.
  - **`-json`** prints the table rows as JSON on stdout and nothing else;
    informational lines go to stderr, so `kospex sca -json FILE > sca.json` works.
  - **`-save` is opt-in, and guarded.** It was declared as an option with a value
    defaulting to on, so it could not be switched off. It is now a flag, and it
    refuses before any deps.dev lookup when the file is not in a git repository,
    the repository or its HEAD commit is not in the kospex DB, the worktree has
    modified or staged files, or the file itself is untracked. A saved row is
    keyed on the HEAD hash, so it must describe a commit kospex knows, read from a
    worktree that matches it. Untracked files elsewhere do not count.
  - **Columns.** `authors` is dropped: it counted authors of the package's source
    repo only when that repo happened to be synced locally, so it read 0 or `-`
    for nearly everything. The `kospex deps -file` table shares these columns and
    loses it too; `deps` still computes the value for its author ratio.
    `malware` is dropped: the lookup behind it is a deprecated service, and
    `-malware` now prints a notice and is ignored. `days_ago` is the whole
    number of days since the release of the version looked up — the floor of a
    range — and blank when there is none; it was a two-decimal float.
  - **`-out`** writes a CSV of the table. It was read into a variable that nothing
    used, so it never wrote a file.

- **The external response cache TTL is a day, not an hour, and is now
  configurable.** Every deps.dev and PyPI lookup goes through
  `url_request_with_status()`, whose TTL defaulted to 3600s; seven call sites rely
  on that default and none overrode it. An hour is enough for a single
  full-estate sweep, which finishes inside the window and so reuses any repeated
  lookups — but wrong for a batched sweep run from cron over
  hours or days, where every tick starts cold and re-fetches the same packages. A
  167-repo estate issues 6,387 lookups for 4,864 distinct package+version pairs,
  so 24% are repeats a cache can serve. That duplication is mostly *within*
  repositories rather than across them — 96% of distinct package+versions appear in
  exactly one repo, and the most widely shared appears in ten — so the saving comes
  from monorepos repeating declarations, not from an estate converging on common
  packages. The default is now **86400** (one day), overridable with
  `KOSPEX_URL_CACHE_SECONDS`. A day rather than a week because a cache hit means
  deps.dev was **not** contacted, so `dependency_data.last_checked` attests
  "evaluated" rather than "fetched" and the honest bound on advisory age is
  `last_checked + TTL`. An explicit `cache=` argument still wins, so a caller
  needing fresher data can ask.

- **`kospex_meta` removed from the schema definitions.** It was an abandoned
  first draft of `observations`, added 2024-07-27 and never executable: its
  `PRIMARY KEY` named columns the table did not declare, so running it raised
  `no such column: hash`. In 2.2 years it was never read, never written and never
  created in any database — `connect_or_create_kospex_db()` always omitted it.
  **No action needed:** no database has the table, and nothing referenced the
  constant. The practical effect is that `DB_CREATE_STATEMENTS` is now safe to
  iterate; all 18 statements execute.

- **`krunner osi` queries the database directly instead of copying the estate
  into RAM.** `load_dependency_memory_db()` copied `commit_files`,
  `file_metadata`, `repos` and `url_cache` into an in-memory database, and it ran
  *before* osi scoped to the requested repos — so `krunner osi <repo_id>` paid a
  whole-estate copy to look at one repo. Only two of the four tables were ever
  read: tracing the SQL gives 5 statements to `file_metadata`, 1 to `repos`, and
  zero to `commit_files` or `url_cache`. The copy carried no indexes either
  (`CREATE TABLE AS SELECT` does not reproduce the primary key), so every lookup
  was a full scan of RAM rather than a seek. On a 167-repo estate the read path
  goes **11.51s → 5.24s** from this change alone, and ~840MB of resident memory
  is no longer allocated. The other five `krunner` commands that build in-memory
  databases are untouched — they read `commits`/`commit_files` and genuinely use
  them.

- **`file_metadata` is now indexed on `(_repo_id, latest)` (migration `0007`).**
  Its only index was the primary key `(Provider, hash, _repo_id)`, led by the
  filename, so every repo-scoped query scanned the whole table — including the
  `UPDATE file_metadata SET LATEST = 0 WHERE _repo_id = ?` that runs on every
  sync. For a median-sized repo, `file_metadata(repo_id)` goes 25.11ms → 0.19ms
  at 131k rows and **8,506ms → 1.63ms at 8.05M rows**; the sync reset goes 294ms
  → 2.4ms at 2.04M rows. Combined with the change above, the osi read path over
  167 repos goes **11.51s → 0.40s**, returning the same 1081 dependency files.
  Cost: roughly 9% on bulk insert and 12.6% on the `file_metadata` table (8.5%
  on the real 2.0GB dev database). Estate-wide queries — `tech_landscape()` with
  no repo scope, `repos_with_tech()` — never constrain `_repo_id` and cannot use
  this index.

### Fixed

- **The package check page returned a 500 for a file it can't read.**
  Uploading a file whose name kospex doesn't recognise (`req.txt`,
  `dev-requirements.txt`, `Pipfile`), or one its parser can't read (invalid
  JSON), failed with `'NoneType' object is not iterable`. An unrecognised name
  is now a 400 and an unreadable file a 422, each with a message saying which.
  The page also showed every failure as `HTTP 500: Internal Server Error`
  because its error handler discarded the server's message; it now shows it.
  An unexpected error no longer sends its exception text to the browser
  (CWE-209).

- **`kospex sca -json` printed invalid JSON for a repo with an unparseable
  remote** ([#277](https://github.com/kospex/kospex/issues/277)). When
  `origin` was something the remote-URL parser can't read, such as a clone of a
  local path, `KospexGit.set_remote_url()` printed
  `WARNING: Failed specific parsing of remote URL` to **stdout**, ahead of the
  JSON array, so `json.load` / `jq` failed even with stderr discarded. The
  warning now goes to stderr and the `kospex_git` log, and names the URL with
  any embedded `user:password@` removed, so a token in a remote is not written
  to either (CWE-532).

- **An un-enriched dependency row counted as vulnerable**
  ([#258](https://github.com/kospex/kospex/issues/258)).
  `dependency_data.advisories` and `versions_behind` are `INTEGER` columns, and
  every record template seeded them with `''`. SQLite's type ordering sorts TEXT
  above every INTEGER regardless of content, so `'' > 0` is **true**: a package
  parsed from a manifest but never enriched matched `WHERE advisories > 0` — the
  predicate meaning "has a security advisory" — while being in none of `> 0`,
  `= 0` or `IS NULL`, the three buckets a reader would treat as exhaustive.

  The two layers then disagreed about the same row. SQL called it vulnerable,
  while the web UI's `item.get("advisories") or 0` coalesced the falsy `''` to 0
  and rendered it clean. Neither was right; the honest answer is unknown.

  `save_dependencies()` already carried a guard whose comment stated the intent
  exactly — *"None, not 0 — 'we did not ask' is not 'we asked and found none'"* —
  and it could never fire, because `setdefault` only fills a **missing** key and
  the templates always supplied one. All six copies of the record template now
  use `None`, and the save path coerces `''` away for any future caller that
  reintroduces it. A measured `0` still means "we asked and found none" and is
  left alone.

  **Existing rows do not self-heal.** A re-save supersedes rather than deletes,
  so most affected rows are at `latest = 0`, where no reporting query looks: on a
  several-hundred-repository estate 1,968 rows carried `''` and 1,962 of them
  were superseded, leaving 6 of 3,537 current reported-vulnerable rows wrong. The
  same estate reports 35% inflation if you drop the `latest = 1` filter, because
  that counts superseded history. Clearing them is a hand-run data change — see
  *Clearing unknown advisory counts* in `docs/refreshing-data.md`.

- **`osi_runs.started_at` recorded when a repository finished, not when it
  started** ([#259](https://github.com/kospex/kospex/issues/259)). The column was
  declared "UTC, when this repository began" and held the opposite:
  `record_repo_run()` defaulted it to now, and its only caller runs after
  extraction, enrichment, save and the outcome record are all done. On a
  whole-estate run the earliest `started_at` was 14m34s after the run's own
  "starting" log line, and that row's `duration_ms` was 874,159ms — exactly
  14m34s. Every row's start was its finish.

  `_process_repo` now stamps a wall-clock start alongside the existing monotonic
  one and passes it through, and **`started_at` no longer defaults to now** — a
  caller that does not know the start writes NULL, meaning unknown, rather than a
  confident wrong value. That default was the whole defect.

  Migration `0010` adds `finished_at`, because neither end of the window can be
  derived from the other: `duration_ms` comes from `time.monotonic()`, which does
  not advance while the machine is asleep, so `finished_at - duration_ms` is not
  the start and `started_at + duration_ms` is not the finish. On the run above,
  1h51m of a 5h34m wall clock was system sleep. One end plus a duration measures
  cost; two ends measure occupancy, and their difference is time the process was
  not running — otherwise recoverable only from operating-system power logs that
  rotate away. `ORDER BY started_at` also returned completion order, which is
  identical for a serial run and wrong as soon as anything is concurrent.

- **`kospex deps -file` crashed on any manifest with an unresolved package**
  ([#264](https://github.com/kospex/kospex/issues/264)). Since #106 an unresolved
  deps.dev lookup — an unpinned requirement, a range, an unknown package — has
  `versions_behind = None`, meaning "could not tell", and the summary compared it
  with `> 2`. Most real `requirements.txt` files hit it. The summary now reports
  `Resolved` and `Unresolved` counts, and takes the `Compliant` / `Non Compliant`
  percentages **over resolved packages only**, labelled `(of resolved)`, omitting
  them when nothing resolved. Previously an unresolved package could not be
  counted at all; counting it as compliant would have reported a file full of
  unpinned packages as nearly 100% compliant. What compliance should mean is
  left to #265. An unsupported or unparseable file also crashed the summary
  (`len(None)`); it now exits 1 after `assess()` has said why.

- **A package removed from a manifest stayed current after `kospex sca -save`**
  ([#151](https://github.com/kospex/kospex/issues/151)). `assess()` upserted
  rows at `latest=1` without demoting the ones they superseded, so a removed or
  renamed package was still reported as a current dependency. It now writes
  through `save_dependencies()`, the writer `krunner osi` uses, which demotes the
  whole file in the same transaction and stamps `source` (`kospex sca`). A
  whole-file demote is safe because extraction is always complete — `-scope`
  filters only what is displayed.

- **A file inside a linked git worktree or a submodule was attributed to the
  enclosing repository.** `find_git_base()` recognised only a `.git`
  *directory*; in a worktree or submodule `.git` is a file, so the walk carried
  on up to the outer checkout and `assess()` stamped rows with that checkout's
  repo id and HEAD. It now accepts either.

- **A `package.json` or `package-lock.json` beginning with a UTF-8 byte order
  mark was reported as unreadable, though npm reads it.** A BOM is what a
  Windows editor writes and is never content: verified against npm 11,
  `npm pkg get name` returns the name from a BOM-prefixed `package.json` and
  `npm ls --package-lock-only` resolves the locked version from a BOM-prefixed
  lockfile. Both are now read with `utf-8-sig`, which consumes a BOM when
  present and is identical to `utf-8` when it is not.

  Found by listing every file that reported `parse_error` after the fail-open
  fix below and reading them one at a time — eight of the nine were test
  fixtures the upstream projects deliberately committed as malformed, and the
  ninth was this. The contract did not cause the bug; it made it visible,
  because the file previously failed open as `empty`.

  Two formats keep refusing a BOM, because their own tooling does and the file
  genuinely cannot be built: `pyproject.toml`, where `pip install .` dies at
  `tomllib.loads`, and `go.mod`, whose parser has no BOM handling and whose
  `ModulePath` tests a `module` prefix that a BOM defeats. Both now **name the
  BOM** in the error. The `go.mod` check runs before the `module` gate, which
  had been reporting "no `module` directive, so not a go.mod" about a file
  whose first directive is `module` — the right outcome with a message that
  sent the reader after the wrong defect. `pnpm-lock.yaml` and `*.csproj`
  already tolerated a BOM, as the YAML and XML specs permit one, and
  `requirements.txt` has since the UTF-16 fix; all three now have regression
  tests. UTF-16 stays refused, as npm refuses it too.

- **A multi-document `pnpm-lock.yaml` read as zero dependencies.** When a
  project pins pnpm itself through `packageManagerDependencies`, pnpm writes
  the closure of the pnpm binary as one YAML document and the project's own
  closure as a second. `yaml.safe_load` accepts exactly one document and
  raises `ComposerError` on the stream, so the whole file produced nothing —
  and, before the fail-open fix below, was reported as `empty`. On a
  development estate **six lockfiles across three repositories held 7,337
  package entries between them**, all invisible; one was a 3,652-entry closure
  for a single repository.

  Read with `safe_load_all` and extracted **per document**, because
  classification is a per-document fact: pooling the stream's importers would
  resolve a direct dependency of the second document against the first
  document's importers, which do not mention it. Every document is validated
  before any is extracted, so an unusable second document cannot return the
  first one's packages as if the file had been read in full. Verified through
  the production extraction loop: one affected repository went from 27 rows to
  700, another from 6,466 to 11,022.

  **Re-run `krunner osi` to pick these up** — the rows do not exist until a
  scan writes them.

- **A manifest kospex could not read was recorded as one declaring nothing
  (CWE-392).** `classify_outcome` ends `return EXTRACTED if row_count else
  EMPTY`, and `parse_error` is reachable only when the parser **raises** —
  so raising is the only error channel an extractor has. Four parsers caught
  their own exceptions and returned `[]`, which arrives as `row_count == 0`
  with no error and is stored as the outcome `empty`, whose documented meaning
  is "parser ran, produced nothing, genuinely declares none". An unreadable
  `pnpm-lock.yaml`, `go.mod`, `.csproj` or `pyproject.toml` therefore made its
  repository look clean. Manifests come from cloned third-party repositories,
  so the input is attacker-influenced.

  Sharpest case: the `.csproj` DOCTYPE guard (CWE-776, uncontrolled entity
  expansion) refused a hostile file correctly and then returned `[]`, so **the
  security control's own outcome was indistinguishable from a clean scan**.

  All four now raise `UnreadableManifest`, and both scan paths already turn
  that into a `parse_error` against the one file. `[]` is kept only for a file
  that was read and genuinely declares nothing — a valid `go.mod` with no
  `require` (42 of 188 on a real estate), an empty `<Project></Project>` (4 of
  9), a pnpm lockfile whose only importer is empty. An unrecognised pnpm
  `lockfileVersion` now raises rather than reporting `empty`, which matters
  because v10 is released and the parser reads 5, 6 and 9.

  Two gates were added where a format had none, each verified against every
  such file on a real estate before being added: a `go.mod` must declare
  `module` (188 of 188 do), and a `.csproj` must have a `<Project>` root (9 of
  9 do). `.csproj` `PackageReference` lookup is now namespace-agnostic, so the
  root gate does not open a fresh silent-empty path for a legal
  namespace-qualified project file.

  The contract is now **driven from the extractor registry** — a test resolves
  every registered parser and asserts none returns a value for a path it
  cannot read. That test is what found the `pyproject.toml` case, which no
  one had looked for. Of 357 real manifests parsed before and after,
  **349 are byte-identical**; no schema change and no stored data rewritten.

  The `pyproject.toml` change **reverses
  [#97](https://github.com/kospex/kospex/issues/97)**, where the swallow was
  added because a malformed TOML fixture aborted an entire `krunner osi` run.
  Both scan paths have since gained broad per-file guards, so the parser no
  longer has to swallow anything for the batch to survive; the extraction loop
  was re-run over that same repository to confirm it completes, with the
  malformed file as the one `parse_error`. #97's other half — skipping a
  single invalid requirement *string* inside a file that was read — is
  unchanged.

- **`peerDependencies` were recorded as `direct`, and extras as nothing at all.**
  Two scan paths write `package_use`, and each kept its own copy of the
  `requirements_type -> package_use` mapping. The copies agreed on every value
  they both mapped and diverged on the one neither did: `parse_package_json`
  emits `peer` and `optional` and `parse_pyproject_file` emits `optional`, none
  of which had an entry, so the **fallback** decided what was stored --
  `direct` on the `sca` side, `""` on the `osi` side, for the same file. On a
  populated database **497 rows** carried `package_use = ''` (185 from
  `package.json`, 312 from `pyproject.toml`), and `kospex sca` recorded peer
  dependencies as `direct`, which asserts the opposite of what a
  `peerDependency` declares -- that the consumer supplies it, not this package.
  `PACKAGE_USE_PEER` and `PACKAGE_USE_OPTIONAL` existed in `PACKAGE_USE_VALUES`
  and were unreachable.

  There is now **one** mapping, `KospexSchema.REQ_TO_PACKAGE_USE`, imported by
  both paths as the same object rather than copied -- the old comment asking the
  two tables to "mirror" each other was a request, not a mechanism. An unmapped
  value now stores **NULL**, following migration `0009`'s rule that NULL means
  *unknown*: `direct` claimed the manifest said something it did not, and `""`
  is indistinguishable from a parser that produced no value, which is how this
  stayed invisible. A contract test reads the parser sources and fails if any
  parser emits a `requirements_type` with no mapping, because the original
  defect was a *missing* mapping and nothing failed while it was missing.

  **Existing rows keep their old values until the estate is re-scanned.**
  `package_use` is currently written by both scan paths and read by none, so
  this corrects stored data rather than anything displayed.

- **A UTF-16 `requirements.txt` lost its first dependency.**
  `detect_encoding` inspects the leading bytes for a byte-order mark and returns
  an encoding. For UTF-8 it returned `utf-8-sig`, which consumes the BOM; for
  UTF-16 it returned `utf-16-le` / `utf-16-be`, which do **not**, so the mark
  survived decoding and the first declaration became
  `"\ufeffrequests==2.31.0"` — not a valid PEP 508 requirement, and dropped.
  The function existed to handle BOMs and got two of its three cases wrong. Now
  returns bare `utf-16`, which reads the mark, picks the byte order from it, and
  consumes it. Only the first line was affected, which is why it was quiet.

- **A malformed `pyproject.toml` invented dependencies that do not exist.**
  PEP 621 declares `dependencies` and each `optional-dependencies` group as
  arrays. A string is also iterable, so `dependencies = "requests"` was iterated
  *character by character* and produced eight rows named `r`, `e`, `q`, `u`,
  `e`, `s`, `t`, `s` — written to `dependency_data` and looked up on deps.dev.

  Not a silent miss but fabrication: `e`, `q`, `u`, `s`, `t` and `p` are all
  real PyPI packages, so a malformed manifest made kospex report a repository as
  depending on packages it has nothing to do with, with those packages' real
  advisories attached. `package_name` is part of the primary key, so the rows
  persisted. A TOML table is now rejected as well — iterating one yields its
  keys, which happened to give the right answer and hid the shape of the file.
  Both now raise, which the scan paths record as a `parse_error`.

- **A `package.json` whose top level was not a JSON object reported no
  dependencies.** A file containing a JSON array or string returned zero records
  rather than raising, because `"dependencies" in ["x"]` is a legitimate
  membership test that answers `False`; a file containing a number raised
  `TypeError`. All three are valid JSON, so the load could not catch them, and
  npm rejects such a file outright — so zero records read as "this repository
  declares no npm dependencies" for a file kospex could not parse. It now raises,
  which both scan paths record as a `parse_error` outcome.

- **`pyproject.toml` extras group names were read and discarded.**
  `for group, deps in optional.items()` never used `group`, so every entry under
  `[project.optional-dependencies]` produced an identical row -- 129 distinct
  group names across 432 declared dependencies in one estate collapsed to one
  value. Both manifest parsers now populate `declared_scope` (migration `0009`)
  with the raw section name in the file format's own vocabulary:
  `dependencies` / `devDependencies` / `peerDependencies` /
  `optionalDependencies` for npm, and the extras group name for Python.

  A `dev` extras group is still recorded as `optional`, deliberately. PEP 621
  extras mean "installed only when requested", which is not npm's
  `devDependencies`, and `check` / `cover` / `type` would be guesses. The name
  is stored as evidence so the classification is a later query, not a re-scan.

- **`kospex sca` recorded no advisory data for pnpm transitive dependencies.** One
  condition in `_enrich_dependency_records` skipped deps.dev entirely when a
  `pnpm-lock.yaml` entry was neither `direct` nor `dev`. On a real repository that
  left **1991 of 2079 packages (96%)** with no `advisories` and no
  `versions_behind` — concentrated in the layer where supply-chain compromise
  actually lands, since transitives are where malware hides precisely because
  nobody inspects them. It was also an `osi`/`sca` divergence: `krunner osi` never
  skipped, so the same manifest produced different advisory data depending on which
  tool read it. The skip is removed; every entry is looked up, and transitives stay
  labelled `transitive` so a consumer can still tell a declared dependency from one
  pulled in beneath it. The cost that motivated the skip is handled where it
  belongs rather than by not looking — `depsdev_record` already short-circuits a
  version it cannot resolve, so a `workspace:*` entry still costs no round trip.

- **Stale advisory counts survived an un-enriched re-save, under a fresh
  `last_checked`.** `save_dependencies()` never calls deps.dev, so it resets
  `resolved_version` to `""` for a caller that did not enrich — otherwise a
  resolved version from a lookup that did not happen this time would sit under a
  freshly advanced timestamp. `advisories` and `versions_behind` were not reset
  the same way, so the *previous* run's counts carried over instead, which a
  reader cannot tell apart from counts confirmed a moment ago. Not reachable
  through `krunner osi`, whose only call site enriches immediately before
  saving; it becomes reachable the moment extraction and enrichment are
  decoupled, such as a batched run that parses manifests estate-wide but
  enriches only the current batch. Both columns are now reset to `NULL` — not
  `0`, since "we did not ask" is not "we asked and found none".

- **A repo kospex has never synced reported as having no developers.**
  `kospex developers -repo .` on an unsynced repository printed *"No active
  developers found in the kospex DB"* and exited 0 — byte-identical to a synced
  repository that is genuinely dormant. The reported case had 137 commits, the
  most recent that week. For a tool whose purpose is identifying unmaintained
  code, a false "unmaintained" is the most damaging answer available, and the
  wrong reading is the natural one. `developers`, `tech-landscape` and `hotspot`
  now say which situation it is and **exit 1**, so scripted callers can branch.
  `tech-landscape` previously rendered an empty table with no message at all.
  Three states are now distinguished: not in the database, in the database with
  no commits recorded (an incomplete sync), and genuinely dormant.

- **Sync suggestions named a command that does not exist, and then the wrong
  one.** `hotspot`'s out-of-sync message said to run `kospex sync <path>` — that
  command is commented out and parked (#123), so following it produced a usage
  error. The suggestion now matches the situation, since the three real
  commands do different things: `kospex sync-directory <path>` syncs what is on
  disk with no network, `kgit sync <url>` clones **and** syncs, and `kgit pull
  <repo_id>` refreshes a clone kospex **already knows about**. A repo absent
  from the database is told to `sync-directory` (`kgit pull` cannot help — it
  only refreshes known repos); a repo that is known but stale is told to `kgit
  pull`, with `sync-directory` offered as the no-network alternative.

- **`/dependencies/` and `/osi/` returned every row when the URL segment was a
  base64 author email.** Both hand-rolled the scope cascade, and the fallback
  branch printed an error then fell through with no `WHERE` clause, so the query
  ran unscoped — 852 rows on a 111-repo database, identical to no scope at all.
  A scope the query cannot honour is now refused, and the routes return **400**
  rather than rendering the whole table. "No scope requested" still means all
  scope, which `krunner` relies on.

- **`/collab/` rendered an empty table for an id that was never valid.** The id
  was passed straight to the query with no validation, so an unparseable id and
  a repository with no collaborators looked identical. It now returns **404**,
  with different messages for "not a repo_id" and "not in the kospex database".

- **Web routes converted their own `4xx` into a `500`.** A route raising
  `HTTPException` inside a `try` whose `except Exception` maps everything to a
  500 reported its own client error as a server error — `HTTPException` is an
  `Exception`. `/file-collab/` was doing this in production: its deliberate
  "file_path parameter is required" 400 reached clients as a 500. **All 42
  handlers that convert to a 500 now re-raise `HTTPException` first.** Two
  handlers that *recover* rather than convert — the 404 template fallback and
  the supply-chain search form — are deliberately left alone, since re-raising
  there would turn a handled error into a propagated one.

- **`pom.xml` was reported as `unclassified` rather than `unsupported`
  ([#221](https://github.com/kospex/kospex/issues/221)).** It had no extractor
  registry entry, so a Maven manifest looked like a file kospex had never heard
  of instead of one it recognises but cannot yet read. It is now registered as
  a `maven` package manifest with no parser, alongside `build.gradle`. Nothing
  is extracted from it yet — this only moves it into the parser backlog that
  `unsupported` is meant to count.

## 0.1.0 - 2026-09-11

### Upgrade notes

**Reported numbers change in this release, in ten ways.** Anything already
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
7. **npm projects report more dependencies from `kospex sca` / `deps`, and npm
   `package_version` changes for caret/tilde ranges.** `devDependencies` are now
   always extracted and tagged `package_use = 'dev'` — `-dev` controls only what
   the printed table shows, not what is recorded — so npm counts rise for anyone
   who was not passing the flag. Filter on `package_use = 'direct'` to compare
   like with like. Separately, `sca`/`deps` stored `express` as `4.18.0` where the
   manifest declared `^4.18.0` and `krunner osi` stored the declared text; both
   now keep the declared text, so the same dependency scanned either way is one
   row rather than two. `package_version` is in the primary key, so previously
   written rows do not collide and are demoted by the next `osi` run. Finally,
   `requirements.txt` and `pyproject.toml` rows gain `package_use = 'direct'`
   where they previously stored NULL.
8. **Python requirements rows show their operator.** `requirements.txt` was the
   only parser that split the operator out of `package_version`, so `flask>=2.0`
   stored `2.0` and looked pinned. It now stores `>=2.0`, matching
   `pyproject.toml`, `package.json`, `go.mod` and `.csproj`. About 317 rows
   change; `package_version` is in the primary key, so the old rows are
   superseded rather than updated and demoted by the next `krunner osi` run.
   Anything reading that column verbatim will show the operator — which is
   accurate, and what `version_kind` now lets you filter on instead. A
   declaration written with spaces around the operator (`tox ~= 4.4`) has them
   removed, so it stores the same `~=4.4` as `tox~=4.4` and as the
   `pyproject.toml` rendering of the same requirement — one constraint is one
   value, which matters because `package_version` is part of the primary key.
   The exception is a **compound** range (`packaging >=23.0, <24.3`), which
   keeps its declared text and so can still differ from the `pyproject.toml`
   form; the author's ordering carries information that normalising discards.
   `version_kind` and `version_operator` agree either way.

9. **Azure DevOps `repo_id`s change, and become fewer per repository.** The
   organisation and project were joined with a hyphen — `dev.azure.com~myorg-MyProject~MyRepo`.
   Hyphens are legal in ADO organisation names, so
   `my-org/Project` and `my/org-Project` produced the **same** id and merged two
   different repositories. They are now encoded as a hierarchy,
   `dev.azure.com~myorg~~MyProject~MyRepo`, using the same `~~` convention as a
   GitLab subgroup; `/` cannot appear in an ADO organisation, project or repo
   name, so it cannot collide. Separately, ADO's SSH URL
   (`git@ssh.dev.azure.com:v3/...`) and collection URLs
   (`.../DefaultCollection/...`) were not recognised as ADO at all, so a single
   repository could hold **five** different ids depending on which clone URL was
   used; it now holds two, differing only by legacy `*.visualstudio.com` versus
   `dev.azure.com`, which are deliberately kept distinct. Any ADO repository
   already synced needs re-syncing to pick up the new id — see the re-sync
   procedure below. Verified against the live database: **0 of 111** repos are
   affected, all being `github.com`.

10. **`repo_id` is now lowercased, and so are `_git_owner` / `_git_repo`.**
   Providers treat an owner and repository name as case-insensitive for
   uniqueness, so `github.com/Kospex/Kospex` and `github.com/kospex/kospex` are
   one repository — but kospex minted two ids for them, splitting commit history
   and double-counting the org. The columns are lowercased with the id because
   they are written from the parsed clone URL rather than derived from it, and
   the org-scoped queries bind `_git_owner` from a split `org_key`; if the two
   disagree on case, every org lookup starting from a `repo_id` silently returns
   nothing. **Existing rows are not changed** — there is no migration, because
   two ids that collapse to one need a decision about which row to keep. The
   check-and-normalise SQL is documented at
   **[Refreshing data → Normalising repo_id case](https://docs.kospex.io/refreshing-data#normalising-repo_id-case)**.
   Measured on a 111-repo estate: 14 repos affected, no collisions. Display
   casing is lost — `_git_owner` held the provider's canonical form and no
   longer does.

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

- **Dependency constraints are now recorded.** Four columns on
  `dependency_data` (migration `0006`): `version_kind` classifies how a
  dependency is constrained (pinned / commit / caret / tilde / gte / bounded /
  excluded / latest / workspace / link / catalog / alias / patch / none),
  `version_operator` keeps the raw declared operator, `resolved_version`
  records the version deps.dev was actually asked about, and `last_checked`
  records when. Previously the classification was computed and discarded, so
  "which of our dependencies float?" could not be asked of the database, and an
  advisory count could not be read without knowing which version it referred to
  or how old it was. `pinned` means exactly one version, nothing can drift —
  a statement about what a declaration permits, not what it looks like, so
  `1.x`, `==1.*` and `1.2.3 - 2.3.4` are `bounded` despite starting with a
  digit, and `!=21.1.0` is `excluded` because it permits every version except
  one. `tilde` is deliberately separate from `caret` — `~29.0.0` permits patch
  drift where `^4.18.0` permits minor and patch — and `commit` is separate
  from `pinned`, since a Go pseudo-version points at a commit with no
  published release to match against. Columns are NULL on existing rows
  until re-sync; a staleness indicator must treat NULL as "never checked", not
  "checked long ago". See `changes/202609-version-constraint-column.md`.

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
- **Retired the five per-ecosystem `*_assess()` functions.** `assess()` moved to
  registry dispatch, leaving `npm_assess`, `pypi_assess`, `pypi_assess2`,
  `gomod_assess` and `nuget_assess` with no callers in `src/` — only tests kept
  them alive. Removing them, plus the two helpers they orphaned
  (`get_npm_dependency_dict`, `write_csv`), takes 341 lines out of
  `kospex_dependencies.py` (1816 → 1488) and deletes a third `upsert_all`: a
  database write path nothing could reach. Unreachability was confirmed three
  ways before deleting — no non-definition references in `src/`, no registry
  `parse_ref` pointing at any of them, and no dynamic dispatch that could reach
  them. The four test files that called them directly now exercise `assess()`,
  so the behaviour they cover (#177, #178, #107, #108) is still asserted against
  the path that runs. No behaviour change. See
  `changes/202608-retire-dead-assess-functions.md`.
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

- **`parse_repo_id()` and `parse_org_key()` rejected every nested organisation.**
  Both split on each `~` and required an exact part count, so an id whose org
  contains a `/` — a GitLab group and subgroup, or an ADO organisation and
  project, encoded as `~~` — returned `None`. Because `kweb2` raises **HTTP 404
  "Repository not found"** when `parse_repo_id` returns `None`, a *synced* GitLab
  subgroup repository was unreachable in the web UI and got no organisation link.
  Both now decode the org correctly. `org` is returned with a real `/`, matching
  the `_git_owner` column; `org_key` stays encoded, because it is a URL path
  segment (`/org/{org_key}`) and a `/` would split the route in two. Each
  rejects the other's shape: a `~` left after decoding `~~` separates an org from
  a repo, so `parse_org_key` refuses every `repo_id` and `parse_repo_id` refuses
  a nested `org_key`. That keeps the `{id}` pages — `/tenure/`, `/landscape/`,
  `/dependencies/`, `/osi/` and others — scoped to the repository rather than to
  an organisation that does not exist.

- **`org_key` disagreed between the SQL and the parser.** Three queries built it
  as `_git_server || '~' || _git_owner`, yielding `gitlab.com~group/subgroup`,
  while the parser produced `gitlab.com~group~~subgroup`. Anything joining across
  the two returned nothing — silently, since an empty join is a valid result. The
  queries now encode to match. GitHub ids are unaffected: for a flat org the two
  forms are the same string, which is why this never surfaced.

- **Azure DevOps repo names ending in `.`, `g`, `i` or `t` were truncated.**
  `rstrip(".git")` takes a *character set*, not a suffix, so a repository named
  `digit` became `d`. It ran before both the `dev.azure.com` and
  `*.visualstudio.com` branches, so both were affected. Now `removesuffix`.

- **A trailing slash made a valid clone URL unparseable.** The parsers allowed a
  trailing slash inconsistently — one in the generic rule, any number in the ADO
  and Bitbucket Server rules, none at all in the scp-style rule — so
  `https://github.com/acme/svc/` and `git@github.com:acme/svc.git/` returned
  `None`. Callers compensated with their own `rstrip("/")`, but the sync path did
  not, so a repository cloned with a trailing slash cloned successfully and then
  failed to parse on sync. Trailing slashes are now normalised once, before any
  rule sees the URL.

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
- **Every Go dependency lookup failed, so Go rows carried no advisory data.**
  `clean_version_spec()` strips a leading `v`, which is right for npm and pypi
  where it is decoration and wrong for Go where it is part of the canonical
  module version — deps.dev returns 200 for `v0.9.1` and 404 for `0.9.1`. Both
  scan paths normalised every ecosystem through it, so Go rows were written with
  no `advisories`, no `versions_behind` and a misleading `resolution` of
  `package_not_found`. Wrong data rather than missing data, with nothing to
  indicate a failure. The helper now takes the `package_type` and preserves the
  prefix for ecosystems where it is canonical. This arrived through the
  deduplication work itself: `gomod_assess()` passed the raw version and was
  correct, and consolidating the five enrichment paths onto one shared path
  propagated the npm/pypi behaviour to Go. Any Go row written since the `krunner
  osi` go.mod support needs a re-sync to be corrected. See
  `changes/202608-go-version-v-prefix.md`.
- **`kospex sca -dev` did nothing, and `sca` and `deps` disagreed on the same
  file.** `sca` builds its kwargs with `params = locals()`, producing `dev`,
  while `assess()` read `dev_deps` — so the flag never arrived and dev
  dependencies were silently dropped, despite defaulting to on and being
  documented as "Include dev/test dependencies". `deps` set `dev_deps`
  correctly, so the two commands returned different results for identical input.
  Extraction is now unconditional and complete: dev dependencies are always
  recorded and tagged `package_use = 'dev'`, with the flag controlling only what
  the printed table shows. A partial result was also what blocked a whole-file
  demote in `assess()` (#151). Closes #181.
- **`assess()` reached six hand-rolled branches to pick a parser, where `krunner
  osi` used the registry.** `kospex sca` / `kospex deps` now dispatch through
  `classify()` and `resolve_parser()` like `osi` does, with one shared enrichment
  path replacing five per-ecosystem ones. That removes three divergences: npm
  rows stored a stripped `package_version` where `osi` stored the declared text
  (so the same dependency scanned both ways produced two rows), `requirements.txt`
  and `pyproject.toml` rows never set `package_use`, and NuGet reached deps.dev
  as `"NuGet"` against the registry's `"nuget"`. Advances #180. See
  `changes/202608-assess-registry-dispatch-c2a.md`.
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

### Security

- **Bumped `click` 8.3.1 → 8.3.3** for CVE-2026-7246 (GHSA-47fr-3ffg-hgmw): command
  injection through the `filename` argument of `click.edit()`, which launches the editor
  through a shell (CWE-78). kospex does not call `click.edit()`, and neither does any of its
  installed dependencies, so kospex itself was not exploitable. The bump still matters: kospex
  pins `click` exactly, so every install got the vulnerable version and could not upgrade it
  on its own.

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
