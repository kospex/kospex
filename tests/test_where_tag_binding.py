"""`KospexData.where_tag()` binds the tag, and validates it against panopticas.

Two concerns, both on the one function.

**Binding.** The clause was built as `f"tech_type LIKE '%|{tag}|%'"` with no
parameter, and an `^[a-zA-Z-]+$` allowlist standing in for the binding it lacked
(CWE-89). The allowlist was the only thing between a caller-supplied tag and the
SQL text, and it rejected real tags that panopticas writes: `.NET`, `ASP.NET`,
`Azure DevOps`, `C#`, `Claude Code`, `Visual Studio` and `llms.txt` are 7 of the
63 tags in live data.

A bound LIKE pattern still interprets `%` and `_` as wildcards, so the pattern is
escaped and the clause declares ESCAPE -- otherwise a tag containing `_` would
silently match tags it is not.

**Validation.** With the value bound, the tag no longer needs an input filter for
safety, so the filter can be what it should always have been: a check that the
caller asked for a tag that exists. `panopticas.get_tags()` is the vocabulary --
derived from the detection rules rather than hand-maintained, so it tracks the
tags panopticas can actually emit.

The check is **case-insensitive**, for two reasons: SQLite's `LIKE` is itself
ASCII case-insensitive, so a case-sensitive gate would reject spellings that
would have matched anyway; and panopticas recases tags between releases (`eslint`
became `ESLint` between 0.0.19 and 0.0.21), which a case-insensitive check
absorbs for free.

The caller's spelling is kept in the bound pattern rather than canonicalised,
since `LIKE` matches either way and rewriting a caller's input would be
surprising.

**Known limitation.** A tag panopticas has *retired* is now rejected along with a
typo, because `get_tags()` reports only the current vocabulary. On live data that
is `Claude Code` (7 files), renamed to brand-level `Claude`. Tests here use a
patched vocabulary rather than asserting on panopticas' current contents, which
would make them a test of someone else's data.
"""
import os

import panopticas
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


@pytest.fixture
def vocabulary(monkeypatch):
    """Replace panopticas' vocabulary, for the cases that need tags panopticas
    does not define -- a LIKE wildcard in a tag, or a retired spelling."""
    def _set(*tags):
        monkeypatch.setattr(panopticas, "get_tags", lambda: list(tags))
    return _set


