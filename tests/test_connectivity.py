"""`kospex connectivity` and kospex.ca_bundles (#453).

connectivity -save used to write the certificate api.deps.dev presented to
~/kospex/REQUEST_CA_CERTS and suggest it as REQUESTS_CA_BUNDLE. A server
certificate can't verify itself (verification needs the CA that issued it),
so that advice never worked. connectivity now finds a system CA bundle that
verifies the host, and -save writes it to kospex.env.

Nothing here touches the network: requests.get, and the certificate probe,
are replaced.
"""
import os
import ssl
from types import SimpleNamespace

import pytest
import requests
from click.testing import CliRunner

from kospex import ca_bundles


def _bundle(tmp_path, name):
    path = tmp_path / name
    path.write_text("-----BEGIN CERTIFICATE-----\n")
    return str(path)


@pytest.fixture
def no_bundle_env(monkeypatch):
    """Both variables unset, and restored at teardown (see test_ca_bundle.py)."""
    for var in ca_bundles.BUNDLE_VARS:
        monkeypatch.setenv(var, "")
        monkeypatch.delenv(var)
    return monkeypatch


@pytest.fixture
def system_paths(monkeypatch, tmp_path):
    """Python's default bundle is `default.pem`; the known paths are a, b."""
    default = _bundle(tmp_path, "default.pem")
    monkeypatch.setattr(ssl, "get_default_verify_paths",
                        lambda: SimpleNamespace(cafile=default, openssl_cafile=default))
    known = (_bundle(tmp_path, "a.pem"), str(tmp_path / "missing.pem"),
             _bundle(tmp_path, "b.pem"))
    monkeypatch.setattr(ca_bundles, "KNOWN_BUNDLE_PATHS", known)
    return default, known


def _verify_only_with(*good):
    """A requests.get that succeeds when verify= is one of `good`."""
    def fake_get(url, verify=True, **_kwargs):
        if verify in good:
            return SimpleNamespace(status_code=404)
        raise requests.exceptions.SSLError("CERTIFICATE_VERIFY_FAILED")
    return fake_get


class TestSystemBundles:

    def test_default_first_then_known_paths_that_exist(self, system_paths):
        default, known = system_paths

        assert ca_bundles.system_bundles() == [default, known[0], known[2]]

    def test_a_symlink_to_a_listed_bundle_is_listed_once(self, system_paths, tmp_path,
                                                         monkeypatch):
        default, known = system_paths
        link = tmp_path / "link.pem"
        link.symlink_to(known[0])
        monkeypatch.setattr(ca_bundles, "KNOWN_BUNDLE_PATHS", (known[0], str(link)))

        assert ca_bundles.system_bundles() == [default, known[0]]


class TestBundleInUse:

    def test_certifi_when_nothing_is_set(self, no_bundle_env):
        import certifi

        assert ca_bundles.bundle_in_use() == (None, certifi.where())

    def test_requests_ca_bundle_wins(self, no_bundle_env):
        no_bundle_env.setenv("REQUESTS_CA_BUNDLE", "/r.pem")
        no_bundle_env.setenv("CURL_CA_BUNDLE", "/c.pem")

        assert ca_bundles.bundle_in_use() == ("REQUESTS_CA_BUNDLE", "/r.pem")

    def test_curl_ca_bundle_when_requests_ca_bundle_is_unset(self, no_bundle_env):
        no_bundle_env.setenv("CURL_CA_BUNDLE", "/c.pem")

        assert ca_bundles.bundle_in_use() == ("CURL_CA_BUNDLE", "/c.pem")


class TestCheckBundle:

    def test_any_response_is_verified(self, monkeypatch):
        monkeypatch.setattr(ca_bundles.requests, "get", _verify_only_with("/good.pem"))

        assert ca_bundles.check_bundle("/good.pem", "https://x").verified

    def test_an_ssl_failure(self, monkeypatch):
        monkeypatch.setattr(ca_bundles.requests, "get", _verify_only_with())
        check = ca_bundles.check_bundle("/bad.pem", "https://x")

        assert not check.verified and check.ssl_error

    def test_a_bad_path_is_a_failure_not_a_crash(self, monkeypatch):
        def fake_get(url, **_kwargs):
            raise OSError("Could not find a suitable TLS CA certificate bundle")

        monkeypatch.setattr(ca_bundles.requests, "get", fake_get)
        check = ca_bundles.check_bundle("/nope.pem", "https://x")

        assert not check.verified and not check.ssl_error
        assert "OSError" in check.error

    def test_find_working_bundles_skips_the_one_in_use(self, system_paths, monkeypatch):
        default, known = system_paths
        monkeypatch.setattr(ca_bundles.requests, "get", _verify_only_with(known[2]))
        checks = ca_bundles.find_working_bundles("https://x", exclude=[default])

        assert [(c.path, c.verified) for c in checks] == [(known[0], False),
                                                          (known[2], True)]


class TestSaveToKospexEnv:

    def test_creates_the_file(self, tmp_path):
        env_file = tmp_path / "kospex" / "kospex.env"

        assert ca_bundles.save_to_kospex_env(str(env_file), "/etc/ssl/cert.pem") is None
        assert env_file.read_text() == "REQUESTS_CA_BUNDLE=/etc/ssl/cert.pem\n"

    def test_replaces_the_value_and_keeps_everything_else(self, tmp_path):
        env_file = tmp_path / "kospex.env"
        env_file.write_text("# mine\nexport REQUESTS_CA_BUNDLE=~/old.pem\nGITHUB_TOKEN=t\n")

        previous = ca_bundles.save_to_kospex_env(str(env_file), "/etc/ssl/cert.pem")

        assert previous == "~/old.pem"
        assert env_file.read_text() == (
            "# mine\nREQUESTS_CA_BUNDLE=/etc/ssl/cert.pem\nGITHUB_TOKEN=t\n")


