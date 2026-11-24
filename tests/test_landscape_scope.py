"""Landscape and /tech/ scoping: breadcrumbs, scoped links, org filter, top-N.

Three behaviours, each with a way to pass vacuously that these tests avoid:

- **Org scope on the queries** is checked against real SQL over two orgs, so a
  filter that was ignored would show the other org's repository.
- **Links keep the scope** the page was opened with. The download link used to
  render `/landscape/None?download=true` when the scope came from `?repo_id=`.
- **Top-N** is client-side JavaScript, which these tests cannot run; they pin
  the markup and wiring the script depends on (both selectors, ranked rows,
  the status line, the per-page label) rather than pretend to cover behaviour.
"""
import re

import pytest
import sqlite_utils
from fastapi import HTTPException
from starlette.requests import Request

import kospex_schema as KospexSchema
from kospex_query import KospexQuery

import kweb2


@pytest.fixture(autouse=True)
def _preserve_kospex_env():
    """Constructing KospexQuery sets KOSPEX_* env vars as a side effect."""
    import os
    saved = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(saved)


def _row(org, repo, filename, language):
    return {
        "Provider": filename, "hash": "h1",
        "_repo_id": f"github.com~{org}~{repo}", "_git_server": "github.com",
        "_git_owner": org, "_git_repo": repo, "Filename": filename,
        "Language": language, "tech_type": "|x|", "Lines": 1,
        "committer_when": "2026-01-01T00:00:00+00:00", "latest": 1,
    }


_ROWS = [
    _row("acme", "one", "a.py", "Python"),
    _row("acme", "one", "b.py", "Python"),
    _row("acme", "two", "c.go", "Go"),
    _row("other", "three", "d.py", "Python"),
    _row("other", "three", "e.py", "Python"),
    _row("other", "three", "f.py", "Python"),
]


def _seed_db():
    db = sqlite_utils.Database(memory=True)
    db.execute(KospexSchema.SQL_CREATE_FILE_METADATA)
    db[KospexSchema.TBL_FILE_METADATA].insert_all(
        _ROWS, pk=["Provider", "hash", "_repo_id"])
    return db


class TestOrgScopedQueries:

    def test_repos_with_tech_unscoped_spans_both_orgs(self):
        rows = KospexQuery(kospex_db=_seed_db()).repos_with_tech("Python")

        assert {r["_repo_id"] for r in rows} == {
            "github.com~acme~one", "github.com~other~three"}

    def test_repos_with_tech_org_scope_excludes_the_other_org(self):
        rows = KospexQuery(kospex_db=_seed_db()).repos_with_tech(
            "Python", org_key="github.com~acme")

        assert [r["_repo_id"] for r in rows] == ["github.com~acme~one"]
        assert rows[0]["count"] == 2

    def test_repos_with_file_metadata_and_count_agree_on_the_org(self):
        kq = KospexQuery(kospex_db=_seed_db())

        listed = kq.repos_with_file_metadata(org_key="github.com~acme")

        assert {r["_repo_id"] for r in listed} == {
            "github.com~acme~one", "github.com~acme~two"}
        assert kq.count_repos_with_file_metadata(org_key="github.com~acme") == 2
        assert kq.count_repos_with_file_metadata() == 3

    def test_repos_without_tech_is_relative_to_the_org(self):
        kq = KospexQuery(kospex_db=_seed_db())

        # Within acme, `two` has no Python. `three` is in another org and
        # must not be listed, whatever it has.
        rows = kq.repos_without_tech("Python", org_key="github.com~acme")

        assert [r["_repo_id"] for r in rows] == ["github.com~acme~two"]

    def test_unknown_org_key_form_is_rejected(self):
        with pytest.raises(ValueError):
            KospexQuery(kospex_db=_seed_db()).repos_with_tech(
                "Python", org_key="not-an-org-key")


class _FakeQuery:
    LANDSCAPE = [
        {"Language": "Python", "count": 5, "repos": 2},
        {"Language": "Go", "count": 1, "repos": 1},
    ]

    def __init__(self, known=True):
        self.known = known
        self.calls = []

    def scope_exists(self, params):
        return self.known

    def tech_landscape(self, repo_id=None, org_key=None):
        self.calls.append(("tech_landscape", repo_id, org_key))
        return list(self.LANDSCAPE)

    def has_tech(self, tech):
        return True

    def repos_with_tech(self, tech, org_key=None):
        self.calls.append(("repos_with_tech", tech, org_key))
        return [{"_repo_id": "github.com~acme~one", "_git_server": "github.com",
                 "_git_owner": "acme", "_git_repo": "one", "count": 2}]

    def count_repos_with_file_metadata(self, org_key=None):
        self.calls.append(("count", org_key))
        return 2

    def repos_without_tech(self, tech, org_key=None):
        self.calls.append(("repos_without_tech", tech, org_key))
        return []

    def repo_files(self, tech=None, repo_id=None):
        return []


@pytest.fixture
def fake(monkeypatch):
    f = _FakeQuery()
    monkeypatch.setattr(kweb2, "KospexQuery", lambda *a, **k: f)
    return f


