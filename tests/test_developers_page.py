"""The /developers/ route: scopes, the active list, paging, search and CSV."""
import base64
import csv
import io
import re

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import kospex_utils as KospexUtils
from kospex_query import KospexQuery
from test_developers_page_queries import COMMITS, REPOS, _make_db


@pytest.fixture
def kweb():
    import kweb2
    kweb2.request_cache.clear()
    yield kweb2
    kweb2.request_cache.clear()


@pytest.fixture
def estate(tmp_path, monkeypatch, kweb):
    _make_db(tmp_path, monkeypatch, COMMITS)
    return kweb


def _big(tmp_path, monkeypatch, count):
    """count developers, all active, in one repo."""
    _make_db(tmp_path, monkeypatch, [
        ("github.com~acme~api", f"dev{i:04d}@example.com", 1 + i % 80, "c@example.com")
        for i in range(count)])


def _get(kweb, query="", id=None):
    request = Request({"type": "http", "method": "GET", "path": "/developers/",
                       "headers": [], "query_string": query.encode(), "app": kweb.app})
    response = kweb.developers(request, id=id)
    assert response.status_code == 200
    return response


def _html(kweb, query="", id=None):
    return _get(kweb, query, id).body.decode()


def _emails(kweb, query="", id=None):
    return [p["email"] for p in _get(kweb, query, id).context["people"]]


def _status(kweb, query="", id=None):
    with pytest.raises(HTTPException) as caught:
        _get(kweb, query, id)
    return caught.value.status_code, caught.value.detail


ACTIVE = {"paul@example.com", "mrpaul@example.com", "a_b@example.com",
          "axb@example.com", "pc%t@example.com", "o'neil@example.com"}
EVERYONE = ACTIVE | {"old@example.com", "aging@example.com", "stale@example.com"}


def test_default_shows_active_developers_only(estate):
    assert set(_emails(estate)) == ACTIVE
    assert set(_emails(estate, "status=all")) == EVERYONE


def test_counts_link_to_each_list_in_path_form(estate):
    context = _get(estate, id="github.com~acme").context
    links = {s["key"]: (s["count"], s["url"]) for s in context["status_links"]}
    assert links == {
        key: (count, f"/developers/github.com~acme?status={key}")
        for key, count in
        [("active", 2), ("aging", 1), ("stale", 0), ("unmaintained", 1), ("all", 4)]}
    html = _html(estate, id="github.com~acme")
    assert 'href="/developers/github.com~acme?status=aging"' in html
    assert context["data"] == {"authors": 4, "committers": 3}


@pytest.mark.parametrize("id, key, expected", [
    ("github.com", "server", EVERYONE - {"pc%t@example.com", "o'neil@example.com"}),
    ("github.com~acme", "org_key",
     {"paul@example.com", "mrpaul@example.com", "old@example.com", "aging@example.com"}),
    ("github.com~acme~api", "repo_id", {"paul@example.com", "old@example.com"}),
])
def test_scope_by_path_and_by_query(estate, id, key, expected):
    assert set(_emails(estate, "status=all", id=id)) == expected
    assert set(_emails(estate, f"status=all&{key}={id}")) == expected


def test_path_id_wins_over_the_query(estate):
    got = _emails(estate, "status=all&org_key=github.com~beta", id="github.com~acme~api")
    assert set(got) == {"paul@example.com", "old@example.com"}


def test_a_dotless_server_name_is_a_server(tmp_path, monkeypatch, kweb):
    assert KospexUtils.is_base64("git1"), "otherwise this proves nothing"
    _make_db(tmp_path, monkeypatch,
             [("git1~acme~api", "dev@example.com", 1, "dev@example.com")],
             repos={"git1~acme~api": ("git1", "acme", "api")})
    assert _emails(kweb, id="git1") == ["dev@example.com"]


def test_a_base64_email_path_points_to_the_developer_view(estate):
    status, detail = _status(estate, id=base64.b64encode(b"paul@example.com").decode())
    assert status == 404
    assert "?author_email=" in detail


@pytest.mark.parametrize("query, id", [
    ("", "nope.example.com"), ("", "github.com~nope"), ("", "github.com~acme~nope"),
    ("server=nope.example.com", None), ("org_key=github.com~nope", None),
    ("repo_id=github.com~acme~nope", None), ("org_key=not-an-org", None),
])
def test_an_unknown_scope_is_a_404(estate, query, id):
    assert _status(estate, query, id)[0] == 404


def test_long_lists_are_paged(tmp_path, monkeypatch, kweb):
    _big(tmp_path, monkeypatch, 251)
    response = _get(kweb, "status=all")
    assert len(response.context["people"]) == 50
    assert "Page 1 of 6" in response.body.decode()
    assert len(_emails(kweb, "status=all&page=6")) == 1

    past = _get(kweb, "status=all&page=99")
    assert past.context["people"] == []
    assert "There is no page 99" in past.body.decode()


