"""The behind-migrations banner must appear on every executed subcommand.

It goes to stderr, never blocks, and never changes an exit code. Click 8.3
separates stderr from stdout on CliRunner results by default.
"""
import pytest
from click.testing import CliRunner


def _behind_db(tmp_path, monkeypatch):
    """A fully-built DB whose migrations are recorded as never applied.

    Built via connect_or_create_kospex_db() and then rewound, rather than
    hand-rolling two tables: the subcommands invoked below run real queries, so
    the DB needs its full schema. `pending()` depends only on the
    schema_migrations rows, so clearing them is what makes it read as behind.
    """
    import kospex_schema as KospexSchema
    from kospex.habitat_config import HabitatConfig
    monkeypatch.setenv("KOSPEX_HOME", str(tmp_path))
    HabitatConfig.reset_instance()

    db = KospexSchema.connect_or_create_kospex_db()
    db.execute("DELETE FROM schema_migrations")
    db.execute(
        "UPDATE kospex_config SET value = '2' WHERE key = ?",
        [KospexSchema.KOSPEX_DB_VERSION_KEY],
    )
    db.conn.commit()
    return db


def test_banner_shows_on_a_kospex_subcommand(tmp_path, monkeypatch):
    import kospex_cli
    db = _behind_db(tmp_path, monkeypatch)
    monkeypatch.setattr(kospex_cli.kospex, "kospex_db", db)

    result = CliRunner().invoke(kospex_cli.cli, ["list-repos", "-db"])

    assert "DATABASE SCHEMA IS OUT OF DATE" in result.stderr


def test_banner_goes_to_stderr_not_stdout(tmp_path, monkeypatch):
    """stdout must stay clean — `kospex list-repos -out x.csv` pipes it."""
    import kospex_cli
    db = _behind_db(tmp_path, monkeypatch)
    monkeypatch.setattr(kospex_cli.kospex, "kospex_db", db)

    result = CliRunner().invoke(kospex_cli.cli, ["list-repos", "-db"])

    assert "DATABASE SCHEMA IS OUT OF DATE" not in result.stdout


def test_banner_suppressed_by_quiet(tmp_path, monkeypatch):
    import kospex_cli
    db = _behind_db(tmp_path, monkeypatch)
    monkeypatch.setattr(kospex_cli.kospex, "kospex_db", db)

    result = CliRunner().invoke(kospex_cli.cli, ["--quiet", "list-repos", "-db"])

    assert "DATABASE SCHEMA IS OUT OF DATE" not in result.stderr


def test_no_banner_on_help(tmp_path, monkeypatch):
    """--help exits during parsing, before the group callback runs."""
    import kospex_cli
    db = _behind_db(tmp_path, monkeypatch)
    monkeypatch.setattr(kospex_cli.kospex, "kospex_db", db)

    result = CliRunner().invoke(kospex_cli.cli, ["--help"])

    assert "DATABASE SCHEMA IS OUT OF DATE" not in result.stderr
    assert "DATABASE SCHEMA IS OUT OF DATE" not in result.stdout


def test_banner_does_not_change_the_exit_code(tmp_path, monkeypatch):
    """It warns; it does not gate."""
    import kospex_cli
    db = _behind_db(tmp_path, monkeypatch)
    monkeypatch.setattr(kospex_cli.kospex, "kospex_db", db)

    result = CliRunner().invoke(kospex_cli.cli, ["list-repos", "-db"])

    assert result.exit_code == 0


@pytest.mark.parametrize(
    "module_name,subcommand",
    [("kgit", "status"), ("krunner", "repos"), ("kreaper", "repos")],
)
def test_banner_shows_on_the_other_clis(module_name, subcommand, tmp_path, monkeypatch):
    """`group subcmd --help` runs the group callback then exits cleanly.

    Verified against Click 8.3.1: the group callback runs for
    ['subcmd', '--help'] but not for ['--help']. That lets each CLI's callback
    be exercised without constructing valid arguments for its subcommand.
    """
    import importlib
    module = importlib.import_module(module_name)
    db = _behind_db(tmp_path, monkeypatch)
    monkeypatch.setattr(module.kospex, "kospex_db", db)

    result = CliRunner().invoke(module.cli, [subcommand, "--help"])

    assert "DATABASE SCHEMA IS OUT OF DATE" in result.stderr
    assert "DATABASE SCHEMA IS OUT OF DATE" not in result.stdout


@pytest.mark.parametrize("module_name", ["kgit", "krunner", "kreaper"])
def test_no_banner_on_group_help_for_the_other_clis(module_name, tmp_path, monkeypatch):
    import importlib
    module = importlib.import_module(module_name)
    db = _behind_db(tmp_path, monkeypatch)
    monkeypatch.setattr(module.kospex, "kospex_db", db)

    result = CliRunner().invoke(module.cli, ["--help"])

    assert "DATABASE SCHEMA IS OUT OF DATE" not in result.stdout
    assert "DATABASE SCHEMA IS OUT OF DATE" not in result.stderr
