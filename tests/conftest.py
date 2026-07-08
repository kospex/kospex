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


def _reset_habitat_singleton():
    try:
        from kospex.habitat_config import HabitatConfig
        HabitatConfig.reset_instance()
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _isolate_kospex_globals():
    saved = {k: os.environ.get(k) for k in _KOSPEX_ENV_KEYS}
    _reset_habitat_singleton()
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    _reset_habitat_singleton()
