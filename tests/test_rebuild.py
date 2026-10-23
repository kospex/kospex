"""`kospex rebuild <task>`: recompute derived tables without re-syncing.

The first task is developer-stats. It exists because the summary pages read
developer_stats with no fallback, and a DB upgraded from before
first_commit / last_commit moved to committer time has rows with author-time
dates (last_authored IS NULL). Rebuilding needs only the DB, no clones.

The scope rule matches `kgit pull`: exactly one of REPO_ID / -all / -org /
-server, so nothing is rebuilt estate-wide by accident.
"""
from datetime import datetime, timedelta, timezone

import pytest
from click.testing import CliRunner

import kospex_rebuild as KospexRebuild
import kospex_schema as KospexSchema
from kospex_query import KospexQuery

REPOS = {
    "github.com~acme~api": ("github.com", "acme", "api"),
    "github.com~acme~web": ("github.com", "acme", "web"),
    "gitlab.com~beta~svc": ("gitlab.com", "beta", "svc"),
}


def _iso(days_ago):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago, hours=1)).isoformat(timespec="seconds")


@pytest.fixture
def db(tmp_path, monkeypatch):
    from kospex.habitat_config import HabitatConfig

    home = tmp_path / "kospex_home"
    home.mkdir()
    monkeypatch.setenv("KOSPEX_HOME", str(home))
    monkeypatch.setenv("KOSPEX_DB", str(home / "kospex.db"))
    HabitatConfig.reset_instance()
    db = KospexSchema.connect_or_create_kospex_db()
    for n, (repo_id, (server, owner, repo)) in enumerate(REPOS.items()):
        db["repos"].insert({"_repo_id": repo_id, "_git_server": server, "_git_owner": owner,
                            "_git_repo": repo, "file_path": str(tmp_path / "missing" / repo)})
        for i, (committed, authored) in enumerate([(10, 300), (400, 400)]):
            db["commits"].insert({
                "hash": f"h{n}{i}", "_repo_id": repo_id, "_git_server": server,
                "_git_owner": owner, "_git_repo": repo,
                "author_email": f"dev{i}@example.com", "committer_email": f"dev{i}@example.com",
                "author_when": _iso(authored), "committer_when": _iso(committed),
                "parents": 1, "_files": 1,
            })
    return db


def _make_pre_change(db):
    """Rows as an un-rebuilt DB has them: author-time dates, no *_authored."""
    kq = KospexQuery(kospex_db=db)
    for repo_id in REPOS:
        kq.update_developer_stats(repo_id)
    db.execute("UPDATE developer_stats SET last_commit = last_authored, first_commit = first_authored, "
               "last_authored = NULL, first_authored = NULL")
    db.execute("DELETE FROM developer_stats WHERE _repo_id = 'gitlab.com~beta~svc'")
    db.conn.commit()
    assert kq.developer_stats_coverage() == {"repos_not_rebuilt": 2, "repos_without_stats": 1}


def _statuses_match_commits(db, **scope):
    kq = KospexQuery(kospex_db=db)
    fast = sorted((r["author"], r["status"]) for r in kq.developer_statuses(**scope))
    slow = sorted((r["author"], r["status"]) for r in kq.developers(**scope))
    return fast == slow


# --- scope -------------------------------------------------------------------

@pytest.mark.parametrize("kwargs", [{}, {"repo_id": "github.com~acme~api", "all_repos": True},
                                    {"org_key": "github.com~acme", "server": "github.com"}])
def test_scope_needs_exactly_one(db, kwargs):
    with pytest.raises(ValueError, match="exactly one scope"):
        KospexRebuild.resolve_scope(KospexQuery(kospex_db=db), **kwargs)


@pytest.mark.parametrize("kwargs, expected", [
    ({"all_repos": True}, set(REPOS)),
    ({"org_key": "github.com~acme"}, {"github.com~acme~api", "github.com~acme~web"}),
    ({"server": "gitlab.com"}, {"gitlab.com~beta~svc"}),
    ({"repo_id": "github.com~acme~web"}, {"github.com~acme~web"}),
])
def test_scope_selects_the_right_repos(db, kwargs, expected):
    repos = KospexRebuild.resolve_scope(KospexQuery(kospex_db=db), **kwargs)
    assert {r["_repo_id"] for r in repos} == expected


# --- the developer-stats task ------------------------------------------------

def test_developer_stats_heals_pre_change_rows_and_fills_missing_repos(db):
    _make_pre_change(db)
    assert not _statuses_match_commits(db), "fixture must show the wrong counts before a rebuild"
    kq = KospexQuery(kospex_db=db)
    repos = KospexRebuild.resolve_scope(kq, all_repos=True)
    results = KospexRebuild.run_task(KospexRebuild.TASKS["developer-stats"], repos, kq)
    assert [r.status for r in results] == ["ok"] * 3
    assert kq.developer_stats_coverage() == {"repos_not_rebuilt": 0, "repos_without_stats": 0}
    assert _statuses_match_commits(db)


def test_developer_stats_needs_no_clone(db):
    # Every repo's file_path points at a directory that doesn't exist.
    task = KospexRebuild.TASKS["developer-stats"]
    assert task.needs_clone is False
    kq = KospexQuery(kospex_db=db)
    results = KospexRebuild.run_task(task, KospexRebuild.resolve_scope(kq, all_repos=True), kq)
    assert all(r.status == "ok" for r in results)


