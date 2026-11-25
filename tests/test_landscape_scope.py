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


# --- /tag/ org scope and the Developers page ---------------------------------

def _tag_row(org, repo, filename, tags):
    row = _row(org, repo, filename, "Markdown")
    row["tech_type"] = tags
    return row


def _seed_tag_db():
    db = sqlite_utils.Database(memory=True)
    db.execute(KospexSchema.SQL_CREATE_FILE_METADATA)
    db[KospexSchema.TBL_FILE_METADATA].insert_all([
        _tag_row("acme", "one", "CLAUDE.md", "|AI|"),
        _tag_row("acme", "two", "README.md", "|license|"),
        _tag_row("other", "three", "CLAUDE.md", "|AI|"),
    ], pk=["Provider", "hash", "_repo_id"])
    return db


class TestTagOrgScopedQueries:

    def test_repos_with_tag_org_scope_excludes_the_other_org(self):
        kq = KospexQuery(kospex_db=_seed_tag_db())

        assert {r["_repo_id"] for r in kq.repos_with_tag("AI")} == {
            "github.com~acme~one", "github.com~other~three"}
        assert [r["_repo_id"] for r in kq.repos_with_tag(
            "AI", org_key="github.com~acme")] == ["github.com~acme~one"]

    def test_repos_without_tag_is_relative_to_the_org(self):
        rows = KospexQuery(kospex_db=_seed_tag_db()).repos_without_tag(
            "AI", org_key="github.com~acme")

        assert [r["_repo_id"] for r in rows] == ["github.com~acme~two"]


def _commit(org, repo, email):
    return {
        "hash": f"{repo}-{email}", "_repo_id": f"github.com~{org}~{repo}",
        "_git_server": "github.com", "_git_owner": org, "_git_repo": repo,
        "author_email": email, "committer_email": email,
        "author_when": "2026-01-01T00:00:00+00:00",
        "committer_when": "2026-01-01T00:00:00+00:00",
    }


class TestAuthorsScope:

    def _kq(self):
        db = sqlite_utils.Database(memory=True)
        db.execute(KospexSchema.SQL_CREATE_COMMITS)
        db[KospexSchema.TBL_COMMITS].insert_all([
            _commit("acme", "one", "a@example.com"),
            _commit("acme", "two", "b@example.com"),
            _commit("other", "three", "c@example.com"),
        ], pk=["hash", "_repo_id"])
        return KospexQuery(kospex_db=db)

    def test_unscoped_lists_everyone(self):
        assert {r["author_email"] for r in self._kq().authors()} == {
            "a@example.com", "b@example.com", "c@example.com"}

    def test_org_scope(self):
        rows = self._kq().authors(org_key="github.com~acme")

        assert {r["author_email"] for r in rows} == {
            "a@example.com", "b@example.com"}

    def test_repo_scope(self):
        rows = self._kq().authors(repo_id="github.com~acme~one")

        assert [r["author_email"] for r in rows] == ["a@example.com"]


class _DevFake(_FakeQuery):

    def authors(self, days=None, org_key=None, repo_id=None):
        self.calls.append(("authors", days, org_key, repo_id))
        return []

    def summary(self, days=None, repo_id=None, org_key=None):
        self.calls.append(("summary", days, repo_id, org_key))
        return {"repos": 0, "commits": 0, "authors": 0, "committers": 0,
                "servers": 0, "orgs": 0}


def _developers(query=b"", id=None):
    return kweb2.developers(_request("/developers/", query), id=id).body.decode()


@pytest.fixture
def devfake(monkeypatch):
    f = _DevFake()
    monkeypatch.setattr(kweb2, "KospexQuery", lambda *a, **k: f)
    return f