# Tags as panopticas writes them into tech_type: pipe-delimited, with a leading
# and trailing pipe, so a tag match is LIKE '%|tag|%'. Every tag used here
# without the `vocabulary` fixture is one panopticas currently defines.
_ROWS = [
    {"Provider": "src/app.cs", "hash": "h1", "_repo_id": "github.com~acme~one",
     "Filename": "app.cs", "tech_type": "|C#|Visual Studio|", "latest": 1},
    {"Provider": "azure-pipelines.yml", "hash": "h1", "_repo_id": "github.com~acme~two",
     "Filename": "azure-pipelines.yml", "tech_type": "|Azure DevOps|pipeline|", "latest": 1},
    {"Provider": "llms.txt", "hash": "h1", "_repo_id": "github.com~acme~two",
     "Filename": "llms.txt", "tech_type": "|llms.txt|AI|", "latest": 1},
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
    # a tag stored in the spelling panopticas used before it recased it
    {"Provider": ".eslintrc", "hash": "h1", "_repo_id": "github.com~acme~four",
     "Filename": ".eslintrc", "tech_type": "|eslint|config|", "latest": 1},
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

    def test_wildcards_in_the_tag_are_escaped_in_the_pattern(self, vocabulary):
        vocabulary("A_B")
        kd = KospexData(kospex_db=_seed_db())
        kd.where_tag("A_B")

        # The pattern keeps the literal tag, with its wildcard neutralised.
        assert kd.get_bind_parameters() == [r"%|A\_B|%"]

    def test_a_percent_and_a_backslash_are_escaped_too(self, vocabulary):
        vocabulary("A%B", "A\\B")
        kd = KospexData(kospex_db=_seed_db())
        kd.where_tag("A%B")
        kd.where_tag("A\\B")

        assert kd.get_bind_parameters() == [r"%|A\%B|%", r"%|A\\B|%"]

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


class TestKnownTags:
    """The tag is checked against panopticas' vocabulary."""

    @pytest.mark.parametrize(
        "tag", ["Python", "C#", "AI", "llms.txt", "Azure DevOps", "workflow"]
    )
    def test_a_current_panopticas_tag_is_accepted(self, tag):
        """Not patched: these must pass against the installed panopticas, which
        is the point of validating against it rather than a local list."""
        kd = KospexData(kospex_db=_seed_db())
        kd.where_tag(tag)

        assert kd.get_bind_parameters() == [f"%|{tag}|%"]

    def test_an_unknown_tag_raises(self, vocabulary):
        vocabulary("Python", "AI")
        kd = KospexData(kospex_db=_seed_db())

        with pytest.raises(ValueError) as exc:
            kd.where_tag("Pytho")

        assert "Pytho" in str(exc.value)

    def test_nothing_is_added_to_the_query_when_the_tag_is_rejected(self, vocabulary):
        """A half-built clause with no matching param would corrupt every later
        bind value in the statement."""
        vocabulary("Python")
        kd = KospexData(kospex_db=_seed_db())

        with pytest.raises(ValueError):
            kd.where_tag("NotATag")

        assert kd.where_clause == []
        assert kd.get_bind_parameters() == []

    def test_the_check_is_case_insensitive(self, vocabulary):
        """panopticas recased eslint -> ESLint between releases, and LIKE is
        case-insensitive anyway, so a case-sensitive gate would reject a
        spelling that would have matched."""
        vocabulary("ESLint")
        kd = KospexData(kospex_db=_seed_db())
        kd.where_tag("eslint")

        assert kd.get_bind_parameters() == ["%|eslint|%"]

    def test_the_callers_spelling_is_kept_not_canonicalised(self, vocabulary):
        vocabulary("ESLint")
        kd = KospexData(kospex_db=_seed_db())
        kd.where_tag("ESLINT")

        assert kd.get_bind_parameters() == ["%|ESLINT|%"]

    def test_a_retired_tag_is_rejected_with_the_rest(self, vocabulary):
        """Documents the known limitation: get_tags() reports only the current
        vocabulary, so a renamed tag is indistinguishable from a typo until
        panopticas exposes its deprecations."""
        vocabulary("Claude")
        kd = KospexData(kospex_db=_seed_db())

        with pytest.raises(ValueError):
            kd.where_tag("Claude Code")


class TestThroughProduction:
    """Drive get_metadata_files(), the only caller, against a real DB."""

    @pytest.mark.parametrize(
        "tag,expected",
        [
            ("C#", ["app.cs"]),                       # '#', was rejected
            ("Azure DevOps", ["azure-pipelines.yml"]),  # space, was rejected
            ("llms.txt", ["llms.txt"]),               # '.', was rejected
            ("Python", ["main.py"]),                  # always worked
        ],
    )
    def test_every_real_tag_is_queryable(self, tag, expected):
        kq = KospexQuery(kospex_db=_seed_db())

        rows = kq.get_metadata_files(tag=tag)

        assert sorted(r["Filename"] for r in rows) == sorted(expected)

    def test_a_recased_tag_still_finds_rows_stored_in_the_old_spelling(self):
        """ESLint is current; the row holds `eslint`. LIKE is case-insensitive,
        so the recasing costs nothing once the gate allows the query through."""
        kq = KospexQuery(kospex_db=_seed_db())

        rows = kq.get_metadata_files(tag="ESLint")

        assert [r["Filename"] for r in rows] == [".eslintrc"]

    def test_a_wildcard_tag_does_not_match_its_neighbour(self, vocabulary):
        vocabulary("A_B")
        kq = KospexQuery(kospex_db=_seed_db())

        rows = kq.get_metadata_files(tag="A_B")

        assert [r["Filename"] for r in rows] == ["a.conf"], "the `_` acted as a wildcard"

    def test_a_partial_tag_does_not_match(self, vocabulary):
        """The pipe delimiters are what make the match exact; keep them."""
        vocabulary("Pytho")
        kq = KospexQuery(kospex_db=_seed_db())

        assert kq.get_metadata_files(tag="Pytho") == []

    def test_a_duplicated_tag_in_one_row_is_still_one_row(self, vocabulary):
        """panopticas can return the same tag twice for one file
        (`.github/dependabot.yml` -> GitHub twice), so it is stored twice. The
        LIKE lookup is boolean per row, so the row is matched once."""
        vocabulary("GitHub")
        db = _seed_db()
        db[KospexSchema.TBL_FILE_METADATA].insert({
            "Provider": ".github/dependabot.yml", "hash": "h1",
            "_repo_id": "github.com~acme~five", "Filename": "dependabot.yml",
            "tech_type": "|Dependabot|GitHub|dependencies|security|GitHub|Git|",
            "latest": 1,
        }, pk=["Provider", "hash", "_repo_id"])

        rows = KospexQuery(kospex_db=db).get_metadata_files(tag="GitHub")

        assert [r["Filename"] for r in rows] == ["dependabot.yml"]


# Payloads for the CWE-89 cases below. Each targets a different mechanism:
# breaking out of the quoted literal, terminating the statement, appending a
# second statement, and smuggling a comment.
_PAYLOADS = [
    "Python' OR '1'='1",
    "x' OR 1=1 --",
    "x'; DROP TABLE file_metadata; --",
    "x' UNION SELECT 1, 2, 3 --",
    "%' --",
]


class TestInjection:
    """CWE-89: a caller-supplied tag must never be able to alter the statement.

    Read the two halves together, because the obvious test here is a test of
    nothing. In production a payload is rejected by the *vocabulary* check long
    before it reaches the pattern, so asserting `pytest.raises(ValueError)` on a
    payload passes identically whether the value is bound or formatted straight
    into the SQL with an f-string -- it would keep passing after a regression
    that reintroduced the defect.

    So the first test pins where production actually stops a payload, and the
    rest patch the vocabulary to *admit* each payload, which is the only way to
    exercise the binding itself.
    """

    @pytest.mark.parametrize("payload", _PAYLOADS)
    def test_in_production_a_payload_is_stopped_by_the_vocabulary(self, payload):
        """Not the injection defence -- the outer layer, pinned so the tests
        below are understood as being about the binding underneath it."""
        kd = KospexData(kospex_db=_seed_db())

        with pytest.raises(ValueError, match="is not a known tag"):
            kd.where_tag(payload)

    @pytest.mark.parametrize("payload", _PAYLOADS)
    def test_an_admitted_payload_never_reaches_the_sql_text(self, vocabulary, payload):
        """The assertion that fails if the f-string ever comes back."""
        vocabulary(payload)
        kd = KospexData(kospex_db=_seed_db())
        kd.from_table(KospexSchema.TBL_FILE_METADATA)
        kd.select("*")
        kd.where_tag(payload)

        sql = kd.generate_sql()

        assert payload not in sql, f"payload reached the SQL text: {sql}"
        assert "'" not in sql.split("ESCAPE")[0], (
            f"a quote appeared in the clause ahead of ESCAPE: {sql}")
        assert kd.where_clause == [r"tech_type LIKE ? ESCAPE '\'"]
        assert len(kd.get_bind_parameters()) == 1

    @pytest.mark.parametrize("payload", _PAYLOADS)
    def test_an_admitted_payload_matches_nothing_and_destroys_nothing(
            self, vocabulary, payload):
        """Executed, not just generated: the payload is a literal to match
        against, so it matches no tag and leaves the schema and rows alone."""
        vocabulary(payload)
        db = _seed_db()
        before = db[KospexSchema.TBL_FILE_METADATA].count

        kd = KospexData(kospex_db=db)
        kd.from_table(KospexSchema.TBL_FILE_METADATA)
        kd.select("*")
        kd.where_tag(payload)
        rows = kd.execute()

        assert rows == []
        tables = [r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        assert KospexSchema.TBL_FILE_METADATA in tables, "the table was dropped"
        assert db[KospexSchema.TBL_FILE_METADATA].count == before

    def test_a_quote_in_a_tag_is_data_and_still_matches(self, vocabulary):
        """The complement of the above: a quote is not merely rejected, it is
        carried through as a value. If the quote were escaping into the SQL this
        would error or return the wrong rows rather than match its own row."""
        vocabulary("O'Brien")
        db = _seed_db()
        db[KospexSchema.TBL_FILE_METADATA].insert({
            "Provider": "quoted.txt", "hash": "h1",
            "_repo_id": "github.com~acme~six", "Filename": "quoted.txt",
            "tech_type": "|O'Brien|", "latest": 1,
        }, pk=["Provider", "hash", "_repo_id"])

        rows = KospexQuery(kospex_db=db).get_metadata_files(tag="O'Brien")

        assert [r["Filename"] for r in rows] == ["quoted.txt"]
