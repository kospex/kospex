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
    # CWE-79 (DOM text reinterpreted as HTML): the select navigates by the
    # selected option's position into URLs the server rendered, never to text
    # read from the page, so nothing in an option can become a javascript: URL.
    import json
    import re
    status, body = _get()
    select = re.search(r"<select[^>]*>.*?</select>", body, re.S).group(0)
    assert "this.value" not in select
    assert "window.location.assign(syncsPickerUrls[this.selectedIndex])" in select
    urls = json.loads(re.search(r"const syncsPickerUrls = (\[.*?\]);", body).group(1))
    assert len(urls) == select.count("<option") > 1
    assert all(u.startswith("/syncs/?tab=") for u in urls)
    assert "/syncs/?tab=repos&org=github.com~acme&days=30" in urls


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


# --- /syncs/run/{run_id} -----------------------------------------------------

HOSTILE = "<script>alert(1)</script>"


def _item(name, **kw):
    return {"_repo_id": f"github.com~acme~{name}",
            "clone_url": f"https://github.com/acme/{name}.git",
            "ssh_url": f"git@github.com:acme/{name}.git",
            "provider_repo_id": f"github.com:{name}", "archived": False, "fork": False,
            "private": False, "visibility": "public", "pushed_at": "2026-11-01T00:00:00Z",
            **kw}


def _vis(visibility):
    return {"visibility": visibility, "private": visibility == "private"}


def _run(db, new="private", forked="public"):
    # `old` is in kospex and listed; `fresh` and the hostile id are in kospex
    # but not listed, so they're the drift.
    run_id = M.save_listing(db, "github.com~acme", [
        _item("old"), _item("new", **_vis(new)),
        _item("forked", fork=True, fork_parent="up/forked", fork_source="root/forked",
              **_vis(forked)),
    ])
    M.mark(db, run_id, "github.com~acme~new", M.FAILED, f"clone failed {HOSTILE}")
    return run_id


def _markers(items):
    import re
    return re.findall(r'class="visibility-marker[^"]*">([^<]*)<', items)


def _get_run(run_id):
    import kweb2
    scope = {"type": "http", "method": "GET", "path": f"/syncs/run/{run_id}",
             "headers": [], "query_string": b""}
    response = kweb2.syncs_run(Request(scope), run_id)
    return response.status_code, response.body.decode()


def test_run_page_shows_items_drift_and_visibility_banner(db):
    run_id = _run(db)
    status, body = _get_run(run_id)
    assert status == 200
    items, drift = body.split('id="itemsTable"')[1].split("In kospex, not in this listing")
    assert "github.com~acme~new" in items and "github.com~acme~forked" in items
    assert "up/forked" in items and "source: root/forked" in items
    assert 'data-search="failed"' in items
    assert _markers(items) == ["private"]
    assert "github.com~acme~fresh" in drift and "github.com~acme~old" not in drift
    assert "This run includes 1 private repo out of 3." in body
    assert 'id="visibility-note"' not in body


def test_internal_and_private_are_told_apart(db):
    # GitHub Enterprise internal repos are readable across the enterprise:
    # not private, and not public either.
    status, body = _get_run(_run(db, new="internal", forked="private"))
    items = body.split('id="itemsTable"')[1]
    assert _markers(items) == ["private", "internal"]  # forked, new: by repo id
    assert "This run includes 1 internal and 1 private repos out of 3." in body


def test_run_page_has_no_banner_when_every_repo_is_public(db):
    status, body = _get_run(_run(db, new="public"))
    assert status == 200
    assert 'id="visibility-banner"' not in body
    assert _markers(body) == []


def test_unrecorded_visibility_says_how_to_fill_it_in(db):
    # A run listed before 0014: visibility NULL, never guessed from private = 0.
    run_id = _run(db, new="internal")
    db.execute(f"UPDATE {M.TBL_ORG_SYNC_ITEMS} SET visibility = NULL")
    status, body = _get_run(run_id)
    assert ("Visibility not recorded for 3 repos. To update, without cloning, run the "
            "following:") in " ".join(body.split())  # as the browser shows it
    assert "kgit sync --org acme -inventory -refresh" in body
    assert 'id="visibility-banner"' not in body
    assert body.count(">not recorded<") == 3


def test_a_finished_run_relists_as_a_new_run(db):
    run_id = _run(db)
    db.execute(f"UPDATE {M.TBL_ORG_SYNC_ITEMS} SET visibility = NULL")
    db.execute(f"UPDATE {M.TBL_ORG_SYNC_RUNS} SET finished_at = '2026-11-20T00:00:00Z'")
    status, body = _get_run(run_id)
    assert ("This run is finished, so it can't be updated. To record visibility in a new "
            "run, without cloning, run the following:") in body
    assert "kgit sync --org acme -inventory<" in body
    assert "-refresh" not in body