class TestDevelopersScope:

    def test_unscoped_links_are_unchanged(self, devfake):
        html = _developers()

        assert 'href="/developers/?download=true"' in html
        assert 'href="/developers/?days=90"' in html

    def test_repo_scope_reaches_the_queries_and_the_links(self, devfake):
        html = _developers(b"repo_id=github.com~acme~one")

        assert ("authors", None, None, "github.com~acme~one") in devfake.calls
        assert ("summary", None, "github.com~acme~one", None) in devfake.calls
        assert ('href="/developers/?repo_id=github.com~acme~one&amp;download=true"'
                in html)
        assert ('href="/developers/?repo_id=github.com~acme~one&amp;days=90"'
                in html)

    def test_org_scope_download_keeps_the_window(self, devfake):
        html = _developers(b"org_key=github.com~acme&days=90")

        assert ("authors", "90", "github.com~acme", None) in devfake.calls
        assert ('href="/developers/?org_key=github.com~acme&amp;download=true'
                '&amp;days=90"') in html

    def test_repo_id_wins_over_org_key(self, devfake):
        _developers(b"repo_id=github.com~acme~one&org_key=github.com~acme")

        assert ("authors", None, None, "github.com~acme~one") in devfake.calls

    def test_path_id_scopes_like_the_query(self, devfake):
        _developers(id="github.com~acme~one")

        assert ("authors", None, None, "github.com~acme~one") in devfake.calls

    def test_scoped_download_is_a_csv_of_that_scope(self, monkeypatch):
        class Rows(_DevFake):
            def authors(self, days=None, org_key=None, repo_id=None):
                self.calls.append(("authors", days, org_key, repo_id))
                return [{"author_email": "a@example.com", "commits": 1}]

        rows = Rows()
        monkeypatch.setattr(kweb2, "KospexQuery", lambda *a, **k: rows)

        response = kweb2.developers(
            _request("/developers/", b"repo_id=github.com~acme~one&download=true"),
            id=None)

        assert "a@example.com" in response.body.decode()
        assert ("authors", None, None, "github.com~acme~one") in rows.calls


class _TagFake(_FakeQuery):

    def repos_with_tag(self, tag, org_key=None):
        self.calls.append(("repos_with_tag", tag, org_key))
        return [{"_repo_id": "github.com~acme~one", "_git_server": "github.com",
                 "_git_owner": "acme", "_git_repo": "one", "count": 2}]

    def repos_without_tag(self, tag, org_key=None):
        self.calls.append(("repos_without_tag", tag, org_key))
        return []

    def repo_files_by_tag(self, tag, repo_id=None):
        self.calls.append(("repo_files_by_tag", tag, repo_id))
        return []


class TestTagScope:

    @pytest.fixture
    def tagfake(self, monkeypatch):
        f = _TagFake()
        monkeypatch.setattr(kweb2, "KospexQuery", lambda *a, **k: f)
        return f

    def _tag(self, query=b""):
        return kweb2.repo_with_tag(_request("/tag/AI", query), tag="AI")

    def test_org_key_reaches_the_lookups_and_the_breadcrumb(self, tagfake):
        html = self._tag(b"org_key=github.com~acme").body.decode()

        assert ("repos_with_tag", "AI", "github.com~acme") in tagfake.calls
        assert ("count", "github.com~acme") in tagfake.calls
        assert 'href="/org/github.com~acme"' in html
        assert "Tag: AI" in _text(html)
        assert "absent=true&amp;org_key=github.com~acme" in html

    def test_absent_view_uses_the_org(self, tagfake):
        self._tag(b"org_key=github.com~acme&absent=true")

        assert ("repos_without_tag", "AI", "github.com~acme") in tagfake.calls

    def test_repo_id_drills_down_and_wins_over_org_key(self, tagfake):
        html = self._tag(
            b"repo_id=github.com~acme~one&org_key=github.com~acme").body.decode()

        assert ("repo_files_by_tag", "AI", "github.com~acme~one") in tagfake.calls
        assert not [c for c in tagfake.calls if c[0] == "repos_with_tag"]
        assert 'href="/repo/github.com~acme~one"' in html

    def test_unknown_org_is_a_404(self, monkeypatch):
        f = _TagFake(known=False)
        monkeypatch.setattr(kweb2, "KospexQuery", lambda *a, **k: f)

        with pytest.raises(HTTPException) as caught:
            self._tag(b"org_key=github.com~nope")

        assert caught.value.status_code == 404


