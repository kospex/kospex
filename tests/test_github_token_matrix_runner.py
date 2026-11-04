"""Offline tests for the manual live runner tests/manual/github_token_matrix.py.

The runner itself needs a real token and is never run in CI; these check its
own logic (environment isolation, classification, leak detection, baseline
handling) without touching the network.
"""
import importlib.util
from pathlib import Path

import pytest

RUNNER = Path(__file__).parent / "manual" / "github_token_matrix.py"
_spec = importlib.util.spec_from_file_location("github_token_matrix", RUNNER)
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)

TOKEN = "github_pat_REALTOKEN0123456789abcdef"

AUTH_OK = "Found Github GITHUB_AUTH_TOKEN in the environment.\nAuthenticated as: octo\n"
# Real output from today's kgit on a 401 (captured, not invented).
REJECTED = ("Found Github GITHUB_AUTH_TOKEN in the environment.\n"
            "HTTP Error: 401 Client Error: Unauthorized for url: https://api.github.com/user\n"
            "No data returned or request failed\n"
            "Authentication failed. Check your GITHUB_AUTH_TOKEN\n")
MISSING = "Could not find Github GITHUB_AUTH_TOKEN in the environment.\n"


def row(n):
    return next(r for r in m.ROWS if r.n == n)


class TestRows:
    def test_thirteen_rows_numbered_in_order(self):
        assert [r.n for r in m.ROWS] == list(range(1, 14))

    def test_only_the_new_and_fix_rows_are_tagged(self):
        assert {r.n: r.tag for r in m.ROWS if r.tag} == {
            3: "new", 12: "fix", 13: "fix"}

    def test_every_row_only_names_known_variables(self):
        for r in m.ROWS:
            assert set(r.values) <= set(m.TOKEN_ENV_VARS), r.n


class TestBuildEnv:
    def test_inherited_tokens_are_removed(self, tmp_path):
        base = {"PATH": "/bin", "GH_TOKEN": "mine", "GITHUB_TOKEN": "mine",
                "GITHUB_AUTH_TOKEN": "mine", "KOSPEX_TEST_GITHUB_TOKEN": TOKEN}
        env = m.build_env(base, row(11), TOKEN, tmp_path)
        assert env["PATH"] == "/bin"
        for var in (*m.TOKEN_ENV_VARS, "KOSPEX_TEST_GITHUB_TOKEN"):
            assert var not in env

    def test_row_values_are_set(self, tmp_path):
        env = m.build_env({}, row(5), TOKEN, tmp_path)
        assert env["GITHUB_AUTH_TOKEN"] == "invalid-token"
        assert env["GH_TOKEN"] == TOKEN
        assert "GITHUB_TOKEN" not in env

    def test_empty_and_padded(self, tmp_path):
        assert m.build_env({}, row(12), TOKEN, tmp_path)["GITHUB_AUTH_TOKEN"] == ""
        assert m.build_env({}, row(13), TOKEN, tmp_path)["GITHUB_AUTH_TOKEN"] == f" {TOKEN}\n"

    def test_gh_config_isolated(self, tmp_path):
        env = m.build_env({"GH_CONFIG_DIR": "/home/me/.config/gh"}, row(1), TOKEN, tmp_path)
        assert env["GH_CONFIG_DIR"] == str(tmp_path)


def test_token_is_never_in_argv():
    assert not any(TOKEN in part for part in m.command())


def test_command_loads_this_checkouts_src():
    argv = m.command()
    assert argv[3] == str(m.SRC)
    assert argv[-2:] == ["github", "-test-auth"]


class TestClassify:
    @pytest.mark.parametrize("rc, out, expected", [
        (0, AUTH_OK, m.OK),
        (1, REJECTED, m.REJECTED),
        (1, "Authentication failed: Invalid token\n", m.REJECTED),  # once test_auth is fixed
        (1, MISSING, m.MISSING),
        (1, "Timeout: read timed out\nNo data returned or request failed\n", m.ERROR),
        (1, "Traceback (most recent call last):\n", m.ERROR),
        (1, AUTH_OK, m.ERROR),   # authenticated but non-zero exit is not a pass
    ])
    def test_classify(self, rc, out, expected):
        assert m.classify(rc, out) == expected


