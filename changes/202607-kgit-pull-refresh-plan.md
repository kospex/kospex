# `kgit pull` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `kgit pull [REPO_ID] --all/--org/--server` to refresh the local clones kospex already knows about (git pull --ff-only + kospex sync), plus an offline `--check` staleness view, tracking `repos.last_fetch`.

**Architecture:** A new `pull` Click command in `src/kgit.py`. Scope (repo_id / org / server / all) resolves to repo rows via `KospexQuery.get_repos`. Refresh mode pulls each clone via `subprocess` (ff-only), stamps `last_fetch`, and calls `kospex.sync_repo` (the file_metadata guard makes unchanged repos cheap). `--check` mode formats staleness from the DB with no network. A migration adds `repos.last_fetch`.

**Tech Stack:** Python 3.12, Click, sqlite_utils, subprocess/git, pytest.

**Spec:** `changes/202607-kgit-pull-refresh.md`. Run tests from the worktree with `PYTHONPATH=<worktree>/src` (editable install points at the main dir).

---

## File structure

- `src/kospex/db/migrations/0004_repos_last_fetch.sql` — new: add `repos.last_fetch`.
- `src/kospex_query.py` — new method `set_repo_last_fetch(repo_id, when=None)`.
- `src/kgit.py` — new `pull` command + pure helpers `_resolve_pull_repos`, `_staleness_rows`, `_pull_command`, and `_git_pull`; stamp `last_fetch` in the existing `clone` command.
- `tests/test_kgit_pull.py` — unit tests for the pure helpers.
- `tests/test_kgit_pull_sync.py` — end-to-end CLI test (marked `integration`).
- `tests/test_db_migrator.py` — extend with the shipped-0004 test.

Conventions from the codebase: `kgit.py` already has module globals `kgit = KospexGit()`, `kospex = Kospex()`, `console = Console()`, `log = KospexUtils.get_kospex_logger('kgit')`, and a `@click.group() cli`. Tests import flat modules (`from kgit import ...`); the shared `tests/conftest.py` isolates `KOSPEX_*` env + the HabitatConfig singleton per test.

---

## Task 1: Migration 0004 — `repos.last_fetch`

**Files:**
- Create: `src/kospex/db/migrations/0004_repos_last_fetch.sql`
- Test: `tests/test_db_migrator.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_db_migrator.py`:

