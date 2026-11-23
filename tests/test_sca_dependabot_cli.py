"""`kospex sca -dependabot FILE -out OUT`: enrich a Dependabot JSONL file.

The lookup layer is the Task 1 StubDeps, swapped in for `kospex_cli.kospex`,
so no network is used.
"""
import json

import pytest
from click.testing import CliRunner

import kospex_cli
from test_dependabot_enrich import StubDeps, _alert


@pytest.fixture
def run(monkeypatch, tmp_path):
    monkeypatch.delenv("KOSPEX_DEPS_ENRICH", raising=False)
    monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)
    monkeypatch.delenv("CURL_CA_BUNDLE", raising=False)
    stub = StubDeps()
    monkeypatch.setattr(kospex_cli.kospex, "dependencies", stub)

    def _run(args):
        return CliRunner().invoke(kospex_cli.cli, ["sca", *args])
    _run.stub = stub
    return _run


@pytest.fixture
def paths(tmp_path):
    src = tmp_path / "alerts.jsonl"
    src.write_text("\n".join(json.dumps(a) for a in (
        _alert(), _alert(ecosystem="nosuch"), _alert(package="other"))) + "\n")
    return src, tmp_path / "out.jsonl"


def _rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_requires_out(run, paths):
    src, out = paths
    r = run(["-dependabot", str(src)])
    assert r.exit_code == 2
    assert "-out" in r.output
    assert not out.exists()
    assert run.stub.calls["release_list"] == []


@pytest.mark.parametrize("extra", [
    ["-json"], ["-scope", "all"], ["-save"], ["-package", "npm:cookie"],
    ["-repo", "."], ["FILE"],
])
def test_refused_with_json_scope_save_package_repo_file(run, paths, extra, tmp_path):
    src, out = paths
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    extra = [str(src) if a == "FILE" else str(repo) if a == "." else a for a in extra]
    r = run(["-dependabot", str(src), "-out", str(out), *extra])
    assert r.exit_code == 2
    assert "-dependabot cannot be used with" in r.output
    assert not out.exists()
    assert run.stub.calls["release_list"] == []


def test_default_scope_not_refused(run, paths):
    src, out = paths
    r = run(["-dependabot", str(src), "-out", str(out)])
    assert r.exit_code == 0, r.output
    assert out.exists()


def test_lookups_off_requires_enrich(run, paths, monkeypatch):
    src, out = paths
    monkeypatch.setenv("KOSPEX_DEPS_ENRICH", "false")
    r = run(["-dependabot", str(src), "-out", str(out)])
    assert r.exit_code == 1
    assert "Add -enrich" in r.stderr
    assert "-dependabot looks alerts up on deps.dev" in r.stderr
    assert not out.exists()


def test_lookups_off_with_enrich_runs(run, paths, monkeypatch):
    src, out = paths
    monkeypatch.setenv("KOSPEX_DEPS_ENRICH", "false")
    r = run(["-dependabot", str(src), "-out", str(out), "-enrich"])
    assert r.exit_code == 0, r.stderr
    assert len(_rows(out)) == 3


def test_env_true_without_flag_runs(run, paths, monkeypatch):
    src, out = paths
    monkeypatch.setenv("KOSPEX_DEPS_ENRICH", "true")
    r = run(["-dependabot", str(src), "-out", str(out)])
    assert r.exit_code == 0, r.stderr
    assert len(_rows(out)) == 3


def test_bad_input_exit_1_names_line(run, tmp_path):
    src = tmp_path / "bad.jsonl"
    src.write_text(json.dumps(_alert()) + "\n{not json\n")
    out = tmp_path / "out.jsonl"
    r = run(["-dependabot", str(src), "-out", str(out)])
    assert r.exit_code == 1
    assert "line 2" in r.stderr
    assert "not written" in r.stderr
    assert not out.exists()


def test_out_equals_in_exits_1(run, paths):
    src, _ = paths
    before = src.read_text()
    r = run(["-dependabot", str(src), "-out", str(src)])
    assert r.exit_code == 1
    assert "differ" in r.stderr
    assert src.read_text() == before


def test_summary_on_stderr_counts_statuses(run, paths):
    src, out = paths
    r = run(["-dependabot", str(src), "-out", str(out)])
    assert r.exit_code == 0, r.stderr
    assert r.stdout == ""
    lines = r.stderr.splitlines()
    assert f"Wrote 3 alerts to {out}" in lines
    status_lines = [ln for ln in lines if ln.startswith("  ")]
    assert any("unsupported_ecosystem" in ln and "1" in ln for ln in status_lines)
    assert any("ok" in ln and "2" in ln for ln in status_lines)
    assert [ln.split()[0] for ln in status_lines] == sorted(
        ln.split()[0] for ln in status_lines)
    assert [row["sca_status"] for row in _rows(out)] == [
        "ok", "unsupported_ecosystem", "ok"]