@pytest.fixture
def run_connectivity(no_bundle_env, system_paths, tmp_path):
    """`kospex connectivity ARGS` with deps.dev verifying only with `good`
    bundles, the certificate probe stubbed, and kospex.env in tmp_path."""
    import kospex_cli
    from kospex.habitat_config import HabitatConfig

    home = tmp_path / "kospex_home"
    home.mkdir()
    no_bundle_env.setenv("KOSPEX_HOME", str(home))
    no_bundle_env.setenv("KOSPEX_CONFIG", str(home / "kospex.env"))
    HabitatConfig.reset_instance()
    no_bundle_env.setattr(kospex_cli, "_show_presented_certificate",
                          lambda host: print("PRESENTED-CERT"))

    def run(*args, good=(), fake_get=None):
        get = fake_get or _verify_only_with(*good)

        # One module object: kospex_cli.requests is ca_bundles.requests. A call
        # without verify= is the bundle in use: REQUESTS_CA_BUNDLE, or certifi.
        def fake(url, verify=None, **kw):
            if verify is None:
                verify = os.environ.get("REQUESTS_CA_BUNDLE") or "certifi"
            return get(url, verify=verify, **kw)

        no_bundle_env.setattr(requests, "get", fake)
        result = CliRunner().invoke(kospex_cli.cli, ["connectivity", *args])
        run.env_file = home / "kospex.env"
        return result

    run.paths = system_paths
    return run


class TestConnectivityCommand:

    def test_success(self, run_connectivity):
        result = run_connectivity(good=("certifi",))

        assert result.exit_code == 0, result.output
        assert "SSL Verification: PASSED" in result.output
        assert "Checking the system CA bundles" not in result.output

    def test_success_with_save_saves_nothing(self, run_connectivity):
        result = run_connectivity("-save", good=("certifi",))

        assert "Nothing saved" in result.output
        assert not run_connectivity.env_file.exists()

    def test_an_ssl_failure_names_a_system_bundle_that_works(self, run_connectivity):
        default, known = run_connectivity.paths
        result = run_connectivity(good=(known[0],))

        assert result.exit_code == 0, result.output
        assert "SSL Error detected" in result.output
        assert "PRESENTED-CERT" in result.output
        assert f"✓ {known[0]}  verifies api.deps.dev" in result.output
        assert f"✗ {default}" in result.output
        assert f"export REQUESTS_CA_BUNDLE={known[0]}" in result.output
        assert not run_connectivity.env_file.exists()

    def test_save_writes_the_working_bundle_to_kospex_env(self, run_connectivity):
        _, known = run_connectivity.paths
        result = run_connectivity("-save", good=(known[2],))

        assert result.exit_code == 0, result.output
        assert run_connectivity.env_file.read_text() == f"REQUESTS_CA_BUNDLE={known[2]}\n"
        assert "Saved REQUESTS_CA_BUNDLE" in result.output
        assert "also set in your environment" not in result.output

    def test_save_warns_when_the_shell_value_would_still_win(self, run_connectivity,
                                                             no_bundle_env, tmp_path):
        _, known = run_connectivity.paths
        no_bundle_env.setenv("REQUESTS_CA_BUNDLE", _bundle(tmp_path, "shell.pem"))
        result = run_connectivity("-save", good=(known[0],))

        assert run_connectivity.env_file.read_text() == f"REQUESTS_CA_BUNDLE={known[0]}\n"
        assert "also set in your environment" in result.output
        assert "unset REQUESTS_CA_BUNDLE" in result.output

    def test_no_server_certificate_file_is_written(self, run_connectivity, tmp_path):
        """The old -save wrote REQUEST_CA_CERTS, which never verified."""
        _, known = run_connectivity.paths
        run_connectivity("-save", good=(known[0],))

        assert not list(tmp_path.rglob("REQUEST_CA_CERTS"))

    def test_nothing_works(self, run_connectivity):
        result = run_connectivity("-save", good=())

        assert "No CA bundle on this system verifies api.deps.dev" in result.output
        assert "update-ca-certificates" in result.output
        assert not run_connectivity.env_file.exists()

    def test_a_missing_bundle_path_looks_for_one_that_works(self, run_connectivity,
                                                            no_bundle_env, tmp_path):
        _, known = run_connectivity.paths
        no_bundle_env.setenv("REQUESTS_CA_BUNDLE", str(tmp_path / "nope.pem"))
        result = run_connectivity(good=(known[0],))

        assert result.exit_code == 0, result.output
        assert "does not exist" in result.output
        assert f"export REQUESTS_CA_BUNDLE={known[0]}" in result.output
        # The probe shows the presented certificate; there is none to show here.
        assert "PRESENTED-CERT" not in result.output

    def test_a_connection_error_does_not_look_for_bundles(self, run_connectivity):
        def fake_get(url, **_kwargs):
            raise requests.exceptions.ConnectionError("Name or service not known")

        result = run_connectivity(fake_get=fake_get)

        assert "Connection error" in result.output
        assert "Checking the system CA bundles" not in result.output
