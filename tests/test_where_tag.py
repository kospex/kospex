"""Tests for KospexData.where_tag().

where_tag filters rows by a tag stored pipe-delimited in tech_type.
Real-world tags include ".", "#", "+" and spaces ("C#", "ASP.NET",
"Claude Code"), so the filter must not reject them, and a tag containing
a LIKE wildcard must match itself, not its neighbours.
"""
import sqlite3

import pytest
from sqlite_utils import Database

from kospex_query import KospexData


@pytest.fixture
def kd():
    """KospexData needs a DB handle; where_tag never touches it."""
    return KospexData(kospex_db=Database(memory=True))


def _rows_matching(kd, *tech_types):
    """Run the generated clause against toy tech_type values via sqlite3."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE t (tech_type TEXT)")
    for value in tech_types:
        conn.execute("INSERT INTO t VALUES (?)", (value,))
    return [
        r[0]
        for r in conn.execute(
            f"SELECT tech_type FROM t WHERE {kd.where_clause[0]}",
            kd.get_bind_parameters(),
        )
    ]


@pytest.mark.parametrize(
    "tag",
    ["python", "C#", ".NET", "ASP.NET", "Azure DevOps", "Claude Code",
     "Visual Studio", "llms.txt", "go", "c++"],
)
def test_real_world_tags_are_accepted(kd, tag):
    kd.where_tag(tag)
    assert kd.where_clause == ["tech_type LIKE ? ESCAPE '\\'"]
    assert kd.get_bind_parameters() == [f"%|{tag}|%"]


def test_tag_matches_itself_exactly(kd):
    kd.where_tag("C#")
    assert _rows_matching(
        kd, "|csharp|", "|C#|", "|go|C#|", "|C#sharp|"
    ) == ["|C#|", "|go|C#|"]


def test_like_wildcards_in_tag_do_not_match_neighbours(kd):
    kd.where_tag("A_B")
    assert _rows_matching(kd, "|A_B|", "|AxB|", "|A%B|") == ["|A_B|"]


def test_backslash_in_tag_does_not_break_the_clause(kd):
    kd.where_tag("back\\slash")
    assert _rows_matching(kd, "|back\\slash|", "|backXslash|") == ["|back\\slash|"]


def test_empty_tag_is_rejected(kd):
    for bad in ("", "   ", None):
        with pytest.raises(ValueError):
            kd.where_tag(bad)
    assert kd.where_clause == []
    assert kd.get_bind_parameters() == []


def test_underscore_only_tag_is_queryable(kd):
    kd.where_tag("_")
    assert kd.get_bind_parameters() == ["%|\\_|%"]


def test_params_stay_in_step_with_clauses(kd):
    kd.where_tag("python")
    kd.where_tag("C#")
    assert len(kd.where_clause) == len(kd.get_bind_parameters()) == 2
