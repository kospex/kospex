"""The sample logging config must only name loggers that something actually uses.

`create_sample_config()` writes a starter `~/kospex/config.json` for users to edit.
It listed `kwatch` long after `src/kwatch.py` was deleted, so anyone following it
configured a level for a logger that can never emit.

Checking "does src/<name>.py exist" is the wrong test — `kospex` is a valid logger
name owned by `kospex_cli.py` and `kospex_core.py`, and there is no `src/kospex.py`.
What matters is whether any module asks for that logger.
"""
import json
import re
from pathlib import Path

import pytest

import kospex_logging

_SRC = Path(__file__).resolve().parent.parent / "src"
_CALL = re.compile(r"""get_kospex_logger\(\s*['"]([a-zA-Z_][a-zA-Z_0-9]*)['"]""")


def _logger_names_in_use():
    """Every logger name requested via get_kospex_logger() anywhere under src/."""
    names = set()
    for path in _SRC.rglob("*.py"):
        names.update(_CALL.findall(path.read_text(encoding="utf-8", errors="ignore")))
    return names


def _sample_modules(tmp_path, monkeypatch):
    """Run create_sample_config() against a throwaway path, return its module names."""
    config_path = tmp_path / "config.json"
    monkeypatch.setattr(kospex_logging._kospex_logger, "config_file", config_path)

    written = kospex_logging.create_sample_config()

    assert Path(written) == config_path
    return json.loads(config_path.read_text())["logging"]["modules"]


def test_sample_config_only_names_loggers_that_are_used(tmp_path, monkeypatch):
    in_use = _logger_names_in_use()
    assert "kospex" in in_use, "sanity: the scanner should find the core logger"

    orphans = [name for name in _sample_modules(tmp_path, monkeypatch) if name not in in_use]

    assert not orphans, (
        f"sample logging config names loggers nothing requests: {orphans}. "
        "Remove them, or point the entry at the logger that replaced them."
    )


def test_sample_config_covers_the_main_cli_tools(tmp_path, monkeypatch):
    modules = _sample_modules(tmp_path, monkeypatch)

    for expected in ("kospex", "kgit", "krunner", "kweb2"):
        assert expected in modules
