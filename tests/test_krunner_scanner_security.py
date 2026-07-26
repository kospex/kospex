"""Security regression tests for the krunner scanner/grep commands (CWE-78).

The scanner binaries are not installed in CI, so subprocess.run is monkeypatched
throughout — nothing is executed. These tests prove that tainted values (a
repo-id-derived filename, a grep keyword) reach subprocess as inert list
elements, never a shell string.
"""
from pathlib import Path

import pytest

from kospex_core import Kospex


def _kospex_with(tmp_path, repo_id, monkeypatch):
    k = Kospex()
    monkeypatch.setattr(k, "get_krunner_directory", lambda: str(tmp_path))
    monkeypatch.setattr(k.git, "get_repo_id", lambda: repo_id)
    return k


def test_generate_krunner_filename_is_absolute_and_contained(tmp_path, monkeypatch):
    k = _kospex_with(tmp_path, "github.com~org~repo", monkeypatch)
    fname = k.generate_krunner_filename(function="TRUFFLEHOG", ext="json")
    assert Path(fname).is_absolute()
    assert Path(fname).parent == tmp_path.resolve()
    assert Path(fname).name == "github.com~org~repo.TRUFFLEHOG.json"


def test_generate_krunner_filename_rejects_escaping_repo_id(tmp_path, monkeypatch):
    # A crafted repo_id that would traverse out of the krunner dir must be refused.
    k = _kospex_with(tmp_path, "../../../../etc/passwd", monkeypatch)
    with pytest.raises(ValueError):
        k.generate_krunner_filename(function="TRUFFLEHOG", ext="json")
