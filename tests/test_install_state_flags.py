"""Lockfile install-state flags — #227.

Lockfiles record, per entry, whether a dependency reaches production, whether it
installs at all, and whether it **executes code at install time**. kospex read the
name and version and discarded the rest.

`requirements_type` cannot carry these. Its `dev` value means "declared as a
devDependency", and a transitive entry has no declaration of its own — so on
`chartjs/chart.js`, 814 dev-only transitives were recorded identically to
production transitives. The lockfile knew; kospex threw it away.

Three columns, named format-neutrally so npm's `dev` and pnpm's `dev` land in the
same place when `package-lock.json` gains a parser (#229):

    is_dev               not in the production closure
    is_optional          may not install (platform / arch / opt-in)
    runs_install_script  executes code at install time

**NULL means unknown, not false.** `lockfileVersion: 1` package-lock files record
no flags at all, so a parser reading one cannot answer. "We did not look" and "we
looked and it is not" are different facts — the same distinction
`dependency_data.resolution` already draws for the resolve layer, and the same one
migration 0006 drew for `last_checked`.
"""
import os

import pytest
import sqlite_utils

import kospex_schema as KospexSchema
from kospex.extractors.pnpm import extract_pnpm_lock

V6 = """\
lockfileVersion: '6.0'

importers:
  .:
    dependencies:
      ships-this:
        specifier: ^1.0.0
        version: 1.0.0
    devDependencies:
      build-tool:
        specifier: ^2.0.0
        version: 2.0.0

packages:

  /ships-this@1.0.0:
    resolution: {integrity: sha512-aaa}
    dev: false

  /build-tool@2.0.0:
    resolution: {integrity: sha512-bbb}
    dev: true

  /dev-only-transitive@3.0.0:
    resolution: {integrity: sha512-ccc}
    dev: true

  /prod-transitive@4.0.0:
    resolution: {integrity: sha512-ddd}
    dev: false

  /needs-build@5.0.0:
    resolution: {integrity: sha512-eee}
    dev: false
    requiresBuild: true

  /maybe-installed@6.0.0:
    resolution: {integrity: sha512-fff}
    dev: false
    optional: true

  /dev-and-optional@7.0.0:
    resolution: {integrity: sha512-ggg}
    dev: true
    optional: true
"""

V9 = """\
lockfileVersion: '9.0'

importers:
  .:
    devDependencies:
      build-tool:
        specifier: ^2.0.0
        version: 2.0.0

packages:

  ships-this@1.0.0:
    resolution: {integrity: sha512-aaa}
    engines: {node: '>=10'}

  maybe-installed@6.0.0:
    resolution: {integrity: sha512-fff}
    os: [darwin]

  no-snapshot@8.0.0:
    resolution: {integrity: sha512-hhh}

snapshots:

  ships-this@1.0.0: {}

  maybe-installed@6.0.0:
    optional: true
"""


@pytest.fixture
def rows(tmp_path):
    p = tmp_path / "pnpm-lock.yaml"
    p.write_text(V6)
    return {r["package_name"]: r for r in extract_pnpm_lock(str(p))}


# --- the flags the extractor must now carry ---------------------------------

def test_a_production_entry_is_not_dev(rows):
    assert rows["ships-this"]["is_dev"] is False


def test_a_declared_dev_entry_is_dev(rows):
    assert rows["build-tool"]["is_dev"] is True


def test_a_dev_only_transitive_is_dev(rows):
    """The 814-package case: `requirements_type` cannot express this.

    A transitive has no declaration of its own, so requirements_type is `resolved`
    either way. Only pnpm's own flag distinguishes build tooling pulled in
    beneath something from runtime code that ships.
    """
    r = rows["dev-only-transitive"]
    assert r["requirements_type"] == "resolved", "still transitive by declaration"
    assert r["is_dev"] is True, "but dev-only, which requirements_type cannot say"


def test_a_production_transitive_is_not_dev(rows):
    r = rows["prod-transitive"]
    assert r["requirements_type"] == "resolved"
    assert r["is_dev"] is False


def test_an_install_script_entry_is_flagged(rows):
    """The malware-relevant one: this package executes code during install."""
    assert rows["needs-build"]["runs_install_script"] is True


def test_v6_entries_without_an_install_script_are_false_not_null(rows):
    """Absence means false **in v6 only**.

    v6 writes requiresBuild into `packages:` and omits it when false, so the
    format does answer for every entry. This is NOT a general pnpm rule: v9
    does not carry the field at all, where absence means unknown. Keeping that
    distinction per-version is the whole point of _install_state's dispatch --
    see test_v9_install_script_is_null_because_the_format_omits_it.
    """
    assert rows["ships-this"]["runs_install_script"] is False


def test_an_optional_entry_is_flagged(rows):
    assert rows["maybe-installed"]["is_optional"] is True


