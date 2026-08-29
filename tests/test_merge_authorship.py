"""Tests for separating merge commits from authorship (#170).

`commit_stats()` counted every row with `COUNT(*)`, so a merge added one to
whoever merged — typically a maintainer or release manager, which is exactly the
population `key_person()` exists to measure. On a 109-repo estate 12.3% of
commits are merges, reaching 96.3% for some authors.

Merges are not simply subtracted. Merging is evidence of knowledge in its own
right: the merger read and accepted other people's changes into the subsystem.
So they are reported as a separate `merges` column rather than discarded — which
is what surfaces the developer with few commits who integrates most of a
subsystem, a bus-factor risk that is invisible either way round otherwise.

A commit counts as authorship unless it is a *clean* merge:

    parents <= 1 OR _files > 0

`parents` is NULL for commits synced before #164, so the `_files` clause carries
those rows. Both clauses are needed for as long as any pre-#164 data remains.
"""
import os

import pytest
import sqlite_utils

import kospex_schema as KospexSchema
from kospex_query import KospexQuery


@pytest.fixture(autouse=True)
def _preserve_kospex_env():
    keys = ("KOSPEX_CODE", "KOSPEX_DB", "KOSPEX_CONFIG", "KOSPEX_HOME", "KOSPEX_LOGS")
    saved = {k: os.environ.get(k) for k in keys}
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


REPO = "s~o~r"
WHEN = "2026-06-01T00:00:00+00:00"


def _commit(hash_value, author, parents, files, when=WHEN):
    return {
        "_repo_id": REPO, "hash": hash_value, "author_email": author,
        "author_when": when, "committer_when": when,
        "parents": parents, "_files": files,
    }


def _seed(rows):
    db = sqlite_utils.Database(memory=True)
    db.execute(KospexSchema.SQL_CREATE_COMMITS)
    db[KospexSchema.TBL_COMMITS].insert_all(rows, pk=["_repo_id", "hash"])
    return KospexQuery(kospex_db=db)


def _by_author(stats):
    return {r["author"]: r for r in stats}


def test_clean_merge_is_not_counted_as_authorship():
    kq = _seed([
        _commit("c1", "dev@e.com", 1, 5),
        _commit("m1", "dev@e.com", 2, 0),
    ])

    row = _by_author(kq.commit_stats(repo_id=REPO))["dev@e.com"]

    assert row["commits"] == 1


def test_merges_are_reported_separately():
    """Merging is evidence of knowledge, so the count is surfaced, not dropped."""
    kq = _seed([
        _commit("c1", "dev@e.com", 1, 5),
        _commit("m1", "dev@e.com", 2, 0),
        _commit("m2", "dev@e.com", 2, 0),
    ])

    row = _by_author(kq.commit_stats(repo_id=REPO))["dev@e.com"]

    assert row["commits"] == 1
    assert row["merges"] == 2


def test_merge_carrying_its_own_content_is_authorship():
    """A conflict resolution is real work by the merger — it exists in no parent."""
    kq = _seed([
        _commit("c1", "dev@e.com", 1, 5),
        _commit("m1", "dev@e.com", 2, 3),   # evil merge
    ])

    row = _by_author(kq.commit_stats(repo_id=REPO))["dev@e.com"]

    assert row["commits"] == 2
    assert row["merges"] == 0


def test_octopus_merge_is_excluded():
    kq = _seed([
        _commit("c1", "dev@e.com", 1, 5),
        _commit("m1", "dev@e.com", 16, 0),
    ])

    row = _by_author(kq.commit_stats(repo_id=REPO))["dev@e.com"]

    assert row["commits"] == 1
    assert row["merges"] == 1


def test_root_commit_is_authorship():
    kq = _seed([_commit("r1", "dev@e.com", 0, 4)])

    row = _by_author(kq.commit_stats(repo_id=REPO))["dev@e.com"]

    assert row["commits"] == 1
    assert row["merges"] == 0


def test_null_parents_falls_back_to_the_file_count():
    """Commits synced before #164 have parents NULL; _files must carry them."""
    kq = _seed([
        _commit("c1", "dev@e.com", None, 5),   # ordinary, pre-#164
        _commit("m1", "dev@e.com", None, 0),   # clean merge, pre-#164
    ])

    row = _by_author(kq.commit_stats(repo_id=REPO))["dev@e.com"]

    assert row["commits"] == 1
    assert row["merges"] == 1


def test_empty_commit_counts_once_parents_is_known():
    """`git commit --allow-empty` has one parent and no files. Pre-#164 it is
    indistinguishable from a merge; with parents populated it is authorship."""
    kq = _seed([_commit("e1", "dev@e.com", 1, 0)])

    row = _by_author(kq.commit_stats(repo_id=REPO))["dev@e.com"]

    assert row["commits"] == 1
    assert row["merges"] == 0


def test_a_mixed_estate_resolves_each_row_independently():
    """Some repos re-synced post-#164, some not — no migration flag needed."""
    kq = _seed([
        _commit("c1", "dev@e.com", 1, 5),       # post-#164 ordinary
        _commit("m1", "dev@e.com", 2, 0),       # post-#164 clean merge
        _commit("c2", "dev@e.com", None, 7),    # pre-#164 ordinary
        _commit("m2", "dev@e.com", None, 0),    # pre-#164 clean merge
    ])

    row = _by_author(kq.commit_stats(repo_id=REPO))["dev@e.com"]

    assert row["commits"] == 2
    assert row["merges"] == 2


def test_percentages_are_computed_on_authorship_not_totals():
    """A merger's share must fall, and a non-merger's share rises because the
    denominator shrank — the support question this will generate."""
    kq = _seed([
        _commit("a1", "writer@e.com", 1, 5),
        _commit("a2", "writer@e.com", 1, 5),
        _commit("b1", "merger@e.com", 1, 5),
        _commit("b2", "merger@e.com", 2, 0),
        _commit("b3", "merger@e.com", 2, 0),
    ])

    people = {p["author"]: p for p in kq.key_person(repo_id=REPO)}

    # 3 authorship commits total: writer 2, merger 1
    assert people["writer@e.com"]["% commits"] == "66.7%"
    assert people["merger@e.com"]["% commits"] == "33.3%"


def test_key_person_carries_the_merge_count_through():
    kq = _seed([
        _commit("c1", "dev@e.com", 1, 5),
        _commit("m1", "dev@e.com", 2, 0),
    ])

    person = kq.key_person(repo_id=REPO)[0]

    assert person["merges"] == 1


def test_the_gatekeeper_is_visible():
    """Few commits, most of the merging — the bus-factor case that is invisible
    both before this change (merges look like authorship) and after a naive
    exclusion (they simply vanish)."""
    rows = [_commit(f"w{i}", "writer@e.com", 1, 5) for i in range(20)]
    rows += [_commit("g1", "gatekeeper@e.com", 1, 2)]
    rows += [_commit(f"gm{i}", "gatekeeper@e.com", 2, 0) for i in range(15)]

    people = {p["author"]: p for p in _seed(rows).key_person(repo_id=REPO)}

    assert people["gatekeeper@e.com"]["commits"] == 1
    assert people["gatekeeper@e.com"]["merges"] == 15
    assert people["writer@e.com"]["merges"] == 0
