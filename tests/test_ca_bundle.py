"""REQUESTS_CA_BUNDLE handling and failed-lookup reporting (#453).

requests ignores the OS certificate store and SSL_CERT_FILE, so behind a
TLS-intercepting proxy the fix is REQUESTS_CA_BUNDLE. Three things went
wrong around it:

- `~` in kospex.env is not expanded by python-dotenv, and requests opens the
  path as given;
- a bundle path that doesn't exist raises a plain OSError (not a
  RequestException) on the first lookup, which ended sca and krunner osi with
  a traceback;
- when every lookup failed TLS verification, the rows were lookup_error and
  nothing said why.
"""
import os

import pytest
import requests
from click.testing import CliRunner

import kospex_utils as KospexUtils


@pytest.fixture
def no_bundle_env(monkeypatch):
    """Both variables unset, and removed again at teardown.

    Set first, then deleted: delenv(raising=False) on an unset variable
    records nothing to restore, so a value load_dotenv() writes straight into
    os.environ leaked into every later test (and they all failed).
    """
    for var in KospexUtils.CA_BUNDLE_VARS:
        monkeypatch.setenv(var, "")
        monkeypatch.delenv(var)
    return monkeypatch


class TestExpandCaBundlePaths:

    def test_a_leading_tilde_is_expanded(self, no_bundle_env, tmp_path):
        no_bundle_env.setenv("HOME", str(tmp_path))
        no_bundle_env.setenv("REQUESTS_CA_BUNDLE", "~/certs/ca.pem")
        no_bundle_env.setenv("CURL_CA_BUNDLE", "~/curl.pem")

        KospexUtils.expand_ca_bundle_paths()

        assert os.environ["REQUESTS_CA_BUNDLE"] == str(tmp_path / "certs" / "ca.pem")
        assert os.environ["CURL_CA_BUNDLE"] == str(tmp_path / "curl.pem")

    def test_a_full_path_is_unchanged(self, no_bundle_env):
        no_bundle_env.setenv("REQUESTS_CA_BUNDLE", "/etc/ssl/certs/ca-certificates.crt")

        KospexUtils.expand_ca_bundle_paths()

        assert os.environ["REQUESTS_CA_BUNDLE"] == "/etc/ssl/certs/ca-certificates.crt"

    def test_kospex_env_is_expanded_when_loaded(self, no_bundle_env, tmp_path):
        no_bundle_env.setenv("HOME", str(tmp_path))
        env_file = tmp_path / "kospex.env"
        env_file.write_text("REQUESTS_CA_BUNDLE=~/ca.pem\n")

        KospexUtils.load_config(str(env_file))

        assert os.environ["REQUESTS_CA_BUNDLE"] == str(tmp_path / "ca.pem")


class TestCaBundleProblem:

    def test_none_when_unset(self, no_bundle_env):
        assert KospexUtils.ca_bundle_problem() is None

    def test_none_when_the_file_exists(self, no_bundle_env, tmp_path):
        bundle = tmp_path / "ca.pem"
        bundle.write_text("x")
        no_bundle_env.setenv("REQUESTS_CA_BUNDLE", str(bundle))

        assert KospexUtils.ca_bundle_problem() is None

    def test_names_the_variable_and_path_when_missing(self, no_bundle_env, tmp_path):
        missing = tmp_path / "nope.pem"
        no_bundle_env.setenv("REQUESTS_CA_BUNDLE", str(missing))

        problem = KospexUtils.ca_bundle_problem()

        assert "REQUESTS_CA_BUNDLE" in problem and str(missing) in problem

    def test_curl_ca_bundle_is_checked_when_requests_ca_bundle_is_empty(
            self, no_bundle_env, tmp_path):
        """requests reads REQUESTS_CA_BUNDLE or, when that is empty, CURL_CA_BUNDLE."""
        no_bundle_env.setenv("REQUESTS_CA_BUNDLE", "")
        no_bundle_env.setenv("CURL_CA_BUNDLE", str(tmp_path / "nope.pem"))

        assert "CURL_CA_BUNDLE" in KospexUtils.ca_bundle_problem()

    def test_curl_ca_bundle_is_ignored_when_requests_ca_bundle_is_set(
            self, no_bundle_env, tmp_path):
        bundle = tmp_path / "ca.pem"
        bundle.write_text("x")
        no_bundle_env.setenv("REQUESTS_CA_BUNDLE", str(bundle))
        no_bundle_env.setenv("CURL_CA_BUNDLE", str(tmp_path / "nope.pem"))

        assert KospexUtils.ca_bundle_problem() is None

    def test_a_directory_is_accepted(self, no_bundle_env, tmp_path):
        """requests accepts a directory of certificates (ca_certs_dir)."""
        no_bundle_env.setenv("REQUESTS_CA_BUNDLE", str(tmp_path))

        assert KospexUtils.ca_bundle_problem() is None