def _request(path, query=b""):
    return Request({"type": "http", "method": "GET", "path": path,
                    "headers": [], "query_string": query, "app": kweb2.app})


def _landscape(query=b"", id=None):
    response = kweb2.landscape(_request("/landscape/", query), id=id)
    return response.body.decode()


def _tech(tech, query=b""):
    return kweb2.repo_with_tech(_request(f"/tech/{tech}", query), tech=tech)


def _text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html)).strip()


class TestLandscapeScope:

    def test_unscoped_has_no_breadcrumb_and_plain_links(self, fake):
        html = _landscape()

        assert 'aria-label="Breadcrumb"' not in html
        assert 'href="/tech/Python"' in html
        assert 'href="/landscape/?download=true"' in html

    def test_repo_scope_breadcrumb_links_and_download(self, fake):
        html = _landscape(b"repo_id=github.com~acme~one")

        assert 'aria-label="Breadcrumb"' in html
        assert 'href="/repo/github.com~acme~one"' in html
        assert "Technology Landscape" in _text(html)
        assert "Repository acme/one" in _text(html)
        assert 'href="/tech/Python?repo_id=github.com~acme~one"' in html
        assert ('href="/landscape/?repo_id=github.com~acme~one&amp;download=true"'
                in html)
        assert "None" not in re.findall(r'href="([^"]*download[^"]*)"', html)[0]

    def test_org_scope_breadcrumb_links_and_download(self, fake):
        html = _landscape(b"org_key=github.com~acme")

        assert 'href="/org/github.com~acme"' in html
        assert "Organisation acme" in _text(html)
        assert 'href="/tech/Python?org_key=github.com~acme"' in html
        assert ('href="/landscape/?org_key=github.com~acme&amp;download=true"'
                in html)
        assert ("tech_landscape", None, "github.com~acme") in fake.calls

    def test_path_id_download_link_keeps_the_id(self, fake):
        html = _landscape(id="github.com~acme~one")

        assert 'href="/landscape/github.com~acme~one?download=true"' in html

    def test_download_link_never_renders_none(self, fake):
        for query in (b"", b"repo_id=github.com~acme~one",
                      b"org_key=github.com~acme"):
            assert "/landscape/None" not in _landscape(query)


class TestLandscapeTopN:

    def test_both_selectors_offer_the_same_choices(self, fake):
        html = _landscape()

        for select_id in ("topN", "topN2"):
            block = re.search(
                rf'<select id="{select_id}".*?</select>', html, re.S).group(0)
            assert re.findall(r'value="([^"]+)"', block) == [
                "5", "10", "20", "all"]
            assert 'value="10" selected' in block

    def test_rows_carry_their_rank_in_count_order(self, fake):
        html = _landscape()

        assert re.findall(r'data-rank="(\d+)"', html) == ["0", "1"]
        assert html.index(">Python<") < html.index(">Go<")

    def test_status_line_and_per_page_label_are_wired(self, fake):
        html = _landscape()

        assert 'id="topNStatus"' in html
        assert "Showing the top " in html
        assert "entries per page" in html
        # Both selectors are bound to the one handler.
        assert '$(".top-n").on("change"' in html


class TestTechOrgScope:

    def test_org_key_is_passed_to_the_lookups_and_breadcrumbed(self, fake):
        response = _tech("Python", b"org_key=github.com~acme")
        html = response.body.decode()

        assert ("repos_with_tech", "Python", "github.com~acme") in fake.calls
        assert ("count", "github.com~acme") in fake.calls
        assert 'href="/org/github.com~acme"' in html
        assert "Technology: Python" in _text(html)

    def test_absent_link_keeps_the_org(self, fake):
        html = _tech("Python", b"org_key=github.com~acme").body.decode()

        assert "absent=true&amp;org_key=github.com~acme" in html

    def test_absent_view_uses_the_org(self, fake):
        _tech("Python", b"org_key=github.com~acme&absent=true")

        assert ("repos_without_tech", "Python", "github.com~acme") in fake.calls

    def test_repo_id_wins_over_org_key(self, fake):
        _tech("Python", b"repo_id=github.com~acme~one&org_key=github.com~acme")

        assert not [c for c in fake.calls if c[0] == "repos_with_tech"]

    def test_unknown_org_is_a_404(self, monkeypatch):
        f = _FakeQuery(known=False)
        monkeypatch.setattr(kweb2, "KospexQuery", lambda *a, **k: f)

        with pytest.raises(HTTPException) as caught:
            _tech("Python", b"org_key=github.com~nope")

        assert caught.value.status_code == 404

    def test_unscoped_is_unchanged(self, fake):
        html = _tech("Python").body.decode()

        assert ("repos_with_tech", "Python", None) in fake.calls
        assert 'aria-label="Breadcrumb"' not in html


class TestOrgViewLinks:

    def test_org_page_tech_links_carry_the_org(self):
        """Read from the template: the handler needs a populated DB to render."""
        from pathlib import Path
        src = (Path(__file__).parent.parent / "src/templates/org_view.html"
               ).read_text()

        assert "?org_key={{ org_key | urlencode }}" in src