def test_a_task_that_needs_a_clone_skips_repos_without_one(db):
    task = KospexRebuild.RebuildTask("needs-clone", "test", (), True, lambda kq, repo: 1)
    kq = KospexQuery(kospex_db=db)
    results = KospexRebuild.run_task(task, KospexRebuild.resolve_scope(kq, all_repos=True), kq)
    assert {r.status for r in results} == {"skipped"}
    assert all("no clone" in r.detail for r in results)


def test_one_failing_repo_does_not_stop_the_rest(db):
    def flaky(kq, repo):
        if repo["_repo_id"] == "github.com~acme~web":
            raise RuntimeError("boom")
        return kq.update_developer_stats(repo["_repo_id"])

    task = KospexRebuild.RebuildTask("flaky", "test", (), False, flaky)
    kq = KospexQuery(kospex_db=db)
    results = {r.repo_id: r for r in
               KospexRebuild.run_task(task, KospexRebuild.resolve_scope(kq, all_repos=True), kq)}
    assert results["github.com~acme~web"].status == "failed"
    assert "boom" in results["github.com~acme~web"].detail
    assert results["github.com~acme~api"].status == results["gitlab.com~beta~svc"].status == "ok"


def test_developer_stats_rebuild_is_atomic_per_repo(db, monkeypatch):
    # sqlite-utils commits each write statement on its own unless a
    # transaction is open, so the DELETE used to commit before the INSERTs:
    # a failure part-way left the repo with only some of its rows.
    kq = KospexQuery(kospex_db=db)
    kq.update_developer_stats("github.com~acme~api")
    before = list(db.query("SELECT * FROM developer_stats WHERE _repo_id = 'github.com~acme~api'"))
    assert before

    real_execute = db.execute

    def fail_on_update(sql, params=None):
        if sql.lstrip().upper().startswith("UPDATE"):
            raise RuntimeError("simulated failure after the inserts")
        return real_execute(sql, params)

    monkeypatch.setattr(db, "execute", fail_on_update)
    with pytest.raises(RuntimeError):
        kq.update_developer_stats("github.com~acme~api")
    monkeypatch.undo()
    assert list(db.query("SELECT * FROM developer_stats WHERE _repo_id = 'github.com~acme~api'")) == before


def test_developer_stats_commits_once_per_repo(db):
    kq = KospexQuery(kospex_db=db)
    sent = []
    db.conn.set_trace_callback(sent.append)
    try:
        kq.update_developer_stats("github.com~acme~api")
    finally:
        db.conn.set_trace_callback(None)
    assert sum(1 for s in sent if s.strip().upper() == "COMMIT") == 1, sent


def test_every_task_is_fully_described():
    for name, task in KospexRebuild.TASKS.items():
        assert task.name == name and task.description and task.writes and callable(task.run)


# --- the CLI -----------------------------------------------------------------

def _cli(*args):
    import kospex_cli
    return CliRunner().invoke(kospex_cli.cli, ["rebuild", *args])


def test_cli_has_a_subcommand_for_every_task():
    import kospex_cli
    assert set(KospexRebuild.TASKS) | {"list"} == set(kospex_cli.rebuild.commands)


def test_cli_rebuild_all(db):
    _make_pre_change(db)
    result = _cli("developer-stats", "-all")
    assert result.exit_code == 0, result.output
    assert "3 rebuilt, 0 skipped, 0 failed" in result.output
    assert _statuses_match_commits(db)


def test_cli_rebuild_one_org(db):
    _make_pre_change(db)
    result = _cli("developer-stats", "-org", "github.com~acme")
    assert result.exit_code == 0, result.output
    assert "2 rebuilt" in result.output
    # gitlab's repo was outside the scope and still has no stats
    assert KospexQuery(kospex_db=db).developer_stats_coverage() == \
        {"repos_not_rebuilt": 0, "repos_without_stats": 1}


@pytest.mark.parametrize("args", [[], ["github.com~acme~api", "-all"], ["-org", "github.com~acme", "-all"]])
def test_cli_refuses_anything_but_exactly_one_scope(db, args):
    result = _cli("developer-stats", *args)
    assert result.exit_code == 2
    assert "exactly one scope" in result.output


def test_cli_dry_run_lists_repos_and_changes_nothing(db):
    _make_pre_change(db)
    before = list(db.query("SELECT * FROM developer_stats ORDER BY _repo_id, author_email"))
    result = _cli("developer-stats", "-all", "-dry-run")
    assert result.exit_code == 0, result.output
    assert all(repo_id in result.output for repo_id in REPOS)
    assert "3 repo(s) would be rebuilt" in result.output
    assert list(db.query("SELECT * FROM developer_stats ORDER BY _repo_id, author_email")) == before


def test_cli_unknown_repo_exits_non_zero(db):
    result = _cli("developer-stats", "github.com~acme~nope")
    assert result.exit_code == 1
    assert "No repos in scope" in result.output


def test_cli_exits_non_zero_when_a_repo_fails(db, monkeypatch):
    def fail(self, repo_id):
        raise RuntimeError("disk full")
    monkeypatch.setattr(KospexQuery, "update_developer_stats", fail)
    result = _cli("developer-stats", "-all")
    assert result.exit_code == 1
    assert "0 rebuilt, 0 skipped, 3 failed" in result.output
    assert "disk full" in result.output


def test_cli_list_shows_each_task(db):
    result = _cli("list")
    assert result.exit_code == 0, result.output
    for name, task in KospexRebuild.TASKS.items():
        assert name in result.output
