"""Path precedence through the real entry points (#466).

Each CLI calls KospexUtils.init() and builds a module-level Kospex() as it is
imported, and the bug lived in that import-time path: init() wrote
KOSPEX_DB=~/kospex/kospex.db into os.environ before any command ran. So these
tests start a fresh interpreter per command rather than calling functions that
have already been imported, and check which database file was opened.

Every path is under tmp_path. KOSPEX_HOME is always set, so nothing here can
reach ~/kospex.
"""
import ast
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
import sqlite_utils

SRC = Path(__file__).resolve().parent.parent / "src"

MARKER_REPO = "example.com~acme~marker-466"

# (program, module, read-only argv that lists repos from the DB)
ENTRY_POINTS = [
    ("kospex", "kospex_cli", ["list-repos", "-db", "-repo_id"]),
    ("kgit", "kgit", ["pull", "--check", "--all"]),
    ("krunner", "krunner", ["repos"]),
]


# --- helpers -----------------------------------------------------------------


def _baseline_db(path: Path) -> sqlite_utils.Database:
    """Every baseline table and an empty schema_migrations: a DB that is
    migrations behind, as one created before the migration system was."""
    import kospex_schema as KospexSchema
    db = sqlite_utils.Database(path)
    for table, create_sql in KospexSchema.DB_CREATE_STATEMENTS.items():
        if table == KospexSchema.TBL_SCHEMA_MIGRATIONS:
            continue
        db.execute(create_sql)
    db.execute(
        "CREATE TABLE schema_migrations ("
        "id TEXT PRIMARY KEY, sequence INTEGER NOT NULL, checksum TEXT NOT NULL, "
        "applied_at TEXT NOT NULL, duration_ms INTEGER, has_python INTEGER NOT NULL)"
    )
    return db


def _db_with_marker_repo(path: Path, code_dir: Path) -> None:
    """A fully migrated DB holding one repo no other DB has."""
    from kospex.db.migrator import Migrator
    db = _baseline_db(path)
    Migrator(db).apply_pending()
    repo_dir = code_dir / "example.com" / "acme" / "marker-466"
    repo_dir.mkdir(parents=True, exist_ok=True)
    db["repos"].insert({
        "_repo_id": MARKER_REPO, "_git_server": "example.com",
        "_git_owner": "acme", "_git_repo": "marker-466",
        "git_remote": "https://example.com/acme/marker-466.git",
        "file_path": str(repo_dir), "last_sync": "2026-11-01T00:00:00",
    })
    db.conn.commit()
    db.conn.close()


def _applied(path: Path) -> int:
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
    finally:
        conn.close()


def _run(module, prog, argv, *, home, cwd, **env_vars):
    """Run `prog argv` in a fresh interpreter with only the given KOSPEX_* set."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("KOSPEX_")}
    env.update(KOSPEX_HOME=str(home), PYTHONPATH=str(SRC), COLUMNS="250",
               **{k: str(v) for k, v in env_vars.items()})
    script = (f"import sys; sys.argv = {[prog] + list(argv)!r}\n"
              f"from {module} import cli\n"
              "cli()\n")
    return subprocess.run([sys.executable, "-c", script], env=env, cwd=cwd,
                          capture_output=True, text=True, timeout=180)


@pytest.fixture
def habitat(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    code = tmp_path / "code"
    code.mkdir()
    return home, code


# --- every entry point opens the DB the settings name ------------------------


@pytest.mark.parametrize("where", ["environment", "kospex.env"])
@pytest.mark.parametrize("prog,module,argv", ENTRY_POINTS, ids=[e[0] for e in ENTRY_POINTS])
def test_entry_point_opens_the_configured_db(habitat, tmp_path, prog, module, argv, where):
    home, code = habitat
    target = tmp_path / "elsewhere.db"
    _db_with_marker_repo(target, code)

    env_vars = {"KOSPEX_CODE": code}
    if where == "environment":
        env_vars["KOSPEX_DB"] = target
    else:
        (home / "kospex.env").write_text(f"KOSPEX_DB={target}\n")

    result = _run(module, prog, argv, home=home, cwd=tmp_path, **env_vars)

    assert result.returncode == 0, result.stdout + result.stderr
    assert MARKER_REPO in result.stdout, result.stdout
    assert not (home / "kospex.db").exists(), "opened the default DB as well"


def test_upgrade_db_apply_migrates_the_named_copy_only(habitat, tmp_path):
    """The issue's scenario: an override set up to protect the real DB."""
    home, code = habitat
    real = home / "kospex.db"
    copy = tmp_path / "copy.db"
    _baseline_db(real).conn.close()
    _baseline_db(copy).conn.close()

    result = _run("kospex_cli", "kospex", ["upgrade-db", "-apply"], home=home,
                  cwd=tmp_path, KOSPEX_DB=copy, KOSPEX_CODE=code)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Applied" in result.stdout
    assert _applied(copy) > 0
    assert _applied(real) == 0, "migrated the default DB, not KOSPEX_DB"


# --- the commands that report the paths --------------------------------------


def _report_setup(habitat, tmp_path):
    home, code = habitat
    target = tmp_path / "from-file.db"
    _db_with_marker_repo(target, code)
    (home / "kospex.env").write_text(f"KOSPEX_DB={target}\nKOSPEX_HOME=/nowhere\n")
    return home, code, target


