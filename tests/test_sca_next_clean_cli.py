"""`kospex sca -dependabot FILE -out OUT -next-clean`. Deps layer and OSV are faked."""
import json

import pytest
from click.testing import CliRunner

import kospex_cli
from test_dependabot_next_clean import LINE, Deps, FakeOsv, alert


@pytest.fixture
def run(monkeypatch, tmp_path):
    monkeypatch.delenv("KOSPEX_DEPS_ENRICH", raising=False)
    monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)
    monkeypatch.delenv("CURL_CA_BUNDLE", raising=False)
    state = {"osv": FakeOsv({"pkg": {"1.0.0": ["A"]}}), "built": 0}

    def make(*a, **kw):
        state["built"] += 1
        return state["osv"]

    monkeypatch.setattr("kospex.dependabot_enrich.OsvClient", make)

    def go(*args, alerts=None, releases=None):
        monkeypatch.setattr(kospex_cli.kospex, "dependencies",
                            Deps(releases or {"pkg": LINE}))
        src = tmp_path / "alerts.jsonl"
        src.write_text("\n".join(json.dumps(a) for a in (alerts or [alert()])) + "\n")
        out = tmp_path / "out.jsonl"
        result = CliRunner().invoke(
            kospex_cli.cli, ["sca", "-dependabot", str(src), "-out", str(out), *args])
        result.rows = ([json.loads(l) for l in out.read_text().splitlines()]
                       if out.exists() else [])
        return result

    go.state = state
    return go


def test_without_dependabot_it_is_a_usage_error():
    result = CliRunner().invoke(kospex_cli.cli, ["sca", "-next-clean"])
    assert result.exit_code == 2
    assert "-next-clean only applies to -dependabot" in result.output


def test_adds_the_fields_and_summarises_their_statuses(run):
    result = run("-next-clean", alerts=[alert(), alert(current=None)])
    assert result.exit_code == 0, result.output
    assert [r["sca_next_clean_status"] for r in result.rows] == ["found", "no_current_version"]
    assert result.rows[0]["sca_next_clean_version"] == "1.1.0"
    assert "next clean found: 1" in result.stderr
    assert "next clean no_current_version: 1" in result.stderr


def test_without_the_flag_the_output_has_no_new_keys_and_osv_is_never_built(run):
    result = run()
    assert result.exit_code == 0, result.output
    assert not any(k.startswith("sca_next_clean") for k in result.rows[0])
    assert run.state["built"] == 0


def test_a_blocked_osv_stops_the_lookups_and_says_so_once(run):
    names = [f"p{i}" for i in range(5)]
    run.state["osv"] = FakeOsv(fail=set(names))
    result = run("-next-clean", alerts=[alert(name=n) for n in names],
                 releases={n: LINE for n in names})
    assert result.exit_code == 0, result.output
    assert all(r["sca_next_clean_status"] == "lookup_error" for r in result.rows)
    assert len(run.state["osv"].batch_calls) == 3
    assert result.stderr.count("stopped asking OSV") == 1
    assert "-next-clean" in result.stderr