```python
def test_shipped_0004_adds_repos_last_fetch(tmp_path):
    """The shipped 0004 migration adds repos.last_fetch."""
    import sqlite_utils
    import kospex_schema as KospexSchema
    from kospex.db.migrator import Migrator

    db = sqlite_utils.Database(tmp_path / "kospex.db")
    db.execute(KospexSchema.SQL_CREATE_REPOS)
    db.execute(
        "CREATE TABLE schema_migrations ("
        "id TEXT PRIMARY KEY, sequence INTEGER NOT NULL, checksum TEXT NOT NULL, "
        "applied_at TEXT NOT NULL, duration_ms INTEGER, has_python INTEGER NOT NULL)"
    )

    Migrator(db).apply_pending()  # default dir = shipped migrations (incl 0003, 0004)

    cols = {r[1] for r in db.execute("PRAGMA table_info(repos)")}
    assert "last_fetch" in cols
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=$PWD/src python -m pytest tests/test_db_migrator.py::test_shipped_0004_adds_repos_last_fetch -v`
Expected: FAIL — `assert 'last_fetch' in {...}` (column absent, 0004 doesn't exist yet).

- [ ] **Step 3: Create the migration**

Create `src/kospex/db/migrations/0004_repos_last_fetch.sql`:

```sql
-- 0004_repos_last_fetch.sql
--
-- Track when kospex last refreshed a repo's local clone from its remote
-- (git clone or git pull). Distinct from last_sync (when kospex last read the
-- clone into the DB) and last_seen (the newest commit date). NULL = never
-- recorded; surfaced as "never" in `kgit pull --check`.

ALTER TABLE repos ADD COLUMN last_fetch TEXT;
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=$PWD/src python -m pytest tests/test_db_migrator.py::test_shipped_0004_adds_repos_last_fetch -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kospex/db/migrations/0004_repos_last_fetch.sql tests/test_db_migrator.py
git commit -m "feat(db): 0004 add repos.last_fetch"
```

---

## Task 2: `KospexQuery.set_repo_last_fetch`

**Files:**
- Modify: `src/kospex_query.py` (add method near `latest_commit_file_map`)
- Test: `tests/test_kgit_pull.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_kgit_pull.py`:

```python
"""Unit tests for kgit pull helpers and provenance."""
import sqlite_utils

import kospex_schema as KospexSchema
from kospex_query import KospexQuery


def _repos_db(rows):
    db = sqlite_utils.Database(memory=True)
    db.execute(KospexSchema.SQL_CREATE_REPOS)
    db.execute("ALTER TABLE repos ADD COLUMN last_fetch TEXT")
    if rows:
        db["repos"].insert_all(rows, pk="_repo_id")
    return db


def test_set_repo_last_fetch_stamps_timestamp():
    db = _repos_db([{"_repo_id": "s~o~r", "_git_server": "s", "_git_owner": "o",
                     "_git_repo": "r", "file_path": "/tmp/r"}])
    kq = KospexQuery(kospex_db=db)

    kq.set_repo_last_fetch("s~o~r", when="2026-07-09T00:00:00+00:00")

    row = next(db.query("SELECT last_fetch FROM repos WHERE _repo_id='s~o~r'"))
    assert row["last_fetch"] == "2026-07-09T00:00:00+00:00"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=$PWD/src python -m pytest tests/test_kgit_pull.py::test_set_repo_last_fetch_stamps_timestamp -v`
Expected: FAIL — `AttributeError: 'KospexQuery' object has no attribute 'set_repo_last_fetch'`.

- [ ] **Step 3: Implement the method**

Add to `src/kospex_query.py` (after `latest_commit_file_map`):

```python
    def set_repo_last_fetch(self, repo_id, when=None):
        """Record when a repo's local clone was last refreshed from its remote.

        when: ISO timestamp string; defaults to now (local tz, second precision).
        """
        if when is None:
            when = datetime.now(timezone.utc).astimezone().replace(
                microsecond=0).isoformat()
        self.kospex_db.execute(
            f"UPDATE {KospexSchema.TBL_REPOS} SET last_fetch = ? WHERE _repo_id = ?",
            [when, repo_id],
        )
        self.kospex_db.conn.commit()
        return when
```

Ensure `from datetime import datetime, timezone` is imported at the top of `kospex_query.py` (add if missing).

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=$PWD/src python -m pytest tests/test_kgit_pull.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kospex_query.py tests/test_kgit_pull.py
git commit -m "feat(query): set_repo_last_fetch to stamp clone refresh time"
```

---

## Task 3: Scope resolver `_resolve_pull_repos`

Validates exactly one scope was given and returns the in-scope repo rows.

**Files:**
- Modify: `src/kgit.py` (add module-level helper)
- Test: `tests/test_kgit_pull.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_kgit_pull.py`:

```python
import pytest
from kgit import _resolve_pull_repos


def _seed_repos():
    db = _repos_db([
        {"_repo_id": "github.com~o1~a", "_git_server": "github.com",
         "_git_owner": "o1", "_git_repo": "a", "file_path": "/c/a"},
        {"_repo_id": "github.com~o1~b", "_git_server": "github.com",
         "_git_owner": "o1", "_git_repo": "b", "file_path": "/c/b"},
        {"_repo_id": "bitbucket.org~o2~c", "_git_server": "bitbucket.org",
         "_git_owner": "o2", "_git_repo": "c", "file_path": "/c/c"},
    ])
    return KospexQuery(kospex_db=db)


def test_resolve_by_repo_id():
    kq = _seed_repos()
    repos = _resolve_pull_repos(kq, repo_id="github.com~o1~a",
                                all_flag=False, org=None, server=None)
    assert [r["_repo_id"] for r in repos] == ["github.com~o1~a"]


def test_resolve_by_org():
    kq = _seed_repos()
    repos = _resolve_pull_repos(kq, repo_id=None, all_flag=False,
                                org="github.com~o1", server=None)
    assert sorted(r["_repo_id"] for r in repos) == ["github.com~o1~a", "github.com~o1~b"]


def test_resolve_by_server():
    kq = _seed_repos()
    repos = _resolve_pull_repos(kq, repo_id=None, all_flag=False,
                                org=None, server="bitbucket.org")
    assert [r["_repo_id"] for r in repos] == ["bitbucket.org~o2~c"]


def test_resolve_all():
    kq = _seed_repos()
    repos = _resolve_pull_repos(kq, repo_id=None, all_flag=True,
                                org=None, server=None)
    assert len(repos) == 3


def test_resolve_requires_exactly_one_scope():
    kq = _seed_repos()
    with pytest.raises(ValueError):
        _resolve_pull_repos(kq, repo_id=None, all_flag=False, org=None, server=None)
    with pytest.raises(ValueError):
        _resolve_pull_repos(kq, repo_id="x~y~z", all_flag=True, org=None, server=None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=$PWD/src python -m pytest tests/test_kgit_pull.py -k resolve -v`
Expected: FAIL — `ImportError: cannot import name '_resolve_pull_repos' from 'kgit'`.

- [ ] **Step 3: Implement the helper**

Add to `src/kgit.py` (module level, after the globals):

```python
def _resolve_pull_repos(kospex_query, repo_id=None, all_flag=False, org=None, server=None):
    """Resolve the in-scope repo rows for `kgit pull`.

    Exactly one of repo_id / all_flag / org / server must be given.
    Returns the list of repo rows from the DB.
    """
    scopes = [bool(repo_id), bool(all_flag), bool(org), bool(server)]
    if sum(scopes) != 1:
        raise ValueError(
            "Specify exactly one scope: a REPO_ID, or one of --all / --org / --server."
        )
    if all_flag:
        return kospex_query.get_repos()
    if repo_id:
        return kospex_query.get_repos(repo_id=repo_id)
    if org:
        return kospex_query.get_repos(org_key=org)
    return kospex_query.get_repos(server=server)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=$PWD/src python -m pytest tests/test_kgit_pull.py -k resolve -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/kgit.py tests/test_kgit_pull.py
git commit -m "feat(kgit): _resolve_pull_repos scope resolver"
```

---

## Task 4: `--check` staleness rows `_staleness_rows`

Pure formatting: repo rows + a reference `now` → display rows sorted stalest-first.

**Files:**
- Modify: `src/kgit.py`
- Test: `tests/test_kgit_pull.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_kgit_pull.py`:

```python
from kgit import _staleness_rows


def test_staleness_rows_formats_age_and_handles_null():
    now = "2026-07-09T00:00:00+00:00"
    repos = [
        {"_repo_id": "s~o~fresh", "last_fetch": "2026-07-08T00:00:00+00:00",
         "last_sync": "2026-07-08T00:00:00+00:00", "last_seen": "2026-07-01T00:00:00+00:00"},
        {"_repo_id": "s~o~stale", "last_fetch": "2026-05-10T00:00:00+00:00",
         "last_sync": "2026-05-10T00:00:00+00:00", "last_seen": "2026-05-01T00:00:00+00:00"},
        {"_repo_id": "s~o~never", "last_fetch": None,
         "last_sync": "2026-05-10T00:00:00+00:00", "last_seen": None},
    ]

    rows = _staleness_rows(repos, now=now)

    # stalest first: never (no fetch) sorts first, then stale, then fresh
    assert [r["repo_id"] for r in rows] == ["s~o~never", "s~o~stale", "s~o~fresh"]
    assert rows[0]["age"] == "never"
    assert rows[2]["age"] == "1d"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=$PWD/src python -m pytest tests/test_kgit_pull.py -k staleness -v`
Expected: FAIL — `ImportError: cannot import name '_staleness_rows'`.

- [ ] **Step 3: Implement the helper**

Add to `src/kgit.py`:

```python
def _staleness_rows(repos, now=None):
    """Build offline staleness display rows, sorted stalest-first.

    now: ISO string reference time (defaults to KospexUtils.now-ish); used so
    the function is deterministic under test.
    """
    if now is None:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).astimezone().isoformat()

    def _age_days(iso):
        if not iso:
            return None
        return KospexUtils.days_between_datetimes(iso, now)

    rows = []
    for r in repos:
        days = _age_days(r.get("last_fetch"))
        rows.append({
            "repo_id": r.get("_repo_id"),
            "last_fetch": r.get("last_fetch") or "never",
            "last_sync": r.get("last_sync") or "-",
            "last_commit": r.get("last_seen") or "-",
            "age": "never" if days is None else f"{int(days)}d",
            "_sort": float("inf") if days is None else days,
        })
    # stalest first: never-fetched (inf) first, then largest age
    rows.sort(key=lambda x: x["_sort"], reverse=True)
    for x in rows:
        del x["_sort"]
    return rows
```

Uses the existing `KospexUtils.days_between_datetimes(datetime1, datetime2) -> float` (kospex_utils.py:518); passing `now` keeps the function deterministic under test.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=$PWD/src python -m pytest tests/test_kgit_pull.py -k staleness -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kgit.py tests/test_kgit_pull.py
git commit -m "feat(kgit): _staleness_rows for offline --check"
```

---

## Task 5: git pull command construction `_pull_command`

Pure: build the git argv + env-overrides for a pull, so `--no-prompt` behaviour is testable without running git.

**Files:**
- Modify: `src/kgit.py`
- Test: `tests/test_kgit_pull.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_kgit_pull.py`:

```python
from kgit import _pull_command


def test_pull_command_interactive_by_default():
    argv, env = _pull_command("/clone/path", no_prompt=False)
    assert argv[:3] == ["git", "-C", "/clone/path"]
    assert argv[-2:] == ["pull", "--ff-only"]
    assert "GIT_TERMINAL_PROMPT" not in env


def test_pull_command_no_prompt_is_non_interactive():
    argv, env = _pull_command("/clone/path", no_prompt=True)
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert "-c" in argv and "credential.interactive=false" in argv
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=$PWD/src python -m pytest tests/test_kgit_pull.py -k pull_command -v`
Expected: FAIL — `ImportError: cannot import name '_pull_command'`.

- [ ] **Step 3: Implement the helper**

Add to `src/kgit.py`:

```python
def _pull_command(path, no_prompt=False):
    """Build (argv, env_overrides) for a fast-forward-only pull of `path`.

    no_prompt: make git non-interactive (fail fast) instead of invoking the
    credential helper — for unattended runs.
    """
    argv = ["git", "-C", path]
    env = {}
    if no_prompt:
        argv += ["-c", "credential.interactive=false"]
        env["GIT_TERMINAL_PROMPT"] = "0"
    argv += ["pull", "--ff-only"]
    return argv, env
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=$PWD/src python -m pytest tests/test_kgit_pull.py -k pull_command -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kgit.py tests/test_kgit_pull.py
git commit -m "feat(kgit): _pull_command builds ff-only pull argv/env"
```

---

## Task 6: `_git_pull` runner + `kgit pull` command + clone stamping (end-to-end)

**Files:**
- Modify: `src/kgit.py` (add `_git_pull`, the `pull` command; stamp `last_fetch` in `clone`)
- Test: `tests/test_kgit_pull_sync.py` (create, marked `integration`)

- [ ] **Step 1: Write the failing end-to-end test**

Create `tests/test_kgit_pull_sync.py`. Key technique: the clone's `origin` is set to a
github-style URL (so `KospexGit` derives a deterministic `github.com~test~repo` id) while
`url.<local>.insteadOf` rewrites fetches to the local bare repo — deterministic id **and**
offline pull.

```python
"""End-to-end test for `kgit pull` against throwaway git repos + KOSPEX_HOME."""
import os
import subprocess

import pytest
from click.testing import CliRunner

pytestmark = pytest.mark.integration

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@e.com",
    "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@e.com",
}
_URL = "https://github.com/test/repo.git"


def _git(cwd, *args, date=None):
    env = {**os.environ, **_GIT_ENV}
    if date:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = date
    subprocess.run(["git", "-C", str(cwd), *args], check=True,
                   capture_output=True, env=env)


def _head(path):
    return subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()


def _setup(tmp_path, monkeypatch):
    home = tmp_path / "home"; home.mkdir()
    monkeypatch.setenv("KOSPEX_HOME", str(home))
    monkeypatch.setenv("KOSPEX_DB", str(home / "kospex.db"))
    from kospex.habitat_config import HabitatConfig
    HabitatConfig.reset_instance()

    bare = tmp_path / "upstream.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)

    work = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", str(bare), str(work)], check=True)
    (work / "app.py").write_text("x = 1\n")
    _git(work, "add", "-A"); _git(work, "commit", "-q", "-m", "c1", date="2025-01-01T00:00:00")
    _git(work, "push", "-q", "origin", "main")

    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(bare), str(clone)], check=True)
    # deterministic repo_id from the github URL, but fetch rewritten to the local bare repo
    _git(clone, "remote", "set-url", "origin", _URL)
    _git(clone, "config", f"url.{bare}.insteadOf", _URL)
    return work, bare, clone


def test_kgit_pull_fast_forwards_stamps_and_syncs(tmp_path, monkeypatch):
    work, bare, clone = _setup(tmp_path, monkeypatch)

    from kgit import cli, kospex as kgit_kospex
    from kospex.db.migrator import Migrator
    import kospex_utils as KospexUtils

    Migrator(kgit_kospex.kospex_db).apply_pending()   # repos.last_fetch exists (0004)
    kgit_kospex.sync_repo(str(clone))                 # register repo (id github.com~test~repo)

    # advance upstream by one commit
    (work / "app.py").write_text("x = 2\n")
    _git(work, "add", "-A"); _git(work, "commit", "-q", "-m", "c2", date="2025-06-01T00:00:00")
    _git(work, "push", "-q", "origin", "main")
    upstream_head = _head(work)

    result = CliRunner().invoke(cli, ["pull", "--all"])
    assert result.exit_code == 0, result.output
    assert "updated" in result.output.lower()
    assert _head(clone) == upstream_head              # clone fast-forwarded to c2

    import sqlite3
    db = sqlite3.connect(KospexUtils.get_kospex_db_path())
    row = db.execute("SELECT _repo_id, last_fetch FROM repos").fetchone()
    assert row is not None and row[1] is not None      # last_fetch stamped
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=$PWD/src python -m pytest tests/test_kgit_pull_sync.py -v`
Expected: FAIL — `pull` command not registered (`No such command 'pull'`).

- [ ] **Step 3: Implement `_git_pull` and the `pull` command**

Add to `src/kgit.py`:

```python
def _git_pull(path, no_prompt=False):
    """Fast-forward the clone at `path`. Returns (ok, detail, commits).

    ok=False on missing dir / non-ff / auth-or-network failure (detail says why).
    commits = number of commits fast-forwarded (0 if already up to date).
    """
    if not os.path.isdir(path):
        return False, "not cloned", 0

    def _head():
        r = subprocess.run(["git", "-C", path, "rev-parse", "HEAD"],
                           capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else None

    before = _head()
    argv, env_over = _pull_command(path, no_prompt=no_prompt)
    proc = subprocess.run(argv, capture_output=True, text=True,
                          env={**os.environ, **env_over})
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "fetch failed").strip().splitlines()
        return False, f"fetch failed: {detail[-1] if detail else 'unknown'}", 0

    after = _head()
    if before and after and before == after:
        return True, "up to date", 0
    count = 0
    if before and after:
        r = subprocess.run(["git", "-C", path, "rev-list", "--count",
                            f"{before}..{after}"], capture_output=True, text=True)
        count = int(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else 0
    return True, f"updated {count} commits", count


@cli.command("pull")
@click.option("--all", "all_flag", is_flag=True, default=False, help="All known repos")
@click.option("--org", default=None, help="Scope to an ORG_KEY, e.g. github.com~kospex")
@click.option("--server", default=None, help="Scope to a git server, e.g. github.com")
@click.option("--check", "check", is_flag=True, default=False,
              help="Offline staleness report; no pull, no network")
@click.option("--no-prompt", "no_prompt", is_flag=True, default=False,
              help="Non-interactive auth (fail fast) for unattended runs")
@click.argument("repo_id", required=False, type=click.STRING)
def pull(all_flag, org, server, check, no_prompt, repo_id):
    """Refresh the local clones kospex already knows about (git pull + sync).

    Scope is required — a REPO_ID or one of --all / --org / --server.
    """
    try:
        repos = _resolve_pull_repos(kospex.kospex_query, repo_id=repo_id,
                                    all_flag=all_flag, org=org, server=server)
    except ValueError as e:
        raise click.UsageError(str(e))

    if not repos:
        console.print("No matching repos in the kospex DB.", style="yellow")
        return

    if check:
        table = PrettyTable()
        table.field_names = ["repo_id", "last_fetch", "last_sync", "last_commit", "age"]
        table.align = "l"
        for row in _staleness_rows(repos):
            table.add_row([row["repo_id"], row["last_fetch"], row["last_sync"],
                           row["last_commit"], row["age"]])
        print(table)
        return

    updated = current = skipped = failed = 0
    for r in repos:
        rid = r.get("_repo_id")
        path = r.get("file_path")
        ok, detail, commits = _git_pull(path, no_prompt=no_prompt)
        if not ok:
            console.print(f"SKIP {rid}: {detail}", style="yellow")
            skipped += 1
            continue
        kospex.kospex_query.set_repo_last_fetch(rid)
        try:
            kospex.sync_repo(path)
        except Exception as e:  # never let one repo kill the run
            log.error(f"sync failed for {rid}: {e}")
            console.print(f"FAIL {rid}: sync error: {e}", style="red")
            failed += 1
            continue
        if commits:
            console.print(f"OK   {rid}: {detail}", style="green")
            updated += 1
        else:
            current += 1
    console.print(
        f"\nDone. updated={updated} up-to-date={current} skipped={skipped} failed={failed}"
    )
```

- [ ] **Step 4: Stamp `last_fetch` on clone**

In the existing `clone` command (`src/kgit.py`), after a successful clone+sync, stamp the fetch time. Locate the single-repo branch:

```python
    if repo:
        repo_path = kgit.clone_repo(repo)
        if sync and repo_path:
            log.info(f"Syncing repository: {repo_path}")
            print("Syncing repo: " + repo_path)  # Keep user feedback
            kospex.sync_repo(repo_path)
            kospex.kospex_query.set_repo_last_fetch(kgit.get_repo_id())
```

(Add the same `set_repo_last_fetch(kgit.get_repo_id())` line after the `kospex.sync_repo(repo_path)` call in the `-filename` loop branch too, using the repo_id for each cloned repo — `kgit.clone_repo` sets the git context, so `kgit.get_repo_id()` returns the just-cloned repo's id.)

- [ ] **Step 5: Run the end-to-end test to verify it passes**

Run: `PYTHONPATH=$PWD/src python -m pytest tests/test_kgit_pull_sync.py -v`
Expected: PASS — pull fast-forwards the clone, output contains "updated", `last_fetch` stamped.

- [ ] **Step 6: Run the full suite**

Run: `PYTHONPATH=$PWD/src python -m pytest -q`
Expected: all pass (prior 274 + the new tests), scc-gated tests still pass without scc.

- [ ] **Step 7: Commit**

```bash
git add src/kgit.py tests/test_kgit_pull_sync.py
git commit -m "feat(kgit): pull command — refresh known clones + --check + stamp last_fetch"
```

---

## Task 7: Docs

**Files:**
- Modify: `CLAUDE.md` (commands section), `changes/202607-kgit-pull-refresh.md` (mark implemented)

- [ ] **Step 1: Update CLAUDE.md**

Under the git/kgit commands, add:

```
- `kgit pull REPO_ID|--all|--org ORG_KEY|--server SERVER` - git pull + re-sync known clones
- `kgit pull --check [scope]` - offline staleness report (last_fetch / last_sync / age)
- `kgit pull --no-prompt [scope]` - non-interactive auth for unattended runs
```

- [ ] **Step 2: Note completion in the change doc**

Add a short "Implemented" note at the top of `changes/202607-kgit-pull-refresh.md` referencing the command and migration `0004`.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md changes/202607-kgit-pull-refresh.md
git commit -m "docs: document kgit pull"
```

---

## Manual verification (after implementation, before PR)

On the real DB (branch code, via `PYTHONPATH`):

```bash
PYTHONPATH=$PWD/src python -c "import sys; sys.argv=['kospex','upgrade-db','-apply']; import kospex_cli; kospex_cli.cli()"   # apply 0004
PYTHONPATH=$PWD/src python -c "import sys; sys.argv=['kgit','pull','--check','--all']; import kgit; kgit.cli()"              # staleness view
PYTHONPATH=$PWD/src python -c "import sys; sys.argv=['kgit','pull','github.com~kospex~kospex']; import kgit; kgit.cli()"     # refresh one repo
```

Confirm: `--check` lists ages offline; a single-repo pull fast-forwards, stamps `last_fetch`, and reports.
