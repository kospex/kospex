"""The unparseable-remote warning goes to stderr, never stdout (#277).

`KospexGit.set_remote_url()` runs on the way into commands that print
machine-readable output, e.g. `kospex sca -json`. A warning printed to stdout
lands ahead of the JSON array and breaks every consumer, even one that
discards stderr.
"""

import json
import subprocess

import pytest
from click.testing import CliRunner

from kospex_dependencies import KospexDependencies
from kospex_git import KospexGit

# A local-path remote: parse_git_remote() can't read it, so set_remote_url()
# falls back and warns. Five "/"-segments, so the fallback doesn't IndexError.
LOCAL_REMOTE = "/srv/git/acme/demo"

PACKAGE_JSON = '{"name":"d","version":"1.0.0","dependencies":{"express":"4.18.2"}}'


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args],
                          check=True, capture_output=True, text=True).stdout.strip()


def _repo(tmp_path):
    repo = tmp_path / "demo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "dev@example.com")
    _git(repo, "config", "user.name", "Dev")
    _git(repo, "remote", "add", "origin", LOCAL_REMOTE)
    (repo / "package.json").write_text(PACKAGE_JSON)
    _git(repo, "add", "package.json")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def test_the_remote_is_unparseable_so_the_fallback_runs():
    # Guards the premise: if the parser ever learns local paths, the tests
    # below stop exercising the warning and would pass vacuously.
    assert KospexGit().parse_git_remote(LOCAL_REMOTE) is None


def test_set_remote_url_warns_on_stderr_not_stdout(capsys):
    KospexGit().set_remote_url(LOCAL_REMOTE)

    out, err = capsys.readouterr()
    assert out == ""
    assert "Failed specific parsing of remote URL" in err
    assert LOCAL_REMOTE in err


def test_the_warning_does_not_print_embedded_credentials(capsys):
    # The warning names the URL so it is actionable, and stderr and the log
    # file are both places a token must not end up (CWE-532).
    url = "ftp://user:s3cret@host.example/acme/demo"
    assert KospexGit().parse_git_remote(url) is None

    KospexGit().set_remote_url(url)

    _, err = capsys.readouterr()
    assert "s3cret" not in err
    assert "user:" not in err
    assert "ftp://host.example/acme/demo" in err


@pytest.mark.parametrize("url, secret", [
    # urlparse ends the netloc at the first '/', '?' or '#', so a password
    # containing one unencoded hides the '@' from a netloc-based redactor.
    ("ftp://user:s3/cret@host.example/acme/demo", "cret"),
    ("ftp://user:s3?cret@host.example/acme/demo", "cret"),
    ("ftp://user:s3#cret@host.example/acme/demo", "cret"),
])
def test_redaction_does_not_trust_the_url_parser(url, secret, capsys):
    assert KospexGit().parse_git_remote(url) is None

    KospexGit().set_remote_url(url)

    _, err = capsys.readouterr()
    assert secret not in err
    assert "host.example" in err


def test_redaction_without_a_scheme():
    # Checked on the helper: through set_remote_url() this URL reaches the
    # split("/") fallback after the warning, which IndexErrors on it. That
    # fallback is pre-existing and tracked separately.
    shown = KospexGit._without_userinfo("user:s3cret@host.example:acme/demo")
    assert shown == "host.example:acme/demo"


def test_a_url_urlparse_rejects_still_only_warns(capsys):
    # urlparse raises ValueError ("Invalid IPv6 URL") on this; redacting must
    # not turn a warning into a crash.
    url = "ftp://user:s3cret@[bad/acme/demo"
    assert KospexGit().parse_git_remote(url) is None

    KospexGit().set_remote_url(url)

    _, err = capsys.readouterr()
    assert "s3cret" not in err


def test_sca_json_stdout_stays_valid_json_with_an_unparseable_remote(
        tmp_path, monkeypatch):
    import kospex_cli

    kd = KospexDependencies()
    kd.depsdev_record = lambda pt, pn, pv: {
        "package_name": pn, "package_version": pv, "package_type": pt,
        "versions_behind": 0, "advisories": 0, "days_ago": 1,
        "published_at": "2026-01-01T00:00:00Z", "resolution": "resolved",
        "source_repo": "", "default": True,
    }
    kd.get_pypi_source_repo = lambda name: ""
    monkeypatch.setattr(kospex_cli.kospex, "dependencies", kd)
    repo = _repo(tmp_path)

    result = CliRunner().invoke(
        kospex_cli.cli, ["sca", "-json", str(repo / "package.json")])

    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    assert [r["package_name"] for r in rows] == ["express"]
    assert "Failed specific parsing of remote URL" in result.stderr