class TestCheck:
    def test_baseline_pass_without_source_line(self):
        assert m.check(row(1), 0, AUTH_OK, TOKEN, baseline=True).status == "PASS"

    def test_post_change_requires_the_source_line(self):
        r = m.check(row(1), 0, AUTH_OK, TOKEN)
        assert r.status == "FAIL"
        assert "Using token from GITHUB_AUTH_TOKEN" in r.notes[0]

    def test_post_change_with_right_source(self):
        out = "Using token from GH_TOKEN\n" + AUTH_OK
        assert m.check(row(2), 0, out, TOKEN).status == "PASS"

    def test_wrong_source_fails(self):
        out = "Using token from GITHUB_TOKEN\n" + AUTH_OK
        assert m.check(row(2), 0, out, TOKEN).status == "FAIL"

    def test_rejected_row_passes_on_401(self):
        out = "Using token from GITHUB_AUTH_TOKEN\n" + REJECTED
        assert m.check(row(5), 1, out, TOKEN).status == "PASS"

    def test_rejected_row_fails_when_login_succeeds(self):
        # The loser's valid token was used: precedence is wrong.
        out = "Using token from GITHUB_AUTH_TOKEN\n" + AUTH_OK
        assert m.check(row(5), 0, out, TOKEN).status == "FAIL"

    def test_network_error_is_error_not_pass(self):
        r = m.check(row(5), 1, "Timeout: read timed out\n", TOKEN)
        assert r.status == "ERROR"

    def test_missing_row_must_not_report_a_source(self):
        assert m.check(row(11), 1, MISSING, TOKEN).status == "PASS"
        out = "Using token from GH_TOKEN\n" + MISSING
        assert m.check(row(11), 1, out, TOKEN).status == "FAIL"

    def test_tagged_row_failing_under_baseline_is_xfail(self):
        assert m.check(row(3), 1, MISSING, TOKEN, baseline=True).status == "XFAIL"

    def test_tagged_row_passing_under_baseline_is_xpass(self):
        assert m.check(row(3), 0, AUTH_OK, TOKEN, baseline=True).status == "XPASS"

    def test_tagged_row_is_a_real_failure_after_the_change(self):
        assert m.check(row(3), 1, MISSING, TOKEN).status == "FAIL"

    @pytest.mark.parametrize("out", [
        f"Request Error: bad header value: 'token  {TOKEN}\\n'",
        f"debug: ...{TOKEN[-16:]}",
    ])
    def test_leak_wins_over_everything(self, out):
        # Even a tagged row under baseline: a leak is never an expected failure.
        assert m.check(row(13), 1, out, TOKEN, baseline=True).status == "LEAK"

    def test_failed_counts_fail_error_leak_only(self):
        results = [m.Result(row(1), s, "") for s in
                   ("PASS", "XFAIL", "XPASS", "FAIL", "ERROR", "LEAK")]
        assert [r.status for r in m.failed(results)] == ["FAIL", "ERROR", "LEAK"]


class TestReadToken:
    def test_from_file_stripped(self, tmp_path):
        f = tmp_path / "t"
        f.write_text(f"{TOKEN}\n")
        assert m.read_token(str(f), environ={}) == TOKEN

    def test_from_env(self):
        assert m.read_token(environ={"KOSPEX_TEST_GITHUB_TOKEN": TOKEN}) == TOKEN

    def test_prompt_when_nothing_else(self):
        assert m.read_token(environ={}, prompt=lambda _: TOKEN) == TOKEN

    @pytest.mark.parametrize("bad", ["", "   ", "two words", "invalid-token"])
    def test_rejects_unusable_tokens(self, bad):
        with pytest.raises(ValueError):
            m.read_token(environ={}, prompt=lambda _: bad)


class TestTokenShape:
    def test_full_fine_grained(self):
        t = "github_pat_" + "a" * 82
        assert m.token_shape(t) == "github_pat_ prefix, 93 chars"

    def test_truncated_paste_is_flagged(self):
        assert "partly pasted" in m.token_shape("github_pat_" + "a" * 40)

    def test_classic(self):
        assert m.token_shape("ghp_" + "a" * 36) == "ghp_ prefix, 40 chars"

    def test_never_includes_the_secret_part(self):
        t = "github_pat_" + "SECRETPART" * 8 + "xy"
        assert "SECRET" not in m.token_shape(t)
        assert "SECRET" not in m.token_shape("SECRETPART-unknown-format")


class TestPreflightVerdict:
    def test_both_accept(self):
        assert m.preflight_verdict({"Bearer": 200, "token": 200})[0] is True

    def test_bad_token_stops_the_run(self):
        ok, msg = m.preflight_verdict({"Bearer": 401, "token": 401})
        assert ok is False and "rejects the token" in msg

    def test_scheme_mismatch_points_at_kospex(self):
        ok, msg = m.preflight_verdict({"Bearer": 200, "token": 401})
        assert ok is True and "kospex bug" in msg

    def test_network_error_stops_the_run(self):
        ok, msg = m.preflight_verdict({"Bearer": "ConnectTimeout", "token": "ConnectTimeout"})
        assert ok is False and "unexpected" in msg


def fake_today(row, token, gh_config_dir, cwd):
    """kgit -test-auth as it behaves before #343, for end-to-end runs of main()."""
    env = m.build_env({}, row, token, gh_config_dir)
    value = env.get("GITHUB_AUTH_TOKEN")
    if value is None:
        value = env.get("GH_TOKEN")
    if not value:
        return 1, MISSING
    return (0, AUTH_OK) if value == token else (1, REJECTED)


class TestMain:
    def test_list_needs_no_token(self, capsys):
        assert m.main(["--list"]) == 0
        assert "GITHUB_AUTH_TOKEN" in capsys.readouterr().out

    def test_baseline_against_todays_behaviour_passes(self, monkeypatch, capsys):
        monkeypatch.setenv("KOSPEX_TEST_GITHUB_TOKEN", TOKEN)
        assert m.main(["--baseline"], runner=fake_today) == 0
        out = capsys.readouterr().out
        assert "13/13 rows as expected" in out
        assert out.count("XFAIL") == 3
        assert TOKEN not in out

    def test_post_change_mode_fails_against_todays_behaviour(self, monkeypatch, capsys):
        monkeypatch.setenv("KOSPEX_TEST_GITHUB_TOKEN", TOKEN)
        assert m.main([], runner=fake_today) == 1

    def test_row_filter(self, monkeypatch, capsys):
        monkeypatch.setenv("KOSPEX_TEST_GITHUB_TOKEN", TOKEN)
        assert m.main(["--baseline", "--row", "1", "--row", "11"], runner=fake_today) == 0
        assert "2/2 rows as expected" in capsys.readouterr().out

    def test_bad_token_exits_2(self, monkeypatch):
        monkeypatch.setenv("KOSPEX_TEST_GITHUB_TOKEN", "invalid-token")
        assert m.main([], runner=fake_today) == 2