def test_init_validate_prints_each_path_and_its_source(habitat, tmp_path):
    home, code, target = _report_setup(habitat, tmp_path)

    result = _run("kospex_cli", "kospex", ["init", "--validate"], home=home,
                  cwd=tmp_path, KOSPEX_CODE=code)

    assert result.returncode == 0, result.stdout + result.stderr
    out = result.stdout
    assert f"KOSPEX_HOME: {home} (environment)" in out
    assert f"KOSPEX_CONFIG: {home / 'kospex.env'} (default)" in out
    assert f"KOSPEX_DB: {target} (kospex.env)" in out
    assert f"KOSPEX_CODE: {code} (environment)" in out
    assert f"KOSPEX_LOGS: {home / 'logs'} (default)" in out
    assert "Overall Status: HEALTHY" in out


def test_init_verbose_warns_about_kospex_home_in_kospex_env(habitat, tmp_path):
    home, code, _ = _report_setup(habitat, tmp_path)

    result = _run("kospex_cli", "kospex", ["init", "--verbose"], home=home,
                  cwd=tmp_path, KOSPEX_CODE=code)

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"KOSPEX_HOME in {home / 'kospex.env'} is ignored" in result.stdout
    assert not Path("/nowhere").exists()


def test_system_status_shows_the_resolved_paths(habitat, tmp_path):
    home, code, target = _report_setup(habitat, tmp_path)

    result = _run("kospex_cli", "kospex", ["system-status"], home=home,
                  cwd=tmp_path, KOSPEX_CODE=code)

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"{target} (kospex.env)" in result.stdout
    assert f"{code} (environment)" in result.stdout


# --- logs --------------------------------------------------------------------


def test_logs_are_written_under_kospex_logs(habitat, tmp_path):
    home, code = habitat
    logs = tmp_path / "custom" / "logs"

    result = _run("kospex_cli", "kospex", ["--debug", "list-repos", "-db"], home=home,
                  cwd=tmp_path, KOSPEX_CODE=code, KOSPEX_LOGS=logs)

    assert result.returncode == 0, result.stdout + result.stderr
    assert (logs / "kospex.log").is_file()
    assert not (home / "logs").exists()


# --- guard: path settings are read through HabitatConfig only ----------------

PATH_SETTINGS = {
    "KOSPEX_HOME", "KOSPEX_CONFIG", "KOSPEX_DB", "KOSPEX_CODE", "KOSPEX_LOGS",
    "KOSPEX_DUCKDB", "KOSPEX_KRUNNER", "KOSPEX_STAGING", "KOSPEX_ASSESSMENTS",
}

# The one module allowed to read them from the environment.
ALLOWED = {SRC / "kospex" / "habitat_config.py"}


def _is_os_environ(node) -> bool:
    return (isinstance(node, ast.Attribute) and node.attr == "environ") or (
        isinstance(node, ast.Name) and node.id == "environ")


def _env_path_accesses(source: str):
    """(line, name) for every os.getenv / os.environ access to a path setting.

    Covers getenv(), environ.get/pop/setdefault() and environ[...], read or
    write. A name passed through a variable is not seen.
    """
    hits = []
    for node in ast.walk(ast.parse(source)):
        key = None
        if isinstance(node, ast.Call) and node.args:
            func = node.func
            is_getenv = (isinstance(func, ast.Attribute) and func.attr == "getenv") or (
                isinstance(func, ast.Name) and func.id == "getenv")
            is_environ_method = (isinstance(func, ast.Attribute)
                                 and func.attr in {"get", "pop", "setdefault"}
                                 and _is_os_environ(func.value))
            if is_getenv or is_environ_method:
                key = node.args[0]
        elif isinstance(node, ast.Subscript) and _is_os_environ(node.value):
            key = node.slice
        if isinstance(key, ast.Constant) and key.value in PATH_SETTINGS:
            hits.append((node.lineno, key.value))
    return hits


def test_guard_detects_each_access_form():
    """The guard is not vacuous: each form it claims to catch is caught."""
    sample = (
        "import os\nfrom os import environ, getenv\n"
        "os.getenv('KOSPEX_DB')\n"
        "getenv(\n    'KOSPEX_CODE')\n"
        "os.environ.get('KOSPEX_LOGS', 'x')\n"
        "os.environ['KOSPEX_CONFIG'] = 'y'\n"
        "environ.setdefault('KOSPEX_HOME', 'z')\n"
        "os.environ.pop('KOSPEX_DUCKDB', None)\n"
        "os.getenv('KOSPEX_DB_FILENAME')\n"       # not a path setting
        "os.getenv('KOSPEX_DEPS_ENRICH')\n"       # not a path setting
    )
    assert [name for _, name in _env_path_accesses(sample)] == [
        "KOSPEX_DB", "KOSPEX_CODE", "KOSPEX_LOGS", "KOSPEX_CONFIG",
        "KOSPEX_HOME", "KOSPEX_DUCKDB"]


def test_path_settings_are_only_read_by_habitat_config():
    """Reading one from os.environ skips kospex.env; writing one overrides the
    user's setting for the rest of the process. Use HabitatConfig."""
    files = sorted(p for p in SRC.rglob("*.py") if p not in ALLOWED)
    assert len(files) > 50, "scanned too few files: wrong SRC?"

    offenders = [f"{path.relative_to(SRC)}:{line} {name}"
                 for path in files
                 for line, name in _env_path_accesses(path.read_text(encoding="utf-8"))]

    assert offenders == [], "\n".join(offenders)
