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
