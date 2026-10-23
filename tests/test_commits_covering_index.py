"""commits gets a covering index, and developer_stats author-time columns: 0011.

commits' only index was its primary key, (_repo_id, hash). The summary
aggregates (developers(), repos(), active_devs()) and sync's
update_developer_stats SELECT fetched table rows for every commit. With
idx_commits_repo_author_when they read the index alone:

                                      PK only        + index
    repos(), estate                   1.66s          0.49s   (1.1M commits)
    repos(org_key)                    0.92s          0.09s
    active_devs(), estate             1.01s          0.08s
    update_developer_stats SELECT     0.54s          0.19s   (199k-commit repo)
    size                              -              190MB (+4.4%)

repos() also selects _git_server, _git_owner and _git_repo. A first version
of the index left them out, and repos() then ignored it and scanned the
table, which these plan tests caught.

first_authored / last_authored are schema only here. update_developer_stats
writes them (see test_developer_stats_committer_time.py), and rows heal on
their next sync, so there is no backfill.

The plan tests capture the SQL that production methods actually send, and
explain that, rather than restating a copy of the query here.
"""
import pytest

import kospex_schema as KospexSchema
from kospex_query import KospexQuery

INDEX_NAME = "idx_commits_repo_author_when"
INDEX_COLUMNS = ["_repo_id", "_git_server", "_git_owner", "_git_repo",
                 "author_email", "committer_email", "committer_when", "author_when"]
REPO = "github.com~acme~svc"


def _fresh_db(tmp_path, monkeypatch):
    from kospex.habitat_config import HabitatConfig

    home = tmp_path / "kospex_home"
    home.mkdir()
    monkeypatch.setenv("KOSPEX_HOME", str(home))
    monkeypatch.setenv("KOSPEX_DB", str(home / "kospex.db"))
    HabitatConfig.reset_instance()
    db = KospexSchema.connect_or_create_kospex_db()
    for i, (author, when) in enumerate([
        ("dev@example.com", "2026-01-02T10:00:00+00:00"),
        ("dev@example.com", "2026-03-04T10:00:00+11:00"),
        ("other@example.com", "2025-06-07T10:00:00-08:00"),
    ]):
        db["commits"].insert({
            "hash": f"h{i}", "_repo_id": REPO, "_git_server": "github.com",
            "_git_owner": "acme", "_git_repo": "svc",
            "author_email": author, "committer_email": author,
            "author_when": when, "committer_when": when,
        })
    return db


def _captured_commits_selects(db, call):
    """Run a production call and return the SELECTs it sent that read commits."""
    sent = []
    db.conn.set_trace_callback(sent.append)
    try:
        call()
    finally:
        db.conn.set_trace_callback(None)
    selects = [s for s in sent
               if s.lstrip().upper().startswith("SELECT") and "commits" in s.lower()
               and "commit_files" not in s.lower()]
    assert selects, f"the call sent no SELECT on commits: {sent}"
    return selects


def _plan(db, sql):
    # Traced SQL has its parameters already bound, so explain it as sent.
    return " | ".join(r[3] for r in db.execute("EXPLAIN QUERY PLAN " + sql).fetchall())


def test_fresh_db_has_the_index_with_its_column_order(tmp_path, monkeypatch):
    """_repo_id must lead so per-repo queries seek; the rest make it covering."""
    db = _fresh_db(tmp_path, monkeypatch)
    cols = [r[2] for r in db.execute(f"PRAGMA index_info({INDEX_NAME})").fetchall()]
    assert cols == INDEX_COLUMNS


def test_fresh_db_has_the_author_time_columns(tmp_path, monkeypatch):
    db = _fresh_db(tmp_path, monkeypatch)
    cols = {r[1] for r in db.execute("PRAGMA table_info(developer_stats)").fetchall()}
    assert {"first_authored", "last_authored"} <= cols


def test_baseline_schema_does_not_declare_them():
    """Declared in the migration only. Declaring them in the baseline CREATE as
    well fails every clean install with 'duplicate column name'."""
    assert "first_authored" not in KospexSchema.SQL_CREATE_DEVELOPER_STATS
    assert "last_authored" not in KospexSchema.SQL_CREATE_DEVELOPER_STATS
    assert INDEX_NAME not in KospexSchema.SQL_CREATE_COMMITS


@pytest.mark.parametrize("label,call", [
    ("repos()", lambda kq: kq.repos()),
    ("repos(repo_id)", lambda kq: kq.repos(repo_id=REPO)),
    ("active_devs()", lambda kq: kq.active_devs()),
    ("update_developer_stats", lambda kq: kq.update_developer_stats(REPO)),
])
def test_production_queries_read_only_the_index(tmp_path, monkeypatch, label, call):
    db = _fresh_db(tmp_path, monkeypatch)
    kq = KospexQuery(kospex_db=db)
    for sql in _captured_commits_selects(db, lambda: call(kq)):
        plan = _plan(db, sql)
        assert f"COVERING INDEX {INDEX_NAME}" in plan, f"{label}: {plan}\n{sql}"
