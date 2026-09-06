"""The four columns recording what a dependency declared and when it was checked (#187)."""


def _fresh_home(tmp_path, monkeypatch):
    """Point kospex at an empty KOSPEX_HOME.

    The HabitatConfig singleton caches the resolved paths, so setting the env
    var is not enough — it must be reset. Matches tests/test_kospex_schema.py.
    """
    from kospex.habitat_config import HabitatConfig
    monkeypatch.setenv("KOSPEX_HOME", str(tmp_path))
    HabitatConfig.reset_instance()


NEW_COLUMNS = ("version_kind", "version_operator", "resolved_version", "last_checked")


class TestSchema:
    def test_fresh_db_has_the_columns(self, tmp_path, monkeypatch):
        """A clean install bootstraps the migrations, so the columns exist."""
        import kospex_schema as KospexSchema
        _fresh_home(tmp_path, monkeypatch)

        db = KospexSchema.connect_or_create_kospex_db()

        cols = [c[1] for c in db.execute("PRAGMA table_info(dependency_data)").fetchall()]
        for name in NEW_COLUMNS:
            assert name in cols, f"{name} missing from a freshly created DB"

    def test_baseline_create_table_does_not_declare_them(self):
        """The migration owns these columns, not the frozen baseline.

        SQL_CREATE_DEPENDENCY_DATA is frozen at KOSPEX_DB_VERSION = 2 and does
        not carry `resolution` either, though 0005 added it. A fresh DB runs the
        baseline CREATE and then bootstraps migrations, so declaring a column in
        both makes ALTER TABLE fail with `duplicate column name` on every clean
        install. This test is the guard against someone "fixing" the baseline.
        """
        import kospex_schema as KospexSchema

        for name in NEW_COLUMNS:
            assert name not in KospexSchema.SQL_CREATE_DEPENDENCY_DATA, (
                f"{name} must be added by migration 0006 only, not the baseline"
            )


import contextlib
import io


def _kdeps():
    from kospex_dependencies import KospexDependencies
    kd = KospexDependencies()
    kd.depsdev_record = lambda pt, pn, pv: {
        "package_name": pn, "package_version": pv, "package_type": pt,
        "versions_behind": 1, "advisories": 0, "resolution": "resolved",
        "published_at": "", "source_repo": "",
    }
    kd.get_pypi_source_repo = lambda n: ""
    return kd


def _assess(kd, path):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        return kd.assess(path) or []


class TestAssessPathPopulates:
    def test_npm_caret_is_classified(self, tmp_path):
        kd = _kdeps()
        p = tmp_path / "package.json"
        p.write_text('{"name":"d","dependencies":{"express":"^4.18.0"}}')

        rec = _assess(kd, str(p))[0]

        assert rec["version_kind"] == "caret"
        assert rec["version_operator"] == "^"

    def test_resolved_version_records_the_floor(self, tmp_path):
        """A range's advisories describe the floor, so the row has to say so."""
        kd = _kdeps()
        p = tmp_path / "package.json"
        p.write_text('{"name":"d","dependencies":{"express":"^4.18.0"}}')

        rec = _assess(kd, str(p))[0]

        assert rec["package_version"] == "^4.18.0"   # declared text preserved
        assert rec["resolved_version"] == "4.18.0"   # what deps.dev was asked

    def test_go_pseudo_version_is_a_commit_pin(self, tmp_path):
        kd = _kdeps()
        p = tmp_path / "go.mod"
        p.write_text("module x\nrequire github.com/a/b v0.0.0-20230828082145-3c4c8a2d2371\n")

        rec = _assess(kd, str(p))[0]

        assert rec["version_kind"] == "commit"

    def test_last_checked_is_set(self, tmp_path):
        kd = _kdeps()
        p = tmp_path / "package.json"
        p.write_text('{"name":"d","dependencies":{"express":"4.18.0"}}')

        rec = _assess(kd, str(p))[0]

        assert rec["last_checked"], "last_checked must be stamped on every save"
        assert rec["last_checked"].startswith("20")   # ISO-ish timestamp

    def test_pnpm_transitive_skips_lookup_but_still_classifies(self):
        """A pnpm-lock transitive entry never asks deps.dev (#178's skip_lookup
        branch), so resolved_version must stay "" — but classification and the
        last_checked stamp don't depend on the lookup having happened.
        """
        from kospex.extractors.registry import classify

        kd = _kdeps()
        extractor = classify("pnpm-lock.yaml").extractor
        records = [{
            "package_name": "lodash",
            "package_version": "4.17.21",
            "requirements_type": "resolved",  # transitive, not direct/dev
        }]

        rec = kd._enrich_dependency_records(records, extractor)[0]

        assert rec["resolved_version"] == ""
        assert rec["version_kind"] == "pinned"
        assert rec["version_operator"] == ""
        assert rec["last_checked"], "last_checked must be stamped even when the lookup is skipped"