class TestDevelopersBreadcrumb:

    def test_unscoped_has_no_header_block(self, devfake):
        html = _developers()

        assert 'aria-label="Breadcrumb"' not in html
        assert "uppercase tracking-wider text-gray-500\">Repository" not in html
        assert html.count("<h1") == 1

    def test_repo_scope_breadcrumb_heading_and_label(self, devfake):
        html = _developers(b"repo_id=github.com~acme~one")

        assert 'aria-label="Breadcrumb"' in html
        assert 'href="/org/github.com~acme"' in html
        assert 'href="/repo/github.com~acme~one"' in html
        text = _text(html)
        assert "Developers" in text
        assert "Repository acme/one" in text
        # The crumb names the page, so it is the current (unlinked) item.
        assert 'aria-current="page">Developers<' in html
        # One heading, not a second one added for the scope.
        assert html.count("<h1") == 1

    def test_org_scope_breadcrumb_and_label(self, devfake):
        html = _developers(b"org_key=github.com~acme")

        assert 'href="/orgs/github.com"' in html
        assert 'href="/org/github.com~acme"' in html
        assert "Organisation acme" in _text(html)

    def test_scoped_links_survive_the_context_merge(self, devfake):
        """scope_context() returns its own keys; none may shadow the page's URLs."""
        html = _developers(b"repo_id=github.com~acme~one")

        assert ('href="/developers/?repo_id=github.com~acme~one&amp;download=true"'
                in html)


class TestDevelopersWindow:

    def test_all_time_is_labelled_and_offers_the_active_view(self, devfake):
        html = _developers()

        assert "All time" in _text(html)
        assert 'href="/developers/?days=90"' in html
        assert "Show all time" not in html

    def test_label_shows_the_days_in_the_url(self, devfake):
        for days, word in (("30", "days"), ("90", "days"), ("1", "day")):
            html = _developers(f"days={days}".encode())

            assert f"Active (last {days} {word})" in _text(html)

    def test_active_view_links_back_to_all_time_in_the_same_scope(self, devfake):
        html = _developers(b"repo_id=github.com~acme~one&days=30")

        assert "Show all time" in _text(html)
        assert 'href="/developers/?repo_id=github.com~acme~one"' in html
        assert "Show active developers" not in html

    @pytest.mark.parametrize("bad", ["abc", "0", "-5", "1.5", ""])
    def test_a_bad_window_is_a_400_not_a_500(self, devfake, bad):
        with pytest.raises(HTTPException) as caught:
            _developers(f"days={bad}".encode())

        assert caught.value.status_code == 400

    def test_days_reaches_the_queries_as_a_clean_number(self, devfake):
        _developers(b"days=030")

        assert ("authors", "30", None, None) in devfake.calls


class TestRepoQuickLinks:
    """The repo page's quick links: a Developers link and a collapsible help."""

    @staticmethod
    def _src():
        from pathlib import Path
        return (Path(__file__).parent.parent / "src/templates/repo_view.html"
                ).read_text()

    def test_developers_link_is_scoped_and_precedes_the_graph(self):
        src = self._src()

        dev = src.index('href="/developers/?repo_id={{ repo_id }}"')
        graph = src.index('href="/graph/?repo_id={{ repo_id }}"')
        assert dev < graph
        assert src.index('href="/landscape/?repo_id={{ repo_id }}"') < dev

    def test_every_quick_link_is_described(self):
        src = self._src()
        links = src[src.index("View repo:"):src.index('id="options-help-toggle"')]
        labels = re.findall(r">\s*([^<>]+?)\s*</a\s*>", links)
        help_block = src[src.index('id="options-help"'):]

        assert len(labels) == 7
        for label in labels:
            assert f">{label}</dt>" in re.sub(r"\s+", " ", help_block).replace(
                "> ", ">").replace(" <", "<"), label

    def test_help_starts_hidden_and_has_a_toggle(self):
        src = self._src()

        assert 'id="options-help"' in src
        assert re.search(r'id="options-help"\s+class="hidden', src)
        assert "What do these options do?" in src
        assert '$("#options-help-toggle").on("click"' in src


class TestCollabGraphBreadcrumb:

    def _page(self, monkeypatch, repo_id="github.com~acme~one"):
        monkeypatch.setattr(kweb2, "KospexQuery", lambda *a, **k: _FakeQuery())
        return kweb2.collab_graph(
            _request(f"/collab/graph/{repo_id}"), repo_id=repo_id
        ).body.decode()

    def test_breadcrumb_replaces_the_raw_repo_id_line(self, monkeypatch):
        html = self._page(monkeypatch)

        assert 'aria-label="Breadcrumb"' in html
        assert 'href="/repo/github.com~acme~one"' in html
        # "Collaboration" links back to the table; the graph is the current page.
        assert 'href="/collab/github.com~acme~one"' in html
        assert 'aria-current="page">Network graph<' in html
        assert "Repository:" not in html

    def test_the_graph_still_loads_its_data(self, monkeypatch):
        html = self._page(monkeypatch)

        assert "/api/collab/graph/github.com~acme~one" in html
