"""Which environment variable supplies the GitHub token (#343).

Precedence: GITHUB_AUTH_TOKEN > GH_TOKEN > GITHUB_TOKEN. Empty or
whitespace-only values count as unset, and surrounding whitespace is stripped.

Expected results are literal tables, not computed from a precedence rule, so
the tests can't share a mistake with the code. The developer running the suite
very likely has GH_TOKEN or GITHUB_TOKEN set for gh, so every test starts with
all three removed; without that, an "unset" row could pass on the real token.

The `live` tests call the real GitHub API and only run when
KOSPEX_TEST_GITHUB_TOKEN holds a real read-only token. Run them, with the full
CLI matrix, via tests/manual/github_token_matrix.py.
"""
import os

import pytest

from kospex_github import KospexGithub

VARS = ("GITHUB_AUTH_TOKEN", "GH_TOKEN", "GITHUB_TOKEN")

# Read before the autouse fixture clears the environment.
LIVE_TOKEN = os.environ.get("KOSPEX_TEST_GITHUB_TOKEN", "").strip()
INVALID = "invalid-token"


@pytest.fixture(autouse=True)
def no_inherited_tokens(monkeypatch):
    for var in (*VARS, "KOSPEX_TEST_GITHUB_TOKEN"):
        monkeypatch.delenv(var, raising=False)


def credentials(monkeypatch, **values):
    for var, value in values.items():
        monkeypatch.setenv(var, value)
    gh = KospexGithub()
    found = gh.get_env_credentials()
    return gh, found


# (GITHUB_AUTH_TOKEN, GH_TOKEN, GITHUB_TOKEN) -> (token used, variable it came from)
# None = unset.
RESOLUTION = [
    pytest.param(None, None, None, None, None, id="none-set"),
    pytest.param("tok-auth", None, None, "tok-auth", "GITHUB_AUTH_TOKEN", id="auth-only"),
    pytest.param(None, "tok-gh", None, "tok-gh", "GH_TOKEN", id="gh-only"),
    pytest.param(None, None, "tok-github", "tok-github", "GITHUB_TOKEN", id="github-only"),
    pytest.param("tok-auth", "tok-gh", None, "tok-auth", "GITHUB_AUTH_TOKEN", id="auth-over-gh"),
    pytest.param("tok-auth", None, "tok-github", "tok-auth", "GITHUB_AUTH_TOKEN",
                 id="auth-over-github"),
    pytest.param(None, "tok-gh", "tok-github", "tok-gh", "GH_TOKEN", id="gh-over-github"),
    pytest.param("tok-auth", "tok-gh", "tok-github", "tok-auth", "GITHUB_AUTH_TOKEN",
                 id="all-three"),
    pytest.param("", "tok-gh", None, "tok-gh", "GH_TOKEN", id="empty-auth-falls-through"),
    pytest.param("   ", None, "tok-github", "tok-github", "GITHUB_TOKEN",
                 id="blank-auth-falls-through"),
    pytest.param("", "", "", None, None, id="all-empty"),
    pytest.param(" tok-auth\n", None, None, "tok-auth", "GITHUB_AUTH_TOKEN", id="padded-stripped"),
    pytest.param("tok-auth\r\n", None, None, "tok-auth", "GITHUB_AUTH_TOKEN", id="crlf-stripped"),
]


@pytest.mark.parametrize("auth, gh_tok, github, token, source", RESOLUTION)
def test_resolution(monkeypatch, auth, gh_tok, github, token, source):
    values = {k: v for k, v in zip(VARS, (auth, gh_tok, github)) if v is not None}
    gh, found = credentials(monkeypatch, **values)

    assert found is (token is not None)
    assert gh.authenticated is (token is not None)
    assert gh.token_source == source
    if token is None:
        assert "Authorization" not in gh.headers
        assert gh.access_token == ""
    else:
        assert gh.access_token == token
        assert gh.headers["Authorization"] == f"token {token}"


def test_names_and_order_are_the_public_contract():
    assert KospexGithub.TOKEN_ENV_VARS == ("GITHUB_AUTH_TOKEN", "GH_TOKEN", "GITHUB_TOKEN")


class TestErrorOutputNeverCarriesTheToken:
    """CWE-532: request errors are printed, and some exception messages quote
    the Authorization header value."""

    SECRET = "ghp_SECRETSECRETSECRETSECRETSECRET0000"

    def test_interior_newline_token_is_not_printed(self, capsys):
        # Stripping can't fix a value with a newline in the middle; requests
        # then refuses the header and quotes it in the exception message.
        gh = KospexGithub()
        gh.set_access_token(f"{self.SECRET[:20]}\n{self.SECRET[20:]}")
        gh._make_request(f"{gh.base_url}/user")
        out = capsys.readouterr().out
        assert "Request Error" in out
        assert self.SECRET[:20] not in out
        assert self.SECRET[20:] not in out