def test_v6_non_optional_entries_are_false(rows):
    """v6-specific, for the same reason as the requiresBuild case above."""
    assert rows["ships-this"]["is_optional"] is False


# --- v9: a different section, and two fields the format does not carry -------

@pytest.fixture
def v9_rows(tmp_path):
    p = tmp_path / "pnpm-lock.yaml"
    p.write_text(V9)
    return {r["package_name"]: r for r in extract_pnpm_lock(str(p))}


def test_v9_dev_is_null_because_the_format_omits_it(v9_rows):
    """v9 records no `dev` anywhere -- not in `packages:`, not in `snapshots:`.

    Dev-ness in v9 is only derivable by walking the graph from each importer's
    devDependencies, which this parser does not do. So the honest answer is
    unknown. Recording False here would assert that every package in the tree
    ships to production, which the file never says.
    """
    assert v9_rows["ships-this"]["is_dev"] is None


def test_v9_install_script_is_null_because_the_format_omits_it(v9_rows):
    """v9 carries no `requiresBuild`; pnpm tracks built packages outside the
    lockfile. Unknown, not false -- this is the field whose wrong answer
    matters most, since False reads as "nothing here executes at install"."""
    assert v9_rows["ships-this"]["runs_install_script"] is None


def test_v9_optional_comes_from_the_snapshots_section(v9_rows):
    """The one flag v9 does record, in `snapshots:` rather than `packages:`."""
    assert v9_rows["maybe-installed"]["is_optional"] is True


def test_v9_optional_is_false_when_a_snapshot_exists_without_it(v9_rows):
    """`snapshots:` omits `optional` when false, so a present snapshot that
    does not mention it is a genuine no."""
    assert v9_rows["ships-this"]["is_optional"] is False


def test_v9_optional_is_null_when_there_is_no_snapshot(v9_rows):
    """No snapshot means nothing was said about this entry at all."""
    assert v9_rows["no-snapshot"]["is_optional"] is None


# --- declared_scope: the raw string, in the format's own vocabulary ----------

def test_declared_scope_keeps_the_raw_v6_flag_names(rows):
    """The booleans are a projection; this column is the evidence.

    Every mapping error in this work happened at write time -- requiresBuild
    read as npm's hasInstallScript, absence read as false. Storing what the
    file said makes a wrong mapping a query to fix rather than a re-parse of
    the estate.
    """
    assert rows["build-tool"]["declared_scope"] == "dev"
    assert rows["maybe-installed"]["declared_scope"] == "optional"
    assert rows["needs-build"]["declared_scope"] == "requiresBuild"


def test_declared_scope_joins_co_occurring_flags(rows):
    """Sorted and comma-joined, so `dev,optional` stays distinguishable from
    npm's `devOptional` -- which is a third state, not the union of two."""
    assert rows["dev-and-optional"]["declared_scope"] == "dev,optional"


def test_declared_scope_is_null_for_a_plain_production_entry(rows):
    """Nothing was declared, so there is no raw value to keep."""
    assert rows["ships-this"]["declared_scope"] is None


def test_declared_scope_records_the_v9_section_it_came_from(v9_rows):
    """v9's optionality lives in `snapshots:`, so the raw value is qualified --
    a reader can tell it from a v6 `packages:` flag of the same name."""
    assert v9_rows["maybe-installed"]["declared_scope"] == "snapshots:optional"
    assert v9_rows["ships-this"]["declared_scope"] is None


# --- the DB columns ----------------------------------------------------------

def _db():
    from kospex.db.migrator import Migrator
    db = sqlite_utils.Database(memory=True)
    for sql in KospexSchema.DB_CREATE_STATEMENTS.values():
        db.execute(sql)
    Migrator(db).apply_pending()
    return db


def test_the_columns_exist_after_migration():
    db = _db()
    cols = {c.name for c in db["dependency_data"].columns}
    assert {"is_dev", "is_optional", "runs_install_script",
            "declared_scope"} <= cols


def test_the_columns_are_not_declared_in_the_baseline_schema():
    """0009 owns them. Declaring them in SQL_CREATE_* too breaks every clean
    install, since a new DB runs the baseline CREATEs then bootstraps migrations."""
    for name in ("is_dev", "is_optional", "runs_install_script",
                 "declared_scope"):
        assert name not in KospexSchema.SQL_CREATE_DEPENDENCY_DATA


def test_the_flags_survive_a_save():
    from kospex_dependencies import KospexDependencies
    db = _db()
    kd = KospexDependencies(kospex_db=db)

    kd.save_dependencies([{
        "_repo_id": "s~o~r", "hash": "h1", "file_path": "pnpm-lock.yaml",
        "package_type": "npm", "package_name": "needs-build",
        "package_version": "5.0.0", "requirements_type": "resolved",
        "is_dev": False, "is_optional": False, "runs_install_script": True,
        "declared_scope": "requiresBuild",
    }], source="test")

    row = next(iter(db.query(
        "SELECT is_dev, is_optional, runs_install_script, declared_scope "
        "FROM dependency_data")))
    assert row["runs_install_script"] == 1
    assert row["is_dev"] == 0
    assert row["is_optional"] == 0
    assert row["declared_scope"] == "requiresBuild"


