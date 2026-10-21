"""`KospexData.where_tag()` must bind the tag, not interpolate it.

The clause was built as `f"tech_type LIKE '%|{tag}|%'"` with no parameter, and
an `^[a-zA-Z-]+$` allowlist standing in for the binding it lacked (CWE-89). The
allowlist is the only thing between a caller-supplied tag and the SQL text, and
it rejects real tags that panopticas writes: on live data `.NET`, `ASP.NET`,
`Azure DevOps`, `C#`, `Claude Code`, `Visual Studio` and `llms.txt` are 7 of the
63 tags in use, so a tag listing would show all 63 and raise ValueError on those
7.

Binding the value fixes both at once, so these tests cover both halves: the
clause carries a `?`, and every real tag becomes queryable through the
production call.

A bound LIKE pattern still interprets `%` and `_` as wildcards, so the pattern
is escaped and the clause declares ESCAPE -- otherwise a tag containing `_`
would silently match tags it is not.
"""
import os

import pytest
import sqlite_utils

import kospex_schema as KospexSchema
from kospex_query import KospexData, KospexQuery


@pytest.fixture(autouse=True)
def _preserve_kospex_env():
    """Constructing KospexQuery sets KOSPEX_* env vars as a side effect;
    restore them so we don't leak state into later tests."""
    keys = ("KOSPEX_CODE", "KOSPEX_DB", "KOSPEX_CONFIG", "KOSPEX_HOME", "KOSPEX_LOGS")
    saved = {k: os.environ.get(k) for k in keys}
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


# Tags as panopticas writes them into tech_type: pipe-delimited, with a leading
# and trailing pipe, so a tag match is LIKE '%|tag|%'.
_ROWS = [
    {"Provider": "src/app.cs", "hash": "h1", "_repo_id": "github.com~acme~one",
     "Filename": "app.cs", "tech_type": "|C#|Visual Studio|", "latest": 1},
    {"Provider": "CLAUDE.md", "hash": "h1", "_repo_id": "github.com~acme~one",
     "Filename": "CLAUDE.md", "tech_type": "|Claude Code|", "latest": 1},
    {"Provider": "llms.txt", "hash": "h1", "_repo_id": "github.com~acme~two",
     "Filename": "llms.txt", "tech_type": "|llms.txt|AI|", "latest": 1},
    {"Provider": "azure-pipelines.yml", "hash": "h1", "_repo_id": "github.com~acme~two",
     "Filename": "azure-pipelines.yml", "tech_type": "|Azure DevOps|CICD|", "latest": 1},
    {"Provider": "main.py", "hash": "h1", "_repo_id": "github.com~acme~two",
     "Filename": "main.py", "tech_type": "|Python|", "latest": 1},
    # superseded row: must not be returned
    {"Provider": "old.cs", "hash": "h0", "_repo_id": "github.com~acme~one",
     "Filename": "old.cs", "tech_type": "|C#|", "latest": 0},
    # a tag holding a LIKE wildcard, and a tag it must not be confused with
    {"Provider": "a.conf", "hash": "h1", "_repo_id": "github.com~acme~three",
     "Filename": "a.conf", "tech_type": "|A_B|", "latest": 1},
    {"Provider": "b.conf", "hash": "h1", "_repo_id": "github.com~acme~three",
     "Filename": "b.conf", "tech_type": "|AxB|", "latest": 1},
]


def _seed_db():
    db = sqlite_utils.Database(memory=True)
    db.execute(KospexSchema.SQL_CREATE_FILE_METADATA)
    db[KospexSchema.TBL_FILE_METADATA].insert_all(
        _ROWS, pk=["Provider", "hash", "_repo_id"]
    )
    return db


class TestClause:
    """The generated SQL, not the result -- the interpolation is the defect."""

    def test_the_tag_is_bound_not_interpolated(self):
        kd = KospexData(kospex_db=_seed_db())
        kd.from_table(KospexSchema.TBL_FILE_METADATA)
        kd.select("*")
        kd.where_tag("C#")

        sql = kd.generate_sql()

        assert "C#" not in sql, f"tag reached the SQL text: {sql}"
        assert "tech_type LIKE ?" in sql, sql
        assert kd.get_bind_parameters() == ["%|C#|%"]

    def test_the_clause_declares_an_escape_character(self):
        kd = KospexData(kospex_db=_seed_db())
        kd.where_tag("Python")

        assert "ESCAPE" in kd.generate_sql().upper()

    def test_wildcards_in_the_tag_are_escaped_in_the_pattern(self):
        kd = KospexData(kospex_db=_seed_db())
        kd.where_tag("A_B")

        # The pattern keeps the literal tag, with its wildcard neutralised.
        assert kd.get_bind_parameters() == [r"%|A\_B|%"]

    def test_params_stay_in_clause_order_alongside_other_wheres(self):
        """where_tag appends to the same params list as where(); a mismatch here
        shifts every bind value in the statement."""
        kd = KospexData(kospex_db=_seed_db())
        kd.from_table(KospexSchema.TBL_FILE_METADATA)
        kd.select("*")
        kd.where("latest", "=", 1)
        kd.where_tag("Python")
        kd.where("_repo_id", "=", "github.com~acme~two")

        assert kd.get_bind_parameters() == [1, "%|Python|%", "github.com~acme~two"]


class TestRejected:
    """A tag must still be a usable value -- binding is not an excuse to
    accept nothing at all."""

    @pytest.mark.parametrize("tag", ["", None, 7, []])
    def test_an_unusable_tag_still_raises(self, tag):
        kd = KospexData(kospex_db=_seed_db())
        with pytest.raises(ValueError):
            kd.where_tag(tag)


class TestThroughProduction:
    """Drive get_metadata_files(), the only caller, against a real DB."""

    @pytest.mark.parametrize(
        "tag,expected",
        [
            ("C#", ["app.cs"]),                       # '#', was rejected
            ("Claude Code", ["CLAUDE.md"]),           # space, was rejected
            ("Azure DevOps", ["azure-pipelines.yml"]),
            ("llms.txt", ["llms.txt"]),               # '.', was rejected
            ("Python", ["main.py"]),                  # always worked
        ],
    )
    def test_every_real_tag_is_queryable(self, tag, expected):
        kq = KospexQuery(kospex_db=_seed_db())

        rows = kq.get_metadata_files(tag=tag)

        assert sorted(r["Filename"] for r in rows) == sorted(expected)

    def test_a_wildcard_tag_does_not_match_its_neighbour(self):
        kq = KospexQuery(kospex_db=_seed_db())

        rows = kq.get_metadata_files(tag="A_B")

        assert [r["Filename"] for r in rows] == ["a.conf"], "the `_` acted as a wildcard"

    def test_a_partial_tag_does_not_match(self):
        """The pipe delimiters are what make the match exact; keep them."""
        kq = KospexQuery(kospex_db=_seed_db())

        assert kq.get_metadata_files(tag="Pytho") == []
