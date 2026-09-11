"""Shared test fixtures.

Kospex resolves paths through the HabitatConfig singleton and, during
construction, writes several KOSPEX_* vars straight to os.environ (not via
monkeypatch). Both leak across tests: a test that constructs Kospex with a
throwaway KOSPEX_HOME can leave the singleton and env pointing at a now-deleted
tmpdir, breaking a later test's DB-path resolution.

This autouse fixture isolates those globals for every test: it snapshots and
restores the KOSPEX_* env vars and resets the singleton before and after each
test, so tests can't pollute one another regardless of run order.
"""
import os
import sqlite3
import sys

import pytest


# --- SQLite floor ------------------------------------------------------------
# kospex runs on whatever SQLite the user's Python was built with, and a
# distro-packaged Python uses the system library: 3.26 on RHEL 8, 3.34 on
# RHEL 9, 3.37 on Ubuntu 22.04. Those releases never raise it. A developer
# machine or CI runner usually has 3.45+, so a query using a newer function
# passes every test here and fails for the user -- `unixepoch()` (3.38) did
# exactly that.
#
# Every connection opened during the tests gets an authorizer that refuses
# each function newer than the floor. The authorizer runs when a statement is
# compiled, as a genuinely missing function fails, so a query over an empty
# table is caught too. The suite then fails wherever production SQL uses one,
# on any machine. Syntax (RETURNING, ->>, RIGHT JOIN, DROP COLUMN) is not
# covered: only function calls reach the authorizer.
#
# To use one of these functions, raise the floor deliberately: remove it here
# and say which platforms stop being supported.
SQLITE_FLOOR = "3.26"
_FUNCTIONS_NEWER_THAN_FLOOR = {
    "iif": "3.32",
    "ceil": "3.35", "ceiling": "3.35", "floor": "3.35", "trunc": "3.35",
    "ln": "3.35", "log": "3.35", "log2": "3.35", "log10": "3.35",
    "exp": "3.35", "pow": "3.35", "power": "3.35", "sqrt": "3.35",
    "mod": "3.35", "pi": "3.35",
    "unixepoch": "3.38", "format": "3.38",
    "unhex": "3.41",
    "octet_length": "3.43", "timediff": "3.43",
    "concat": "3.44", "concat_ws": "3.44", "string_agg": "3.44",
}


def _refuse_functions_newer_than_floor(action, arg1, arg2, db_name, trigger):
    if action == sqlite3.SQLITE_FUNCTION:
        since = _FUNCTIONS_NEWER_THAN_FLOOR.get((arg2 or "").lower())
        if since:
            # SQLite's own error is only "not authorized to use function: X";
            # this lands in the failing test's captured stderr.
            print(f"SQL function {arg2}() needs SQLite {since}; kospex supports "
                  f"SQLite {SQLITE_FLOOR}+ (see tests/conftest.py)", file=sys.stderr)
            return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


_real_connect = sqlite3.connect


def _connect_at_floor(*args, **kwargs):
    conn = _real_connect(*args, **kwargs)
    conn.set_authorizer(_refuse_functions_newer_than_floor)
    return conn


# Patched at conftest import, before any test module imports kospex, so every
# connection -- sqlite_utils' included -- goes through it.
sqlite3.connect = _connect_at_floor


_KOSPEX_ENV_KEYS = (
    "KOSPEX_HOME", "KOSPEX_DB", "KOSPEX_CONFIG", "KOSPEX_CODE", "KOSPEX_LOGS",
    "KOSPEX_DUCKDB", "KOSPEX_STAGING", "KOSPEX_KRUNNER", "KOSPEX_ASSESSMENTS",
)


# Captured at conftest import time. pytest imports conftest.py BEFORE the test
# modules, so this snapshot is the pristine shell environment — before any test
# module's imports run kospex construction. (Importing some modules, e.g.
# `import kgit`, runs a module-level `Kospex()` that writes KOSPEX_* straight to
# os.environ at import time; a per-test snapshot taken during setup would already
# contain that leak and perpetuate it across the whole session.) Restoring to
# this pristine snapshot around every test undoes both import-time and per-test
# leaks.
_PRISTINE_ENV = {k: os.environ.get(k) for k in _KOSPEX_ENV_KEYS}


def _reset_habitat_singleton():
    try:
        from kospex.habitat_config import HabitatConfig
        HabitatConfig.reset_instance()
    except Exception:
        pass


def _restore_pristine_env():
    for k in _KOSPEX_ENV_KEYS:
        v = _PRISTINE_ENV[k]
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


@pytest.fixture(autouse=True)
def _isolate_kospex_globals():
    _restore_pristine_env()
    _reset_habitat_singleton()
    yield
    _restore_pristine_env()
    _reset_habitat_singleton()