def test_250_rows_are_shown_whole(tmp_path, monkeypatch, kweb):
    _big(tmp_path, monkeypatch, 250)
    response = _get(kweb)
    assert len(response.context["people"]) == 250
    assert "Page 1 of" not in response.body.decode()


def test_sort_and_page_links_keep_the_filters(tmp_path, monkeypatch, kweb):
    _big(tmp_path, monkeypatch, 251)
    html = _html(kweb, "status=all&q=dev&sort=email&dir=desc&page=2")
    assert 'href="/developers/?status=all&amp;q=dev&amp;sort=email&amp;dir=desc&amp;page=3"' in html
    assert 'href="/developers/?status=all&amp;q=dev&amp;sort=email&amp;dir=asc"' in html
    assert 'href="/developers/?status=all&amp;q=dev&amp;sort=commits&amp;dir=desc"' in html


def test_search_covers_every_status_and_shows_it(estate):
    response = _get(estate, "q=example.com")
    assert set(p["email"] for p in response.context["people"]) == EVERYONE
    assert ">Status</th>" in response.body.decode()
    assert ">Status</th>" not in _html(estate)
    assert _emails(estate, "q=example.com&status=stale") == ["stale@example.com"]


def test_search_text_is_escaped(estate):
    html = _html(estate, "q=%3Cscript%3Ealert(1)%3C%2Fscript%3E")
    assert "<script>alert(1)" not in html
    assert "&lt;script&gt;alert(1)" in html


@pytest.mark.parametrize("query", [
    "status=bogus", "page=abc", "page=0", "page=99999999999999999999",
    "sort=bogus", "dir=up",
])
def test_bad_parameters_are_a_400(estate, query):
    assert _status(estate, query)[0] == 400


def test_an_empty_email_has_a_label_and_no_links(tmp_path, monkeypatch, kweb):
    _make_db(tmp_path, monkeypatch,
             [("github.com~acme~api", "", 1, "c@example.com")])
    html = _html(kweb)
    assert "(no email)" in html
    assert "author_email=" not in html


def _csv(kweb, query, id=None):
    request = Request({"type": "http", "method": "GET", "path": "/developers/",
                       "headers": [], "query_string": query.encode(), "app": kweb.app})
    response = kweb.developers(request, id=id)
    return list(csv.reader(io.StringIO(response.body.decode())))


def test_csv_is_every_matching_row(tmp_path, monkeypatch, kweb):
    _big(tmp_path, monkeypatch, 251)
    rows = _csv(kweb, "status=all&q=dev&page=3&download=true")
    assert rows[0] == ["author_email", "commits", "repos", "first_commit",
                       "last_commit", "last_seen", "is_bot", "bot_type"]
    assert len(rows) == 252


def test_csv_follows_the_status_and_defaults_to_everyone(estate):
    assert {r[0] for r in _csv(estate, "download=true")[1:]} == EVERYONE
    assert {r[0] for r in _csv(estate, "status=active&download=true")[1:]} == ACTIVE
    assert 'href="/developers/?status=active&amp;download=true"' in _html(estate)


def test_the_old_queries_are_not_called(estate, monkeypatch):
    for name in ("summary", "authors", "committers"):
        def boom(*a, _name=name, **k):
            raise AssertionError(f"/developers/ called {_name}()")
        monkeypatch.setattr(KospexQuery, name, boom)
    for query in ("", "view=committers", "days=90", "q=paul"):
        _get(estate, query)


def test_a_stats_gap_lists_the_same_people(estate):
    def listed():
        estate.request_cache.clear()
        return {(p["email"], p["commits"])
                for p in _get(estate, "status=all").context["people"]}

    before = listed()
    kq = KospexQuery()
    kq.kospex_db.execute(
        "DELETE FROM developer_stats WHERE _repo_id = 'github.com~acme~web'")
    kq.kospex_db.conn.commit()
    assert kq.developer_stats_coverage()["repos_without_stats"] == 1
    assert listed() == before


def test_days_and_committers_views_ignore_status_and_search(estate):
    windowed = _get(estate, "days=90&status=stale&q=zzz")
    assert {p["email"] for p in windowed.context["people"]} == ACTIVE
    assert windowed.context["data"]["authors"] == len(ACTIVE)
    assert 'name="q"' not in windowed.body.decode()

    committers = _get(estate, "view=committers&status=stale&q=zzz")
    assert "merge@example.com" in [p["email"] for p in committers.context["people"]]
    assert 'name="q"' not in committers.body.decode()
    assert re.search(r"Shown below", committers.body.decode())


def test_the_repo_active_developers_page_lists_the_window(estate):
    repo_id = "github.com~acme~web"
    request = Request({"type": "http", "method": "GET",
                       "path": f"/developers/active/{repo_id}",
                       "headers": [], "query_string": b"", "app": estate.app})
    response = estate.active_developers(request, repo_id=repo_id)
    assert response.status_code == 200
    assert [p["email"] for p in response.context["people"]] == ["mrpaul@example.com"]
    assert response.context["data"]["authors"] == 1
    assert "Active (last 90 days)" in response.body.decode()
