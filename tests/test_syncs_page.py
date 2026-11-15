"""kweb /syncs/: the handler, called directly against a throwaway DB.

Not TestClient: starlette's needs httpx2, which isn't installed, so web tests
that use it skip. The page's data rules are in tests/test_syncs_view.py; these
check the route renders them, refuses bad query strings, escapes what it shows
and copes with a DB from before migration 0013.
"""
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import kospex.org_sync_manifest as M


def _ago(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


@pytest.fixture
def db(tmp_path, monkeypatch):
    from kospex.habitat_config import HabitatConfig
    import kospex_schema as KospexSchema
    monkeypatch.setenv("KOSPEX_HOME", str(tmp_path))
    HabitatConfig.reset_instance()
    db = KospexSchema.connect_or_create_kospex_db()
    db["repos"].insert_all([
        {"_repo_id": "github.com~acme~old", "_git_server": "github.com",
         "_git_owner": "acme", "_git_repo": "old", "last_fetch": _ago(40), "last_seen": _ago(10),
         "last_sync": _ago(40)},
        {"_repo_id": "github.com~acme~fresh", "_git_server": "github.com",
         "_git_owner": "acme", "_git_repo": "fresh", "last_fetch": _ago(1),
         "last_sync": _ago(1)},
        {"_repo_id": "github.com~acme~<script>alert(1)</script>",
         "_git_server": "github.com", "_git_owner": "acme",
         "_git_repo": "<script>alert(1)</script>", "last_sync": _ago(90)},
    ], pk="_repo_id", alter=True)
    yield db
    HabitatConfig.reset_instance()


def _get(**params):
    import kweb2
    scope = {"type": "http", "method": "GET", "path": "/syncs/", "headers": [],
             "query_string": urlencode(params).encode()}
    response = kweb2.syncs(Request(scope))
    return response.status_code, response.body.decode()


def test_repos_tab_shows_the_count_and_its_command(db):
    status, body = _get()
    assert status == 200
    assert "2 of 3" in body
    assert "kgit pull --all --stale 30" in body
    assert "kgit pull --check --all --stale 30" in body
    assert "github.com~acme~old" in body
    assert "github.com~acme~fresh" not in body.split("<tbody")[1]
    assert ">Sync Age (days)</th>" in body


def test_an_org_and_days_narrow_the_table_and_the_command(db):
    status, body = _get(org="github.com~acme", days="all")
    assert status == 200
    assert "kgit pull --org &#39;github.com~acme&#39;" in body
    assert "github.com~acme~fresh" in body


def test_repo_status_is_a_pill(db):
    status, body = _get(days="all")
    table = body.split("<tbody")[1]
    assert 'rounded-full bg-green-100 text-green-800">Active</span>' in table
    assert 'rounded-full bg-gray-100 text-gray-500">Unknown</span>' in table


def test_the_picker_only_navigates_within_syncs(db):
    # CWE-79 (DOM text reinterpreted as HTML): the select navigates to a fixed
    # /syncs/? prefix plus the option's query string, never to the option's text
    # alone, so no option value can become a javascript: URL.
    import re
    status, body = _get()
    select = re.search(r"<select[^>]*>.*?</select>", body, re.S).group(0)
    assert "window.location.assign('/syncs/?' + this.value)" in select
    values = re.findall(r'<option value="([^"]*)"', select)
    assert values and all(v.startswith("tab=") for v in values)


def test_nothing_stale_says_all_repos_are_refreshed(db):
    message = "All repos refreshed within specified window."
    status, body = _get(days="100")
    assert status == 200
    assert message in body
    assert "0 of 3" in body
    assert message not in _get()[1]           # 2 of 3 are stale
    assert message not in _get(days="all")[1]  # no window


@pytest.mark.parametrize("params", [
    {"tab": "other"}, {"org": "github.com~acme", "server": "github.com"},
    {"org": "nope"}, {"days": "0"}, {"days": "soon"},
])
def test_bad_query_strings_are_400(db, params):
    with pytest.raises(HTTPException) as raised:
        _get(**params)
    assert raised.value.status_code == 400


def test_repo_ids_are_escaped(db):
    # Review focus: a repo id from the DB is text, never markup.
    status, body = _get(days="all")
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body


def test_repo_dates_show_the_time_in_lighter_gray(db):
    import re
    status, body = _get(days="all")
    table = body.split("<tbody")[1]
    date = _ago(40)[:10]
    assert re.search(date + r'<span class="text-gray-400">T\d\d:\d\d:\d\d', table)
    # A value with no time stays as it is.
    assert ">never<" in table and ">-<" in table


def test_org_syncs_tab_shows_runs_and_commands(db):
    run_id = M.save_listing(db, "github.com~acme", [{
        "_repo_id": "github.com~acme~old", "clone_url": "https://github.com/acme/old.git",
        "ssh_url": "git@github.com:acme/old.git", "provider_repo_id": "github.com:1",
        "archived": False, "fork": False, "private": False,
        "pushed_at": "2026-11-01T00:00:00Z"}])
    command_id = M.start_command(db, "github.com~acme", "kgit sync --org acme -next 1 -ssh",
                                 "-next 1 -ssh", {"login": "octocat", "kind": "classic",
                                                  "source": "GH_TOKEN", "expires_at": None})
    M.finish_command(db, command_id, run_id=run_id, listing=None, synced_any=True, synced=1,
                     failed=0, skipped=0, new=None, gone=None)
    status, body = _get(tab="orgs")
    assert status == 200
    assert run_id in body
    assert "kgit sync --org acme -next 1 -ssh" in body
    assert "octocat · classic · GH_TOKEN" in body


def test_org_syncs_tab_on_a_db_behind_0013(db):
    # Review focus: kweb doesn't migrate; the page says how, rather than a 500.
    db.execute(f"DROP TABLE {M.TBL_ORG_SYNC_COMMANDS}")
    db.execute(f"DROP TABLE {M.TBL_ORG_SYNC_LISTINGS}")
    status, body = _get(tab="orgs")
    assert status == 200
    assert "kospex upgrade-db" in body


def test_recent_is_gone():
    # /recent/ was a stub that always rendered an empty table.
    import kweb2
    assert "/recent/" not in {getattr(route, "path", None) for route in kweb2.app.routes}


def test_not_linked_from_the_header():
    from pathlib import Path
    import kweb2
    header = Path(kweb2.__file__).parent / "templates" / "_header.html"
    assert "/syncs/" not in header.read_text()
