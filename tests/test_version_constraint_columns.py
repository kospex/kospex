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