class TestUrlFailureSummary:

    def test_nothing_when_nothing_failed(self):
        assert KospexUtils.url_failure_summary({}) == []
        assert KospexUtils.url_failure_summary(None) == []

    def test_ssl_failures_get_the_ca_bundle_advice(self):
        error = requests.exceptions.SSLError("CERTIFICATE_VERIFY_FAILED")
        lines = KospexUtils.url_failure_summary({"SSLError": (5, error)})
        text = "\n".join(lines)

        assert "5 requests" in text and "SSLError x5" in text
        assert "lookup_error" in text
        assert "CERTIFICATE_VERIFY_FAILED" in text
        assert "REQUESTS_CA_BUNDLE" in text

    def test_other_failures_show_the_first_error(self):
        error = requests.exceptions.ConnectionError("Name or service not known")
        text = "\n".join(KospexUtils.url_failure_summary({"ConnectionError": (1, error)}))

        assert "1 request to external services failed" in text
        assert "Name or service not known" in text
        assert "REQUESTS_CA_BUNDLE" not in text


class TestUrlFailuresAreCounted:

    def test_each_failure_is_counted_and_the_first_kept(self, tmp_path, monkeypatch):
        import kospex_query
        from kospex_query import KospexQuery
        from sqlite_utils import Database

        monkeypatch.setenv("KOSPEX_URL_CACHE_SECONDS", "0")
        errors = iter([requests.exceptions.SSLError("first"),
                       requests.exceptions.SSLError("second"),
                       requests.exceptions.ConnectionError("dns")])

        def fake_get(url, **_kwargs):
            raise next(errors)

        monkeypatch.setattr(kospex_query.requests, "get", fake_get)
        db = Database(str(tmp_path / "k.db"))
        db["url_cache"].create({"url": str, "content": str, "timestamp": int}, pk="url")
        query = KospexQuery(kospex_db=db)
        for _ in range(3):
            assert query.url_request_with_status("https://example.com/x") == (None, None)

        assert query.url_failures["SSLError"][0] == 2
        assert str(query.url_failures["SSLError"][1]) == "first"
        assert query.url_failures["ConnectionError"][0] == 1


class TestKrunnerOsi:

    def test_a_missing_ca_bundle_stops_osi_before_scanning(self, no_bundle_env, tmp_path):
        import krunner

        no_bundle_env.setenv("REQUESTS_CA_BUNDLE", str(tmp_path / "nope.pem"))
        no_bundle_env.setenv("KOSPEX_DEPS_ENRICH", "true")
        result = CliRunner().invoke(krunner.cli, ["osi", "-all"])

        assert result.exit_code == 1
        assert "REQUESTS_CA_BUNDLE" in result.output and "does not exist" in result.output

    def test_no_enrich_does_not_need_a_ca_bundle(self, no_bundle_env, tmp_path,
                                                 monkeypatch):
        """Parsing only contacts nothing, so a bad path is not its problem."""
        import krunner

        no_bundle_env.setenv("REQUESTS_CA_BUNDLE", str(tmp_path / "nope.pem"))
        # No database: the run and its pre-run reports are stubbed.
        for name in ("_run_osi_next", "_report_earlier_lookups", "_report_private_lookups"):
            monkeypatch.setattr(krunner, name, lambda *a, **k: None)
        result = CliRunner().invoke(krunner.cli, ["osi", "-no-enrich", "-next", "1"])

        assert "does not exist" not in result.output

    def test_failures_are_reported_after_the_run(self, capsys):
        import krunner

        class Query:
            url_failures = {"SSLError": (3, requests.exceptions.SSLError("VERIFY"))}

        krunner._report_url_failures(Query())
        out = capsys.readouterr().out

        assert "3 requests" in out and "REQUESTS_CA_BUNDLE" in out

    def test_nothing_reported_without_failures(self, capsys):
        import krunner

        class Query:
            url_failures = {}

        krunner._report_url_failures(Query())

        assert capsys.readouterr().out == ""
