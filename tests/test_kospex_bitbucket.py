"""
Tests for KospexBitbucket — credential mode selection, basic-auth tuple
construction, and auth-test status code handling.
"""
import pytest
from unittest.mock import patch, Mock

from kospex_bitbucket import (
    KospexBitbucket,
    MODE_TOKEN,
    MODE_LEGACY,
    MODE_NONE,
    MODE_CONFIG_ERROR,
    TOKEN_ONLY_USERNAME,
    REQUIRED_SCOPES,
)


BB_ENV_VARS = (
    "BITBUCKET_API_TOKEN",
    "BITBUCKET_EMAIL",
    "BITBUCKET_USERNAME",
    "BITBUCKET_APP_PASSWORD",
)


def _clear_env(monkeypatch):
    for name in BB_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


# ---------------------------------------------------------------------------
# get_env_credentials mode selection
# ---------------------------------------------------------------------------

def test_env_creds_token_only(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("BITBUCKET_API_TOKEN", "tok")

    bb = KospexBitbucket()
    mode, reason = bb.get_env_credentials()

    assert mode == MODE_TOKEN
    assert reason is None
    assert bb.api_token == "tok"
    assert bb.email == ""
    assert bb.username == ""


def test_env_creds_token_plus_email(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("BITBUCKET_API_TOKEN", "tok")
    monkeypatch.setenv("BITBUCKET_EMAIL", "user@example.com")

    bb = KospexBitbucket()
    mode, reason = bb.get_env_credentials()

    assert mode == MODE_TOKEN
    assert reason is None
    assert bb.email == "user@example.com"
    assert bb.username == ""


def test_env_creds_token_plus_username(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("BITBUCKET_API_TOKEN", "tok")
    monkeypatch.setenv("BITBUCKET_USERNAME", "bbuser")

    bb = KospexBitbucket()
    mode, reason = bb.get_env_credentials()

    assert mode == MODE_TOKEN
    assert reason is None
    assert bb.username == "bbuser"
    assert bb.email == ""


def test_env_creds_token_with_both_email_and_username_is_config_error(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("BITBUCKET_API_TOKEN", "tok")
    monkeypatch.setenv("BITBUCKET_EMAIL", "user@example.com")
    monkeypatch.setenv("BITBUCKET_USERNAME", "bbuser")

    bb = KospexBitbucket()
    mode, reason = bb.get_env_credentials()

    assert mode == MODE_CONFIG_ERROR
    assert reason and "mutually exclusive" in reason
    # On config error we should not have populated the instance
    assert bb.api_token == ""


def test_env_creds_legacy(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("BITBUCKET_USERNAME", "bbuser")
    monkeypatch.setenv("BITBUCKET_APP_PASSWORD", "appp")

    bb = KospexBitbucket()
    mode, reason = bb.get_env_credentials()

    assert mode == MODE_LEGACY
    assert reason and "deprecated" in reason
    assert bb.username == "bbuser"
    assert bb.app_password == "appp"


def test_env_creds_token_wins_over_legacy(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("BITBUCKET_API_TOKEN", "tok")
    monkeypatch.setenv("BITBUCKET_USERNAME", "bbuser")
    monkeypatch.setenv("BITBUCKET_APP_PASSWORD", "appp")

    bb = KospexBitbucket()
    mode, _ = bb.get_env_credentials()

    assert mode == MODE_TOKEN
    assert bb.api_token == "tok"
    # app_password should be ignored when a token is present
    assert bb.app_password == ""


def test_env_creds_none(monkeypatch):
    _clear_env(monkeypatch)

    bb = KospexBitbucket()
    mode, reason = bb.get_env_credentials()

    assert mode == MODE_NONE
    assert reason is None


# ---------------------------------------------------------------------------
# _auth_tuple
# ---------------------------------------------------------------------------

def test_auth_tuple_token_with_email():
    bb = KospexBitbucket()
    bb.set_api_token("tok")
    bb.set_email("user@example.com")

    assert bb._auth_tuple() == ("user@example.com", "tok")


def test_auth_tuple_token_with_username():
    bb = KospexBitbucket()
    bb.set_api_token("tok")
    bb.set_username("bbuser")

    assert bb._auth_tuple() == ("bbuser", "tok")


def test_auth_tuple_token_email_wins_over_username():
    bb = KospexBitbucket()
    bb.set_api_token("tok")
    bb.set_email("user@example.com")
    bb.set_username("bbuser")

    # Direct setter usage bypasses the env-var mutual-exclusion check, so
    # _auth_tuple just picks: email first.
    assert bb._auth_tuple() == ("user@example.com", "tok")


def test_auth_tuple_token_only_falls_back_to_static_username():
    bb = KospexBitbucket()
    bb.set_api_token("tok")

    assert bb._auth_tuple() == (TOKEN_ONLY_USERNAME, "tok")


def test_auth_tuple_legacy():
    bb = KospexBitbucket()
    bb.set_username("bbuser")
    bb.set_app_password("appp")

    assert bb._auth_tuple() == ("bbuser", "appp")


# ---------------------------------------------------------------------------
# test_auth
# ---------------------------------------------------------------------------

def _mock_response(status_code):
    resp = Mock()
    resp.status_code = status_code
    return resp


def test_test_auth_200_ok():
    bb = KospexBitbucket()
    bb.set_api_token("tok")
    bb.set_email("user@example.com")

    with patch("kospex_bitbucket.requests.get", return_value=_mock_response(200)) as g:
        ok, status = bb.test_auth(workspace_id="myws")

    assert ok is True
    assert status == 200
    # Verify we hit the workspace repos endpoint, not /user or /workspaces
    called_url = g.call_args[0][0]
    assert called_url.startswith(
        "https://api.bitbucket.org/2.0/repositories/myws"
    )


def test_test_auth_401_bad_credentials():
    bb = KospexBitbucket()
    bb.set_api_token("bad")

    with patch("kospex_bitbucket.requests.get", return_value=_mock_response(401)):
        ok, status = bb.test_auth(workspace_id="myws")

    assert ok is False
    assert status == 401


def test_test_auth_403_missing_scopes():
    bb = KospexBitbucket()
    bb.set_api_token("tok-without-scopes")

    with patch("kospex_bitbucket.requests.get", return_value=_mock_response(403)):
        ok, status = bb.test_auth(workspace_id="myws")

    assert ok is False
    assert status == 403


def test_test_auth_uses_instance_workspace_when_no_arg():
    bb = KospexBitbucket()
    bb.set_api_token("tok")
    bb.set_email("user@example.com")
    bb.set_workspace_id("instance-ws")

    with patch("kospex_bitbucket.requests.get", return_value=_mock_response(200)) as g:
        ok, _ = bb.test_auth()

    assert ok is True
    called_url = g.call_args[0][0]
    assert "instance-ws" in called_url


def test_test_auth_requires_workspace():
    bb = KospexBitbucket()
    bb.set_api_token("tok")
    bb.set_email("user@example.com")

    with pytest.raises(ValueError, match="workspace_id"):
        bb.test_auth()


# ---------------------------------------------------------------------------
# REQUIRED_SCOPES is the set we promise in --help and the 403 error
# ---------------------------------------------------------------------------

def test_required_scopes_are_documented_strings():
    assert REQUIRED_SCOPES == (
        "read:project:bitbucket",
        "read:repository:bitbucket",
        "read:workspace:bitbucket",
    )
