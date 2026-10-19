"""An un-enriched row must read as unknown, not as vulnerable (#258).

`dependency_data.advisories` and `.versions_behind` are INTEGER columns that
received `''`. SQLite's type ordering sorts TEXT above every INTEGER regardless
of content, so `'' > 0` is **true** and an un-enriched row matched the
"vulnerable" predicate while being in none of the three buckets a reader would
consider exhaustive (`> 0`, `= 0`, `IS NULL`).

Two layers then disagreed about the same row: SQL `WHERE advisories > 0` called
it vulnerable, while `kweb2.py`'s `item.get("advisories") or 0` coalesced the
falsy `''` to 0 and rendered it clean. Neither is right -- the honest answer is
unknown, which is what NULL means.

`save_dependencies()` already carried a guard for this, and it could not fire:
`setdefault` only fills a *missing* key, and every record template supplied the
key already set to `''`. So the fix is in two places, and both are tested here
-- the templates stop producing `''`, and the save path coerces it away for any
future caller that reintroduces it.
"""
import pytest
import sqlite_utils

import kospex_schema as KospexSchema

REPO = "github.com~acme~a"

# The two columns the issue is about. Kept as a name so the tests below read as
# statements about the pair rather than about one column twice.
ADVISORY_COLUMNS = ("advisories", "versions_behind")


def _db():
    """Every baseline table, then the shipped migrations -- as a real DB is built.

    Not a hand-rolled `CREATE TABLE` plus the columns this test happens to want:
    `save_dependencies()` writes every column, and the point of the invariant
    test below is to notice a column this file does not know about.
    """
    from kospex.db.migrator import Migrator
    db = sqlite_utils.Database(memory=True)
    for create_sql in KospexSchema.DB_CREATE_STATEMENTS.values():
        db.execute(create_sql)
    Migrator(db).apply_pending()
    return db


@pytest.fixture
def kdeps():
    from kospex_dependencies import KospexDependencies
    db = _db()
    return KospexDependencies(kospex_db=db), db


def _extractor_shaped_record(kdeps):
    """A record exactly as an extractor hands it over: parsed, never enriched.

    Built from `get_package_template()` rather than written out here, because
    the defect was that the template supplies the key -- a hand-built dict that
    omits it lets `setdefault` fire and the save looks correct. The existing
    `test_advisory_data_does_not_survive_an_unenriched_re_save` passes today for
    exactly that reason.
    """
    rec = kdeps.get_package_template()
    rec.update({
        "_repo_id": REPO, "hash": "h1", "file_path": "package.json",
        "package_type": "npm", "package_name": "express",
        "package_version": "4.18.0",
    })
    return rec


def _stored(db, column):
    row = next(iter(db.query(
        f"SELECT [{column}] AS value, typeof([{column}]) AS type "
        "FROM dependency_data WHERE latest = 1")))
    return row["value"], row["type"]


# --- the templates -----------------------------------------------------------

def test_get_package_template_advisory_columns_are_null():
    """The sixth copy of the template, and the one the save path goes through.

    The five extractors each hold their own copy, asserted in their own test
    modules; this is the shared one in KospexDependencies.
    """
    from kospex_dependencies import KospexDependencies
    template = KospexDependencies().get_package_template()

    for column in ADVISORY_COLUMNS:
        assert template[column] is None, (
            f"{column} is {template[column]!r}; '' lands in an INTEGER column "
            "as TEXT and compares greater than every integer")


# --- the save path -----------------------------------------------------------

def test_an_unenriched_record_stores_null_not_an_empty_string(kdeps):
    """The defect, through the production save path.

    Asserts `typeof()`, not the Python value: the Python value and the stored
    value are the same object here, and the *storage type* is the defect. A
    test comparing `row["advisories"] == ""` would pass on a fixed database
    too, because NULL and '' are both falsy on the way out.
    """
    kd, db = kdeps

    kd.save_dependencies([_extractor_shaped_record(kd)], source="test")

    for column in ADVISORY_COLUMNS:
        value, stored_type = _stored(db, column)
        assert stored_type == "null", (
            f"{column} stored as {stored_type} ({value!r}); an INTEGER column "
            "holding TEXT sorts above every integer")