def test_run_page_on_a_db_behind_0014(db):
    # Review focus: kweb doesn't migrate; the page says how, rather than a 500.
    run_id = _run(db)
    # transform() rebuilds the table: ALTER TABLE DROP COLUMN needs SQLite 3.35,
    # and kospex supports 3.26 (RHEL 9 ships 3.34).
    db[M.TBL_ORG_SYNC_ITEMS].transform(drop={"visibility"})
    status, body = _get_run(run_id)
    assert status == 200
    assert "kospex upgrade-db" in body


def test_run_page_escapes_repo_ids_and_details(db):
    # Review focus: repo ids and failure details are text, never markup.
    status, body = _get_run(_run(db))
    assert HOSTILE not in body
    assert "clone failed &lt;script&gt;alert(1)&lt;/script&gt;" in body
    assert "github.com~acme~&lt;script&gt;alert(1)&lt;/script&gt;" in body


def test_run_page_status_filter_reads_only_fixed_statuses(db):
    # CWE-79: the filter picks a status by the button's position in a list the
    # server rendered, never from text read from the page.
    import json
    import re
    status, body = _get_run(_run(db))
    statuses = json.loads(re.search(r"const runStatusFilters = (\[.*?\]);", body).group(1))
    assert statuses == ["", "failed", "pending"]
    assert "this.dataset.filter" in body and "this.textContent" not in body


def test_unknown_run_is_404(db):
    _run(db)
    with pytest.raises(HTTPException) as raised:
        _get_run("19990101T000000Z")
    assert raised.value.status_code == 404


def test_run_page_on_a_db_behind_0013(db):
    db.execute(f"DROP TABLE {M.TBL_ORG_SYNC_COMMANDS}")
    status, body = _get_run("anything")
    assert status == 200
    assert "kospex upgrade-db" in body


def test_org_syncs_tab_links_each_run(db):
    run_id = _run(db)
    status, body = _get(tab="orgs")
    assert f'href="/syncs/run/{run_id}"' in body


def test_run_page_not_linked_from_anywhere_but_syncs():
    from pathlib import Path
    import kweb2
    templates = Path(kweb2.__file__).parent / "templates"
    linking = sorted(p.name for p in templates.glob("*.html")
                     if "/syncs/run/" in p.read_text())
    assert linking == ["syncs.html"]


def _breadcrumb(body):
    import re
    nav = body.split('aria-label="Breadcrumb"')[1].split("</nav>")[0]
    return re.findall(r'<(?:a href="([^"]*)"|span)[^>]*>([^<]+)<', nav)


def test_run_page_has_the_org_breadcrumb_and_keeps_the_syncs_link(db):
    status, body = _get_run(_run(db))
    assert status == 200
    crumbs = [(href, text) for href, text in _breadcrumb(body) if text != "/"]
    assert crumbs == [("/orgs/github.com", "github.com"),
                      ("/org/github.com~acme", "acme"), ("", "Org sync run")]
    assert "← Syncs</a>" in body


def test_no_breadcrumb_before_the_org_has_repos_in_kospex(db):
    # Its crumbs would link to an org page that 404s.
    run_id = M.save_listing(db, "github.com~nobody", [
        {**_item("x"), "_repo_id": "github.com~nobody~x"}])
    status, body = _get_run(run_id)
    assert status == 200
    assert 'aria-label="Breadcrumb"' not in body
    assert "← Syncs</a>" in body


RUNS_NOTHING = ("kweb doesn't run commands. To sync repos, refresh the inventory or update "
                "repo details, copy a command from this page and run it in a terminal.")


def test_says_kweb_runs_nothing_when_there_is_a_command(db):
    status, body = _get_run(_run(db))
    assert 'id="continue-command"' in body
    assert RUNS_NOTHING in " ".join(body.split())


def test_no_such_sentence_when_the_page_has_no_command(db):
    # Finished, and every repo's visibility recorded: nothing to copy.
    run_id = _run(db)
    db.execute(f"UPDATE {M.TBL_ORG_SYNC_RUNS} SET finished_at = '2026-11-20T00:00:00Z'")
    status, body = _get_run(run_id)
    assert status == 200
    assert 'id="continue-command"' not in body and 'id="relist-command"' not in body
    assert "kweb doesn't run commands" not in body


@pytest.mark.parametrize("org_key, title", [
    ("github.com~acme", "github.com / acme"),
    # A GitLab subgroup: "~~" in the key is a "/" in the group's path.
    ("gitlab.com~group~~sub", "gitlab.com / group/sub"),
])
def test_run_page_title_is_server_slash_org(db, org_key, title):
    run_id = M.save_listing(db, org_key, [{**_item("x"), "_repo_id": f"{org_key}~x"}])
    status, body = _get_run(run_id)
    assert status == 200
    assert f">{title}</h1>" in body
    assert f"Repos kospex has under {title} that" in body