class TestTestAuthReports401:
    def test_401_prints_invalid_token(self, monkeypatch, capsys):
        import requests

        resp = requests.Response()
        resp.status_code = 401
        gh = KospexGithub()
        monkeypatch.setattr(gh, "_make_request", lambda url, **kw: (None, resp))
        assert gh.test_auth() is False
        assert "Invalid token" in capsys.readouterr().out

    def test_no_response_is_a_request_failure(self, monkeypatch, capsys):
        gh = KospexGithub()
        monkeypatch.setattr(gh, "_make_request", lambda url, **kw: (None, None))
        assert gh.test_auth() is False
        assert "request failed" in capsys.readouterr().out


class TestKgitTestAuthCli:
    """`kgit github -test-auth` messages, with test_auth stubbed (no network)."""

    TOKEN = "ghp_CLITOKENCLITOKENCLITOKENCLITOKEN00"

    def run(self, monkeypatch, ok=True, **values):
        from click.testing import CliRunner
        import kgit

        for var, value in values.items():
            monkeypatch.setenv(var, value)
        monkeypatch.setattr(KospexGithub, "test_auth", lambda self: ok)
        return CliRunner().invoke(kgit.cli, ["github", "-test-auth"])

    @pytest.mark.parametrize("var", VARS)
    def test_names_the_source_variable(self, monkeypatch, var):
        result = self.run(monkeypatch, **{var: self.TOKEN})
        assert result.exit_code == 0
        assert f"Using token from {var}" in result.output
        assert self.TOKEN not in result.output

    def test_missing_names_all_three(self, monkeypatch):
        result = self.run(monkeypatch)
        assert result.exit_code == 1
        assert "Could not find" in result.output
        for var in VARS:
            assert var in result.output
        assert "Using token from" not in result.output

    def test_help_lists_all_three_in_order(self):
        from click.testing import CliRunner
        import kgit

        out = " ".join(CliRunner().invoke(kgit.cli, ["github", "--help"]).output.split())
        assert "GITHUB_AUTH_TOKEN, GH_TOKEN, GITHUB_TOKEN" in out

    def test_failed_auth_exits_1(self, monkeypatch):
        result = self.run(monkeypatch, ok=False, GH_TOKEN=self.TOKEN)
        assert result.exit_code == 1
        assert "Using token from GH_TOKEN" in result.output
        assert self.TOKEN not in result.output


# --- live: the real GitHub API ---------------------------------------------

live = pytest.mark.skipif(not LIVE_TOKEN, reason="KOSPEX_TEST_GITHUB_TOKEN not set")

# Each row puts the real token in some variables and INVALID in the others, so
# authenticating or not shows which variable was used.
LIVE_ROWS = [
    pytest.param({"GITHUB_AUTH_TOKEN": "valid"}, True, id="auth-alone"),
    pytest.param({"GH_TOKEN": "valid"}, True, id="gh-alone"),
    pytest.param({"GITHUB_TOKEN": "valid"}, True, id="github-alone"),
    pytest.param({"GITHUB_AUTH_TOKEN": "valid", "GH_TOKEN": "invalid"}, True, id="auth-beats-gh"),
    pytest.param({"GITHUB_AUTH_TOKEN": "invalid", "GH_TOKEN": "valid"}, False,
                 id="auth-beats-gh-proof"),
    pytest.param({"GITHUB_AUTH_TOKEN": "valid", "GITHUB_TOKEN": "invalid"}, True,
                 id="auth-beats-github"),
    pytest.param({"GITHUB_AUTH_TOKEN": "invalid", "GITHUB_TOKEN": "valid"}, False,
                 id="auth-beats-github-proof"),
    pytest.param({"GH_TOKEN": "valid", "GITHUB_TOKEN": "invalid"}, True, id="gh-beats-github"),
    pytest.param({"GH_TOKEN": "invalid", "GITHUB_TOKEN": "valid"}, False,
                 id="gh-beats-github-proof"),
    pytest.param({"GITHUB_AUTH_TOKEN": "valid", "GH_TOKEN": "invalid",
                  "GITHUB_TOKEN": "invalid"}, True, id="all-three"),
]


@pytest.mark.live
@live
@pytest.mark.parametrize("specs, authenticates", LIVE_ROWS)
def test_live_precedence(monkeypatch, capsys, specs, authenticates):
    values = {var: LIVE_TOKEN if s == "valid" else INVALID for var, s in specs.items()}
    gh, found = credentials(monkeypatch, **values)
    assert found is True
    assert gh.test_auth() is authenticates
    assert LIVE_TOKEN not in capsys.readouterr().out