class TestOsiPathPopulates:
    """Both write paths must populate these. Testing one and assuming the other
    is how the Go `v`-prefix defect reached production twice."""

    def _db(self):
        import sqlite_utils
        import kospex_schema as KospexSchema
        db = sqlite_utils.Database(memory=True)
        db.execute(KospexSchema.SQL_CREATE_DEPENDENCY_DATA)
        # SQL_CREATE_DEPENDENCY_DATA is the frozen baseline (KOSPEX_DB_VERSION
        # = 2); these four columns are added by migration 0006. A real DB
        # save_dependencies() writes to has always run that migration (new
        # DBs bootstrap it, existing ones are nagged until `upgrade-db
        # -apply`), so apply it here rather than testing against a schema
        # save_dependencies() would never actually see in production.
        for col in NEW_COLUMNS:
            db.execute(f"ALTER TABLE dependency_data ADD COLUMN {col} TEXT")
        return db

    def _record(self, version="^4.18.0", name="express"):
        return {
            "_repo_id": "s~o~r", "hash": "h1", "file_path": "package.json",
            "package_type": "npm", "package_name": name,
            "package_version": version, "requirements_type": "direct",
        }

    def test_save_dependencies_classifies(self):
        from kospex_dependencies import KospexDependencies
        db = self._db()
        kd = KospexDependencies(kospex_db=db)

        kd.save_dependencies([self._record()], source="test")

        row = next(db.query("SELECT * FROM dependency_data"))
        assert row["version_kind"] == "caret"
        assert row["version_operator"] == "^"
        assert row["last_checked"]

    def test_last_checked_updates_on_re_save(self):
        """The precise bug created_at has: a DB DEFAULT is set on INSERT and
        never again, so a row refreshed from deps.dev keeps its original date."""
        import time
        from kospex_dependencies import KospexDependencies
        db = self._db()
        kd = KospexDependencies(kospex_db=db)

        kd.save_dependencies([self._record()], source="test")
        first = next(db.query("SELECT last_checked FROM dependency_data"))["last_checked"]

        time.sleep(1.1)   # second precision
        kd.save_dependencies([self._record()], source="test")
        second = next(db.query(
            "SELECT last_checked FROM dependency_data WHERE latest=1"))["last_checked"]

        assert second > first, (
            f"last_checked did not advance on re-save ({first} -> {second}); "
            "it must be written explicitly, not left to a column default"
        )


class TestRequirementsRealignment:
    """requirements.txt was the only parser splitting the operator out of
    package_version, so `flask>=2.0` stored `2.0` — indistinguishable from a
    pin. Source files are 53% pinned while the stored data read 94% bare."""

    def test_operator_stays_in_package_version(self, tmp_path):
        kd = _kdeps()
        p = tmp_path / "requirements.txt"
        p.write_text("flask>=2.0\n")

        rec = _assess(kd, str(p))[0]

        assert rec["package_version"] == ">=2.0"
        assert rec["version_kind"] == "gte"
        assert rec["version_operator"] == ">="

    def test_exact_pin_is_unchanged(self, tmp_path):
        kd = _kdeps()
        p = tmp_path / "requirements.txt"
        p.write_text("click==8.1\n")

        rec = _assess(kd, str(p))[0]

        assert rec["package_version"] == "==8.1"
        assert rec["version_kind"] == "pinned"

    def test_matches_pyproject_for_the_same_declaration(self, tmp_path):
        """The point of the realignment: one declaration, one representation."""
        kd = _kdeps()
        (tmp_path / "requirements.txt").write_text("flask>=2.0\n")
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname="d"\ndependencies=["flask>=2.0"]\n')

        req = _assess(kd, str(tmp_path / "requirements.txt"))[0]
        proj = _assess(kd, str(tmp_path / "pyproject.toml"))[0]

        assert req["package_version"] == proj["package_version"] == ">=2.0"

    def test_tilde_equals_resolves_a_real_lookup_version(self, tmp_path):
        """`~=` (PEP 440 compatible-release) was the regression this change

        introduced: assess() feeds package_version into clean_version_spec()
        for the deps.dev lookup and resolved_version. Once package_version
        kept the operator, `~=2.3.3` hit extract_version_from_constraint's
        caret/tilde fallback (`startswith("~")`) before its comparison-
        operator list even ran, stripping only the tilde and leaving the
        corrupt lookup version `=2.3.3`. resolved_version must be the bare
        version, exactly as it is for every other operator.
        """
        kd = _kdeps()
        p = tmp_path / "requirements.txt"
        p.write_text("numpy~=2.3.3\n")

        rec = _assess(kd, str(p))[0]

        assert rec["package_version"] == "~=2.3.3"
        assert rec["version_kind"] == "tilde"
        assert rec["version_operator"] == "~="
        assert rec["resolved_version"] == "2.3.3"