def test_an_unenriched_row_is_unknown_to_every_reader(kdeps):
    """`> 0`, `= 0` and `IS NULL` must partition the rows.

    This is the consequence that made the issue worth filing rather than the
    mechanism: the row was in none of the three, and the predicate it *did*
    match was the one meaning "has an advisory". On a ~370-repository estate
    that overstated `WHERE advisories > 0` by 35%.
    """
    kd, db = kdeps

    kd.save_dependencies([_extractor_shaped_record(kd)], source="test")

    for column in ADVISORY_COLUMNS:
        buckets = {
            pred: next(iter(db.query(
                f"SELECT count(*) AS n FROM dependency_data "
                f"WHERE [{column}] {pred}")))["n"]
            for pred in ("> 0", "= 0", "IS NULL")
        }
        assert buckets == {"> 0": 0, "= 0": 0, "IS NULL": 1}, (
            f"{column}: an un-enriched row should be only IS NULL, got {buckets}")


def test_a_caller_supplied_empty_string_is_coerced(kdeps):
    """The second line of defence, independent of the templates.

    `setdefault` could not defend a key that is always present. The save path
    now coerces `''` away, so a future template regression -- or any caller
    building a record by hand -- cannot reopen this. Tested separately from the
    templates so that reverting one does not hide the other.
    """
    kd, db = kdeps
    rec = _extractor_shaped_record(kd)
    for column in ADVISORY_COLUMNS:
        rec[column] = ""

    kd.save_dependencies([rec], source="test")

    for column in ADVISORY_COLUMNS:
        value, stored_type = _stored(db, column)
        assert stored_type == "null", (
            f"{column} stored as {stored_type} ({value!r}) -- the save path "
            "must coerce an empty string even when a caller supplies one")


def test_a_real_count_of_zero_is_not_turned_into_unknown(kdeps):
    """0 means "we asked and found none" and must survive.

    The coercion targets `''` only. Were it written as a falsy check, an
    enriched row reporting zero advisories would become NULL, which is the same
    class of defect in the opposite direction.
    """
    kd, db = kdeps
    rec = _extractor_shaped_record(kd)
    rec["advisories"] = 0
    rec["versions_behind"] = 0

    kd.save_dependencies([rec], source="test")

    for column in ADVISORY_COLUMNS:
        value, stored_type = _stored(db, column)
        assert (value, stored_type) == (0, "integer"), (
            f"{column} stored as {value!r} ({stored_type}); a measured zero is "
            "not unknown")


# --- the invariant -----------------------------------------------------------

def test_no_integer_column_holds_text(kdeps):
    """No `dependency_data` row may hold TEXT in an INTEGER column.

    Derived from the live schema with PRAGMA rather than a list written here,
    so the next column to acquire this defect fails without anyone remembering
    to extend the test. `advisories` and `versions_behind` were not the first:
    `is_dev`, `is_optional` and `runs_install_script` got it right two lines
    away in the same template, and said why.
    """
    kd, db = kdeps

    kd.save_dependencies([_extractor_shaped_record(kd)], source="test")

    integer_columns = [
        row["name"] for row in db.query("PRAGMA table_info(dependency_data)")
        if (row["type"] or "").upper() == "INTEGER"
    ]
    assert integer_columns, "found no INTEGER columns -- the PRAGMA shape changed"

    offenders = {}
    for column in integer_columns:
        bad = next(iter(db.query(
            f"SELECT count(*) AS n FROM dependency_data "
            f"WHERE typeof([{column}]) = 'text'")))["n"]
        if bad:
            offenders[column] = bad

    assert not offenders, (
        f"INTEGER columns holding TEXT: {offenders}. TEXT sorts above every "
        "INTEGER in SQLite, so such a value reads as the largest.")
