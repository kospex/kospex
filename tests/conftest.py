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

import pytest

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