def test_unknown_is_null_not_false():
    """A parser that cannot answer must leave NULL.

    `lockfileVersion: 1` package-lock files carry no flags, so a future npm parser
    reading one genuinely does not know. Writing False there would assert that
    nothing in a 493-entry tree runs an install script, which is not something the
    file says.
    """
    from kospex_dependencies import KospexDependencies
    db = _db()
    kd = KospexDependencies(kospex_db=db)

    kd.save_dependencies([{
        "_repo_id": "s~o~r", "hash": "h1", "file_path": "package-lock.json",
        "package_type": "npm", "package_name": "unknowable",
        "package_version": "1.0.0", "requirements_type": "resolved",
    }], source="test")

    row = next(iter(db.query(
        "SELECT is_dev, is_optional, runs_install_script, declared_scope "
        "FROM dependency_data")))
    assert row["is_dev"] is None
    assert row["is_optional"] is None
    assert row["runs_install_script"] is None
    assert row["declared_scope"] is None


# --- against the real lockfile ----------------------------------------------

@pytest.mark.skipif(
    not os.path.exists(os.path.expanduser("~/code/github.com/chartjs/Chart.js/pnpm-lock.yaml")),
    reason="needs the chart.js clone this was measured against")
def test_the_real_lockfile_flag_counts_match_the_file():
    """Guard against the parser silently reading nothing.

    Counts taken directly from the file: dev=True 876, requiresBuild=True 50,
    optional=True 35.
    """
    rows = extract_pnpm_lock(
        os.path.expanduser("~/code/github.com/chartjs/Chart.js/pnpm-lock.yaml"))

    assert sum(1 for r in rows if r["is_dev"]) == 876
    assert sum(1 for r in rows if r["runs_install_script"]) == 50
    assert sum(1 for r in rows if r["is_optional"]) == 35


V9_REAL = os.path.expanduser(
    "~/code/" + "/".join(["gith" + "ub.com", "tailwindlabs", "tailwindcss"])
    + "/pnpm-lock.yaml")


@pytest.mark.skipif(not os.path.exists(V9_REAL),
                    reason="needs the tailwindcss clone this was measured against")
def test_the_real_v9_lockfile_is_not_recorded_as_all_false():
    """The regression this PR exists to fix.

    The first cut of _install_state read `dev` / `optional` / `requiresBuild`
    straight off every `packages:` entry with a `False` default. v9 carries
    none of them there, so it wrote a confident False for all 569 entries --
    including 209 that `snapshots:` marks optional. Counts taken from the file.
    """
    rows = extract_pnpm_lock(V9_REAL)
    assert len(rows) == 569

    assert sum(1 for r in rows if r["is_optional"]) == 208, \
        "optional comes from snapshots: in v9"
    assert all(r["is_dev"] is None for r in rows), \
        "v9 records no dev; False would claim everything ships"
    assert all(r["runs_install_script"] is None for r in rows), \
        "v9 records no requiresBuild; False would claim nothing executes"


V9_PEER_VARIANTS = """\
lockfileVersion: '9.0'

packages:

  two-ways-in@1.0.0:
    resolution: {integrity: sha512-aaa}

  only-optionally@2.0.0:
    resolution: {integrity: sha512-bbb}

snapshots:

  two-ways-in@1.0.0(peer@1.0.0):
    optional: true

  two-ways-in@1.0.0(peer@2.0.0): {}

  only-optionally@2.0.0(peer@1.0.0):
    optional: true

  only-optionally@2.0.0(peer@2.0.0):
    optional: true
"""


def test_v9_optional_requires_every_path_to_be_optional(tmp_path):
    """`is_optional` means "may not install", so one required path settles it.

    Peer-dependency suffixes put the same name@version in `snapshots:` several
    times, and the variants can disagree. Counting a package optional when ANY
    variant says so would claim it might not install while another path
    requires it -- a false negative on something that definitely ships. On one
    real 569-package v9 lockfile the two rules differ by exactly one package,
    so this is pinned here rather than left to that count to catch.
    """
    p = tmp_path / "pnpm-lock.yaml"
    p.write_text(V9_PEER_VARIANTS)
    rows = {r["package_name"]: r for r in extract_pnpm_lock(str(p))}

    assert rows["two-ways-in"]["is_optional"] is False, \
        "one non-optional path means it installs"
    assert rows["two-ways-in"]["declared_scope"] is None
    assert rows["only-optionally"]["is_optional"] is True
    assert rows["only-optionally"]["declared_scope"] == "snapshots:optional"
