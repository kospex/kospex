"""Tests for KospexSchema.build_file_metadata_rows().

The single-row builder merges the three sources that previously produced two
separate rows per file into exactly one current-state row per file:

  - panopticas walk  -> Provider, Language, tech_type (all files, incl UNKNOWN)
  - per-file commit  -> hash (the file's last-commit hash) + committer_when
  - scc              -> Lines/Code/Comments/Blanks/Complexity/Bytes (where scc knows it)

hash is the file's last-commit hash, falling back to the repo HEAD only when a
file has no commit entry. latest=1 on every row.
"""
import kospex_schema as KospexSchema


def _panopticas_walk():
    # Mirrors get_repo_files(): keyed by path, values carry Location, Language,
    # tech_type (list), Filename, and _git_* (add_git_to_dict already ran).
    git = {
        "_git_server": "github.com", "_git_owner": "kospex",
        "_git_repo": "kospex", "_repo_id": "github.com~kospex~kospex",
    }
    return {
        "src/app.py": {"Location": "src/app.py", "Filename": "app.py",
                       "Language": "Python", "tech_type": ["Python"], **git},
        "data.bin": {"Location": "data.bin", "Filename": "data.bin",
                     "Language": "UNKNOWN", "tech_type": [], **git},   # panopticas-only
        "new.py": {"Location": "new.py", "Filename": "new.py",
                   "Language": "Python", "tech_type": ["Python"], **git},  # not committed yet
    }


def test_merges_panopticas_scc_and_commit_into_one_row_per_file():
    files = _panopticas_walk()
    commit_map = {
        "src/app.py": {"hash": "aaa111", "committer_when": "2025-05-01T00:00:00+00:00"},
        "data.bin": {"hash": "bbb222", "committer_when": "2024-01-01T00:00:00+00:00"},
        # new.py deliberately absent (uncommitted)
    }
    scc_metrics = {
        "src/app.py": {"Lines": 100, "Code": 80, "Comments": 10, "Blanks": 10,
                       "Complexity": 5, "Bytes": 2048},
        # data.bin unknown to scc; new.py unknown to scc
    }

    rows = KospexSchema.build_file_metadata_rows(
        files, commit_map, scc_metrics=scc_metrics, git_hash="HEADHASH"
    )

    assert len(rows) == 3  # exactly one row per file
    by = {r["Provider"]: r for r in rows}

    # scc-known + committed: full merge
    app = by["src/app.py"]
    assert app["hash"] == "aaa111"                       # per-file last-commit hash
    assert app["committer_when"] == "2025-05-01T00:00:00+00:00"
    assert app["Language"] == "Python"
    assert app["tech_type"] == "|Python|"
    assert app["Lines"] == 100 and app["Code"] == 80
    assert app["latest"] == 1
    assert app["_git_owner"] == "kospex"

    # panopticas-only (scc-unknown) but committed: tags + date, no scc metrics
    dbin = by["data.bin"]
    assert dbin["hash"] == "bbb222"
    assert dbin["committer_when"] == "2024-01-01T00:00:00+00:00"
    assert dbin["Language"] == "UNKNOWN"
    assert "Lines" not in dbin

    # uncommitted file: HEAD fallback hash, no committer_when
    new = by["new.py"]
    assert new["hash"] == "HEADHASH"
    assert new.get("committer_when") is None
    assert new["latest"] == 1


def test_tech_type_none_when_no_tags():
    files = {
        "x": {"Location": "x", "Filename": "x", "Language": "UNKNOWN",
              "tech_type": None, "_repo_id": "s~o~r"},
    }
    rows = KospexSchema.build_file_metadata_rows(files, {}, git_hash="H")
    assert rows[0]["tech_type"] is None
    assert rows[0]["hash"] == "H"
