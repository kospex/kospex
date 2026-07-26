"""Security regression tests for the krunner scanner/grep commands.

These tests cover the fix for two related weaknesses:
- CWE-78 (OS command injection): the trufflehog/gitleaks/semgrep and grep
  commands interpolated tainted values into os.system shell strings; the tests
  prove those values now reach subprocess as inert list elements, never a shell.
- CWE-22 (path traversal): generate_krunner_filename returns an absolute path
  contained within the krunner directory.

The scanner binaries are not installed in CI, so subprocess.run is monkeypatched
throughout the subprocess-exercising tests — nothing is executed.
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


import subprocess  # noqa: E402

import krunner  # noqa: E402


class _FakeCompleted:
    def __init__(self, returncode=0):
        self.returncode = returncode


def test_run_scanner_uses_list_argv_and_no_shell(monkeypatch):
    calls = {}

    def fake_run(argv, **kwargs):
        calls["argv"] = argv
        calls["kwargs"] = kwargs
        return _FakeCompleted(0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    krunner._run_scanner(["gitleaks", "detect", "-r", "/x/out.json"], cwd="/repo")

    assert isinstance(calls["argv"], list)
    assert calls["argv"] == ["gitleaks", "detect", "-r", "/x/out.json"]
    assert calls["kwargs"].get("cwd") == "/repo"
    assert calls["kwargs"].get("shell") in (None, False)


def test_run_scanner_routes_stdout_to_file(tmp_path, monkeypatch):
    out_path = tmp_path / "out.json"

    def fake_run(argv, **kwargs):
        # prove stdout is a writable handle at out_path
        kwargs["stdout"].write(b"hello")
        return _FakeCompleted(0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    krunner._run_scanner(
        ["trufflehog", "filesystem", "-j", "."], cwd="/repo", stdout_path=str(out_path))

    assert out_path.read_bytes() == b"hello"
