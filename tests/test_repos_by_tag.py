"""Repositories by tag: the lookup and the route (#293).

`file_metadata.tech_type` holds the tags panopticas assigns from a filename --
`AI`, `pipeline`, `license` and ~60 others. Nothing reached them from the web
UI, and `/tech/{tech}` cannot: it filters `WHERE Language = ?`, and a tag is
not a Language. The AI signal in particular is in the *filename*, so `scc`
records those files as Markdown or JSON while panopticas tags them `AI` -- the
two columns carry different information, and 9 names (`Go`, `Python`,
`JavaScript`, `C#` ...) exist as both with different meanings.

So this is a separate lookup keyed on tag, not a fallback inside `/tech/`.

Two things are worth stating about the assertions below:

- **The delimiters are the test, not decoration.** `tech_type` is pipe-delimited
  *and* pipe-terminated (`|Git|GitHub|dependencies|`), which is what lets a tag
  match exactly. A naive `LIKE '%Git%'` also matches `GitHub`, so there is a
  case here for exactly that pair.
- **An unknown tag is a 404, not an empty table.** `/tech/AI` returning 200 with
  zero rows is what made this invisible: it reads as "no repositories have AI".
  A tag the vocabulary does not define is a bad URL and says so.
"""
import os
import re

import panopticas
import pytest
import sqlite_utils
from fastapi import HTTPException
from starlette.requests import Request

import kospex_schema as KospexSchema
from kospex_query import KospexQuery

import kweb2


@pytest.fixture(autouse=True)
def _preserve_kospex_env():
    """Constructing KospexQuery sets KOSPEX_* env vars as a side effect;
    restore them so we don't leak state into later tests."""
    keys = ("KOSPEX_CODE", "KOSPEX_DB", "KOSPEX_CONFIG", "KOSPEX_HOME", "KOSPEX_LOGS")
    saved = {k: os.environ.get(k) for k in keys}
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


# Rows as panopticas writes them. `Git` and `GitHub` are both real tags and one
# is a substring of the other, which is the case the delimiters exist for.
_ROWS = [
    {"Provider": ".github/workflows/ci.yml", "hash": "h1",
     "_repo_id": "github.com~acme~one", "_git_server": "github.com",
     "_git_owner": "acme", "_git_repo": "one", "Filename": "ci.yml",
     "Language": "YAML", "tech_type": "|GitHub|workflow|", "Lines": 40,
     "committer_when": "2026-01-01T00:00:00+00:00", "latest": 1},
    {"Provider": ".gitignore", "hash": "h1",
     "_repo_id": "github.com~acme~one", "_git_server": "github.com",
     "_git_owner": "acme", "_git_repo": "one", "Filename": ".gitignore",
     "Language": "Gitignore", "tech_type": "|Git|", "Lines": 5,
     "committer_when": "2026-01-01T00:00:00+00:00", "latest": 1},
    {"Provider": "CLAUDE.md", "hash": "h1",
     "_repo_id": "github.com~acme~one", "_git_server": "github.com",
     "_git_owner": "acme", "_git_repo": "one", "Filename": "CLAUDE.md",
     "Language": "Markdown", "tech_type": "|AI|", "Lines": 100,
     "committer_when": "2026-01-01T00:00:00+00:00", "latest": 1},
    {"Provider": "AGENTS.md", "hash": "h1",
     "_repo_id": "github.com~acme~one", "_git_server": "github.com",
     "_git_owner": "acme", "_git_repo": "one", "Filename": "AGENTS.md",
     "Language": "Markdown", "tech_type": "|AI|", "Lines": 80,
     "committer_when": "2026-01-01T00:00:00+00:00", "latest": 1},
    # one AI file, so repo `two` sorts below `one` on count
    {"Provider": ".mcp.json", "hash": "h1",
     "_repo_id": "github.com~acme~two", "_git_server": "github.com",
     "_git_owner": "acme", "_git_repo": "two", "Filename": ".mcp.json",
     "Language": "JSON", "tech_type": "|AI|", "Lines": 12,
     "committer_when": "2026-01-01T00:00:00+00:00", "latest": 1},
    # a superseded row: the tag is there, but the file is not current
    {"Provider": "old-CLAUDE.md", "hash": "h0",
     "_repo_id": "github.com~acme~three", "_git_server": "github.com",
     "_git_owner": "acme", "_git_repo": "three", "Filename": "old-CLAUDE.md",
     "Language": "Markdown", "tech_type": "|AI|", "Lines": 9,
     "committer_when": "2025-01-01T00:00:00+00:00", "latest": 0},
    # ... and a current file that is not AI, so repo `three` is a repository we
    # have looked at and found no AI in -- the case the absent view is for.
    {"Provider": "LICENSE", "hash": "h1",
     "_repo_id": "github.com~acme~three", "_git_server": "github.com",
     "_git_owner": "acme", "_git_repo": "three", "Filename": "LICENSE",
     "Language": "Text", "tech_type": "|license|", "Lines": 20,
     "committer_when": "2026-01-01T00:00:00+00:00", "latest": 1},
    # a Language nothing current has: the only COBOL file is superseded, so
    # /tech/COBOL is as unknown as a typo. latest = 0, so no count moves.
    {"Provider": "legacy.cbl", "hash": "h0",
     "_repo_id": "github.com~acme~two", "_git_server": "github.com",
     "_git_owner": "acme", "_git_repo": "two", "Filename": "legacy.cbl",
     "Language": "COBOL", "tech_type": "|license|", "Lines": 300,
     "committer_when": "2025-01-01T00:00:00+00:00", "latest": 0},
    # two current files, no AI, so the absent list has an order to check
    {"Provider": "LICENSE", "hash": "h1",
     "_repo_id": "github.com~acme~four", "_git_server": "github.com",
     "_git_owner": "acme", "_git_repo": "four", "Filename": "LICENSE",
     "Language": "Text", "tech_type": "|license|", "Lines": 20,
     "committer_when": "2026-01-01T00:00:00+00:00", "latest": 1},
    {"Provider": "LICENSE.md", "hash": "h1",
     "_repo_id": "github.com~acme~four", "_git_server": "github.com",
     "_git_owner": "acme", "_git_repo": "four", "Filename": "LICENSE.md",
     "Language": "Markdown", "tech_type": "|license|", "Lines": 22,
     "committer_when": "2026-01-01T00:00:00+00:00", "latest": 1},
]


def _seed_db():
    db = sqlite_utils.Database(memory=True)
    db.execute(KospexSchema.SQL_CREATE_FILE_METADATA)
    db[KospexSchema.TBL_FILE_METADATA].insert_all(
        _ROWS, pk=["Provider", "hash", "_repo_id"]
    )
    return db


class TestReposWithTag:
    """The repositories that have a tag, with how many files carry it."""

    def test_returns_one_row_per_repo_with_a_file_count(self):
        rows = KospexQuery(kospex_db=_seed_db()).repos_with_tag("AI")

        counts = {r["_repo_id"]: r["count"] for r in rows}
        assert counts == {"github.com~acme~one": 2, "github.com~acme~two": 1}

    def test_the_busiest_repo_comes_first(self):
        rows = KospexQuery(kospex_db=_seed_db()).repos_with_tag("AI")

        assert [r["count"] for r in rows] == sorted(
            (r["count"] for r in rows), reverse=True)
        assert rows[0]["_repo_id"] == "github.com~acme~one"

    def test_a_superseded_file_does_not_put_its_repo_in_the_list(self):
        rows = KospexQuery(kospex_db=_seed_db()).repos_with_tag("AI")

        assert "github.com~acme~three" not in {r["_repo_id"] for r in rows}

    def test_a_tag_does_not_match_a_tag_it_is_a_substring_of(self):
        """`Git` must not pick up `|GitHub|`. This is what the delimiters do."""
        kq = KospexQuery(kospex_db=_seed_db())

        git = {r["_repo_id"]: r["count"] for r in kq.repos_with_tag("Git")}
        github = {r["_repo_id"]: r["count"] for r in kq.repos_with_tag("GitHub")}

        assert git == {"github.com~acme~one": 1}
        assert github == {"github.com~acme~one": 1}

    def test_the_git_details_are_returned_for_the_template(self):
        row = KospexQuery(kospex_db=_seed_db()).repos_with_tag("AI")[0]

        assert row["_git_server"] == "github.com"
        assert row["_git_owner"] == "acme"
        assert row["_git_repo"] == "one"

    def test_a_tag_the_vocabulary_does_not_define_is_rejected(self):
        with pytest.raises(ValueError):
            KospexQuery(kospex_db=_seed_db()).repos_with_tag("NotATag")

    def test_a_language_is_not_a_tag(self):
        """`Markdown` is a Language, never a tag, so it must not resolve here --
        the distinction the Language filter could not express."""
        with pytest.raises(ValueError):
            KospexQuery(kospex_db=_seed_db()).repos_with_tag("Markdown")


class TestReposWithoutTag:
    """The negative direction: repositories that have *no* file with the tag.

    This is the "everyone should have a Dependabot file" question, so the
    denominator matters. The universe is repositories with current
    file_metadata -- repositories we have actually looked at. A repository with
    no file metadata at all is excluded rather than reported as missing the
    tag, because "we have not looked" is not the same finding as "it is not
    there".
    """

    def test_lists_repositories_with_no_file_carrying_the_tag(self):
        rows = KospexQuery(kospex_db=_seed_db()).repos_without_tag("AI")

        assert {r["_repo_id"] for r in rows} == {
            "github.com~acme~three", "github.com~acme~four"}

    def test_a_superseded_tagged_file_does_not_count_as_having_the_tag(self):
        """Repo `three`'s only AI file is latest = 0. The tag was there once;
        it is not there now, which is the whole point of the question."""
        rows = KospexQuery(kospex_db=_seed_db()).repos_without_tag("AI")

        assert "github.com~acme~three" in {r["_repo_id"] for r in rows}

    def test_counts_every_current_file_not_just_tagged_ones(self):
        """In this view the count is the repository's size, which is the
        context for whether a missing file matters."""
        rows = {r["_repo_id"]: r["count"]
                for r in KospexQuery(kospex_db=_seed_db()).repos_without_tag("AI")}

        # `three` has one current file (the superseded one is excluded),
        # `four` has two.
        assert rows == {"github.com~acme~three": 1, "github.com~acme~four": 2}

    def test_the_biggest_repo_comes_first(self):
        rows = KospexQuery(kospex_db=_seed_db()).repos_without_tag("AI")

        assert [r["count"] for r in rows] == sorted(
            (r["count"] for r in rows), reverse=True)

    def test_present_and_absent_do_not_overlap(self):
        kq = KospexQuery(kospex_db=_seed_db())

        present = {r["_repo_id"] for r in kq.repos_with_tag("AI")}
        absent = {r["_repo_id"] for r in kq.repos_without_tag("AI")}

        assert not present & absent

    def test_present_and_absent_cover_every_repo_we_have_looked_at(self):
        kq = KospexQuery(kospex_db=_seed_db())

        present = {r["_repo_id"] for r in kq.repos_with_tag("AI")}
        absent = {r["_repo_id"] for r in kq.repos_without_tag("AI")}

        assert len(present) + len(absent) == kq.count_repos_with_file_metadata()

    def test_the_git_details_are_returned_for_the_template(self):
        row = KospexQuery(kospex_db=_seed_db()).repos_without_tag("AI")[0]

        for column in ("_git_server", "_git_owner", "_git_repo"):
            assert row[column], f"{column} missing"

    def test_a_tag_the_vocabulary_does_not_define_is_rejected(self):
        with pytest.raises(ValueError):
            KospexQuery(kospex_db=_seed_db()).repos_without_tag("NotATag")

    def test_a_tag_every_repo_has_gives_an_empty_list(self):
        rows = KospexQuery(kospex_db=_seed_db()).repos_without_tag("license")

        assert {r["_repo_id"] for r in rows} == {
            "github.com~acme~one", "github.com~acme~two"}


class TestRepoUniverse:

    def test_counts_repositories_with_current_file_metadata(self):
        count = KospexQuery(kospex_db=_seed_db()).count_repos_with_file_metadata()

        assert count == 4

    def test_a_repo_with_only_superseded_files_is_not_counted(self):
        """It is not a repository we can make a statement about."""
        db = _seed_db()
        db[KospexSchema.TBL_FILE_METADATA].insert({
            "Provider": "gone.md", "hash": "h0",
            "_repo_id": "github.com~acme~five", "_git_server": "github.com",
            "_git_owner": "acme", "_git_repo": "five", "Filename": "gone.md",
            "Language": "Markdown", "tech_type": "|AI|", "Lines": 1,
            "committer_when": "2025-01-01T00:00:00+00:00", "latest": 0,
        }, pk=["Provider", "hash", "_repo_id"])

        assert KospexQuery(kospex_db=db).count_repos_with_file_metadata() == 4


class TestRepoFilesByTag:
    """The reverse direction: a tag's files within one repository."""

    def test_returns_only_that_repos_files_with_that_tag(self):
        rows = KospexQuery(kospex_db=_seed_db()).repo_files_by_tag(
            "AI", repo_id="github.com~acme~one")

        assert {r["Provider"] for r in rows} == {"CLAUDE.md", "AGENTS.md"}

    def test_does_not_leak_another_repos_files(self):
        rows = KospexQuery(kospex_db=_seed_db()).repo_files_by_tag(
            "AI", repo_id="github.com~acme~two")

        assert {r["Provider"] for r in rows} == {".mcp.json"}

    def test_a_superseded_file_is_not_listed(self):
        rows = KospexQuery(kospex_db=_seed_db()).repo_files_by_tag(
            "AI", repo_id="github.com~acme~three")

        assert rows == []

    def test_returns_the_columns_the_file_listing_renders(self):
        row = KospexQuery(kospex_db=_seed_db()).repo_files_by_tag(
            "AI", repo_id="github.com~acme~one")[0]

        for column in ("Provider", "Filename", "Language", "tech_type",
                       "Lines", "committer_when", "_repo_id"):
            assert column in row, f"{column} missing: {sorted(row)}"


class TestHasTech:
    """Whether any current file has a Language at all.

    The drill-down needs this without pulling a repository list: there is no
    vocabulary to validate a Language against, so "nothing has it" is the only
    signal available that a name is a typo.
    """

    def test_true_for_a_language_a_current_file_has(self):
        assert KospexQuery(kospex_db=_seed_db()).has_tech("Markdown") is True

    def test_false_for_a_language_nothing_has(self):
        assert KospexQuery(kospex_db=_seed_db()).has_tech("Fortran") is False

    def test_false_when_only_a_superseded_file_has_it(self):
        """The one COBOL file is latest = 0, so no current file is COBOL."""
        assert KospexQuery(kospex_db=_seed_db()).has_tech("COBOL") is False


class TestReposWithoutTech:
    """The same question for a Language: which repositories have no file of it.

    Lives here because it shares the universe (repositories with current file
    metadata) and the template with the tag side; the wording and the
    denominator have to stay identical between the two.
    """

    def test_lists_the_repositories_with_no_file_of_that_language(self):
        rows = KospexQuery(kospex_db=_seed_db()).repos_without_tech("Markdown")

        assert {r["_repo_id"] for r in rows} == {"github.com~acme~two",
                                                 "github.com~acme~three"}

    def test_a_superseded_file_does_not_count_as_having_the_language(self):
        """Repo three's only Markdown file is latest = 0, so it has none."""
        rows = KospexQuery(kospex_db=_seed_db()).repos_without_tech("Markdown")

        assert "github.com~acme~three" in {r["_repo_id"] for r in rows}

    def test_the_count_is_the_repositorys_total_files(self):
        """Not files of that Language, which is zero on every row -- the total,
        as the context for whether the gap matters."""
        rows = KospexQuery(kospex_db=_seed_db()).repos_without_tech("Text")

        assert [(r["_repo_id"], r["count"]) for r in rows] == [
            ("github.com~acme~one", 4), ("github.com~acme~two", 1)]

    def test_a_technology_with_no_files_anywhere_is_rejected(self):
        """Its complement is every repository, which for a typo is a
        confidently wrong answer. The route turns this into a 404."""
        with pytest.raises(ValueError):
            KospexQuery(kospex_db=_seed_db()).repos_without_tech("Fortran")

    def test_present_and_absent_cover_every_repo_we_have_looked_at(self):
        kospex = KospexQuery(kospex_db=_seed_db())

        present = {r["_repo_id"] for r in kospex.repos_with_tech("Markdown")}
        absent = {r["_repo_id"] for r in kospex.repos_without_tech("Markdown")}
        universe = {r["_repo_id"] for r in kospex.repos_with_file_metadata()}

        assert present | absent == universe
        assert not present & absent


class _FakeQuery:
    """Stands in for KospexQuery so the route can be driven without a database.

    The route's own behaviour is what is under test here: which template it
    picks, what it puts in the context, and that a rejected tag becomes a 404
    rather than a 500.
    """

    def __init__(self, reject=False):
        self.reject = reject
        self.calls = []

    _REPOS = [
        {"_repo_id": "github.com~acme~one", "_git_server": "github.com",
         "_git_owner": "acme", "_git_repo": "one", "count": 2},
        {"_repo_id": "github.com~acme~two", "_git_server": "github.com",
         "_git_owner": "acme", "_git_repo": "two", "count": 1},
    ]

    def repos_with_tag(self, tag):
        if self.reject:
            raise ValueError(f"Tag '{tag}' is not a known tag")
        self.calls.append(("repos_with_tag", tag))
        return list(self._REPOS)

    _ABSENT = [
        {"_repo_id": "github.com~acme~three", "_git_server": "github.com",
         "_git_owner": "acme", "_git_repo": "three", "count": 9},
    ]

    def repos_without_tag(self, tag):
        if self.reject:
            raise ValueError(f"Tag '{tag}' is not a known tag")
        self.calls.append(("repos_without_tag", tag))
        return list(self._ABSENT)

    def count_repos_with_file_metadata(self):
        self.calls.append(("count_repos_with_file_metadata",))
        return 3

    def repos_with_tech(self, tech):
        self.calls.append(("repos_with_tech", tech))
        # Unlike repos_with_tag(), this never raises -- there is no vocabulary
        # to reject a Language against, so an unknown one is simply empty, and
        # turning that into a 404 is the route's job.
        return [] if self.reject else list(self._REPOS)

    def has_tech(self, tech):
        self.calls.append(("has_tech", tech))
        return not self.reject

    def repos_without_tech(self, tech):
        if self.reject:
            raise ValueError(f"No files with technology '{tech}'")
        self.calls.append(("repos_without_tech", tech))
        return list(self._ABSENT)

    def repo_files(self, tech=None, repo_id=None):
        self.calls.append(("repo_files", tech, repo_id))
        return [{"Provider": "main.py", "_repo_id": repo_id}]

    def repo_files_by_tag(self, tag, repo_id=None):
        if self.reject:
            raise ValueError(f"Tag '{tag}' is not a known tag")
        self.calls.append(("repo_files_by_tag", tag, repo_id))
        return [{"Provider": "CLAUDE.md", "_repo_id": repo_id}]


def _text(body):
    """The page's text with runs of whitespace collapsed, as a browser shows it."""
    return re.sub(r"\s+", " ", body)


def _visible(body):
    """What a reader sees: markup removed, whitespace collapsed.

    Needed where a sentence has a link in the middle of it -- the words are
    contiguous on screen but not in the HTML.
    """
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", body)).strip()


def _request(query_string=b""):
    return Request({
        "type": "http", "method": "GET", "path": "/tag/AI",
        "headers": [], "query_string": query_string,
        "app": kweb2.app,
    })


@pytest.fixture
def fake_query(monkeypatch):
    """Install a fake KospexQuery into the route's module."""
    def _install(reject=False):
        fake = _FakeQuery(reject=reject)
        monkeypatch.setattr(kweb2, "KospexQuery", lambda *a, **k: fake)
        return fake
    return _install


class TestRoute:

    def test_lists_the_repositories_for_a_tag(self, fake_query):
        fake = fake_query()

        response = kweb2.repo_with_tag(_request(), tag="AI")

        assert response.status_code == 200
        # Plus the universe count, for the "how many do not have it" wording.
        assert ("repos_with_tag", "AI") in fake.calls
        assert ("repos_without_tag", "AI") not in fake.calls

    def test_renders_the_repo_listing_template(self, fake_query):
        fake_query()

        response = kweb2.repo_with_tag(_request(), tag="AI")

        assert response.template.name == "repos-tech.html"

    def test_the_page_says_it_is_showing_a_tag(self, fake_query):
        """The template is shared with /tech/, which labels its heading
        'Technology'. A tag page saying 'Technology: AI' would restate the
        confusion this route exists to remove."""
        fake_query()

        context = kweb2.repo_with_tag(_request(), tag="AI").context

        assert context["tech_kind"] == "Tag"
        assert context["tech"] == "AI"

    def test_the_file_links_point_back_at_the_tag_route(self, fake_query):
        """Not /tech/{tag}?repo_id=... -- that filters Language and comes back
        empty, which is the defect in #293."""
        fake_query()

        context = kweb2.repo_with_tag(_request(), tag="AI").context

        assert context["tech_url"] == "/tag/AI"

    def test_a_repo_id_narrows_to_that_repos_files(self, fake_query):
        fake = fake_query()

        response = kweb2.repo_with_tag(
            _request(b"repo_id=github.com~acme~one"), tag="AI")

        assert response.template.name == "repo_files.html"
        assert fake.calls == [
            ("repo_files_by_tag", "AI", "github.com~acme~one")]

    def test_an_unknown_tag_is_a_404_not_an_empty_table(self, fake_query):
        fake_query(reject=True)

        with pytest.raises(HTTPException) as raised:
            kweb2.repo_with_tag(_request(), tag="NotATag")

        assert raised.value.status_code == 404

    def test_an_unknown_tag_with_a_repo_id_is_also_a_404(self, fake_query):
        fake_query(reject=True)

        with pytest.raises(HTTPException) as raised:
            kweb2.repo_with_tag(
                _request(b"repo_id=github.com~acme~one"), tag="NotATag")

        assert raised.value.status_code == 404


class TestRenderedListing:
    """What the page actually shows.

    The handler's TemplateResponse renders on construction, so `response.body`
    is the real HTML -- worth asserting on, because the count these assertions
    look for is returned by the query and was never displayed.
    """

    def _body(self, fake_query, path="/tag/AI", tag="AI"):
        fake_query()
        request = Request({
            "type": "http", "method": "GET", "path": path,
            "headers": [], "query_string": b"", "app": kweb2.app,
        })
        return kweb2.repo_with_tag(request, tag=tag).body.decode()

    def test_says_how_many_repositories_have_the_tag(self, fake_query):
        # Collapsed, because the template wraps the sentence over lines and the
        # browser collapses that whitespace -- asserting on the indentation
        # would break on reformatting without the page changing.
        assert "2 repositories with this tag" in _text(self._body(fake_query))

    def test_counts_one_repository_in_the_singular(self, fake_query, monkeypatch):
        fake = _FakeQuery()
        monkeypatch.setattr(kweb2, "KospexQuery", lambda *a, **k: fake)
        monkeypatch.setattr(
            fake, "repos_with_tag", lambda tag: [_FakeQuery._REPOS[0]])
        request = Request({
            "type": "http", "method": "GET", "path": "/tag/llms.txt",
            "headers": [], "query_string": b"", "app": kweb2.app,
        })

        body = kweb2.repo_with_tag(request, tag="llms.txt").body.decode()

        assert "1 repository with this tag" in _text(body)

    def test_shows_each_repositorys_file_count(self, fake_query):
        """The query returns `count` per repository. Without it in the table
        there is no column worth sorting on -- every row's Files cell held the
        same words."""
        body = self._body(fake_query)

        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.S)
        counts = [r for r in rows if re.search(r">\s*2\s*<", r)]
        assert counts, "no row renders the file count 2"

    def test_the_datatable_sorts_on_a_column_that_exists(self, fake_query):
        """`order: [[4, "asc"]]` on a two-column table throws on init, which
        silently loses paging, search and sorting altogether."""
        body = self._body(fake_query)

        table = re.search(r'<table[^>]*id="myTable".*?</thead>', body, re.S)
        assert table, "no #myTable to configure"
        columns = len(re.findall(r"<th", table.group(0)))
        order = re.search(r"order:\s*\[\[\s*(\d+)", body)

        assert order, "no DataTables order configured"
        assert int(order.group(1)) < columns, (
            f"orders on column {order.group(1)} of a {columns}-column table")

    def test_the_technology_page_counts_its_repositories_too(self, fake_query):
        """Same template, so /tech/ gets the same line with its own wording."""
        fake_query()
        request = Request({
            "type": "http", "method": "GET", "path": "/tech/Python",
            "headers": [], "query_string": b"", "app": kweb2.app,
        })

        body = kweb2.repo_with_tech(request, tech="Python").body.decode()

        assert "2 repositories with this technology" in _text(body)

    @pytest.mark.parametrize("path,handler,kind", [
        ("/tag/AI", "repo_with_tag", "tag"),
        ("/tech/Python", "repo_with_tech", "technology"),
    ])
    def test_the_absent_clause_always_carries_a_count(
            self, fake_query, path, handler, kind):
        """The second half of the sentence must never render empty.

        Jinja's Undefined satisfies `is not none`, so a route that does not
        set absent_count renders "96 repositories with this technology,
        repositories do not have files with this technology" -- which /tech/
        did until the guard tested `is defined` first.
        """
        fake_query()
        request = Request({
            "type": "http", "method": "GET", "path": path,
            "headers": [], "query_string": b"", "app": kweb2.app,
        })
        name = path.rsplit("/", 1)[1]

        body = _visible(getattr(kweb2, handler)(request, **{
            "tag" if kind == "tag" else "tech": name}).body.decode())

        assert not re.search(r",\s*repositor(y|ies) do(es)? not have", body), (
            f"the count is missing from the absent clause: {body[:400]}")


class TestAbsentView:
    """`?absent=true`: which repositories do NOT have the tag.

    The point is adoption of a good practice -- "everyone should have a
    Dependabot file" -- so the page has to name the repositories that don't,
    and the positive page has to say how many those are and link to them.
    """

    def _body(self, fake_query, query_string=b"absent=true", tag="AI"):
        fake_query()
        request = Request({
            "type": "http", "method": "GET", "path": f"/tag/{tag}",
            "headers": [], "query_string": query_string, "app": kweb2.app,
        })
        return kweb2.repo_with_tag(request, tag=tag)

    def test_lists_the_repositories_without_the_tag(self, fake_query):
        fake = fake_query()
        request = Request({
            "type": "http", "method": "GET", "path": "/tag/AI",
            "headers": [], "query_string": b"absent=true", "app": kweb2.app,
        })

        response = kweb2.repo_with_tag(request, tag="AI")

        assert response.status_code == 200
        assert ("repos_without_tag", "AI") in fake.calls

    def test_renders_the_repo_listing_template(self, fake_query):
        assert self._body(fake_query).template.name == "repos-tech.html"

    def test_the_heading_says_these_lack_the_tag(self, fake_query):
        body = _text(self._body(fake_query).body.decode())

        assert "1 repository has no files with this tag" in body

    def test_the_title_itself_says_the_tag_is_not_present(self, fake_query):
        """The sentence below carries it, but the heading is what a reader
        scans -- and "Tag: AI" over a list of repositories reads as the
        repositories that have it."""
        body = _visible(self._body(fake_query).body.decode())

        assert "Tag: AI is not present" in body

    def test_reports_the_denominator(self, fake_query):
        """Without the universe the number is unreadable: 1 of how many?

        The unit is named, because "of 3" alone reads as 3 of something --
        files, tags -- on a page whose other numbers are file counts.
        """
        body = _text(self._body(fake_query).body.decode())

        assert "of 3 repositories that have file metadata" in body

    def test_says_what_the_files_column_counts(self, fake_query):
        """Every row has zero files with the tag, so the count beside it has
        to say what it does count, or it reads as a contradiction."""
        body = _visible(self._body(fake_query).body.decode())

        assert ("The '# Files' column shows the total number of all file "
                "types and tags in this repository") in body

    def test_offers_no_see_files_link(self, fake_query):
        """There are no files with the tag to see -- the link would go to an
        empty page."""
        body = self._body(fake_query).body.decode()

        assert "See Files" not in body

    def test_still_links_each_repository(self, fake_query):
        body = self._body(fake_query).body.decode()

        assert 'href="/repo/github.com~acme~three"' in body

    def test_an_unknown_tag_is_a_404(self, fake_query):
        fake_query(reject=True)
        request = Request({
            "type": "http", "method": "GET", "path": "/tag/NotATag",
            "headers": [], "query_string": b"absent=true", "app": kweb2.app,
        })

        with pytest.raises(HTTPException) as raised:
            kweb2.repo_with_tag(request, tag="NotATag")

        assert raised.value.status_code == 404

    def test_a_repo_id_takes_precedence(self, fake_query):
        """Files in one repo that lack the tag is not a question with an
        answer; the drill-down wins."""
        fake = fake_query()
        request = Request({
            "type": "http", "method": "GET", "path": "/tag/AI",
            "headers": [],
            "query_string": b"absent=true&repo_id=github.com~acme~one",
            "app": kweb2.app,
        })

        response = kweb2.repo_with_tag(request, tag="AI")

        assert response.template.name == "repo_files.html"
        assert ("repos_without_tag", "AI") not in fake.calls

    @pytest.mark.parametrize("query_string", [b"absent=false", b"absent=0",
                                              b"absent=no", b""])
    def test_not_asking_for_absent_gives_the_positive_listing(
            self, fake_query, query_string):
        fake = fake_query()
        request = Request({
            "type": "http", "method": "GET", "path": "/tag/AI",
            "headers": [], "query_string": query_string, "app": kweb2.app,
        })

        kweb2.repo_with_tag(request, tag="AI")

        assert ("repos_with_tag", "AI") in fake.calls
        assert ("repos_without_tag", "AI") not in fake.calls

    @pytest.mark.parametrize("query_string", [b"absent=true", b"absent=1",
                                              b"absent=yes", b"absent="])
    def test_the_usual_spellings_of_yes_are_accepted(
            self, fake_query, query_string):
        """`?absent` with no value included -- a bare flag is what a person
        types."""
        fake = fake_query()
        request = Request({
            "type": "http", "method": "GET", "path": "/tag/AI",
            "headers": [], "query_string": query_string, "app": kweb2.app,
        })

        kweb2.repo_with_tag(request, tag="AI")

        assert ("repos_without_tag", "AI") in fake.calls


class TestPositiveViewMentionsAbsent:

    def test_says_how_many_repositories_lack_the_tag(self, fake_query):
        """The wording asked for: both sides of the estate in one line. Read as
        visible text, because the second half is a link."""
        fake_query()
        body = kweb2.repo_with_tag(_request(), tag="AI").body.decode()

        assert ("2 repositories with this tag, "
                "1 repository does not have files with this tag") in _visible(body)

    def test_the_title_does_not_say_not_present(self, fake_query):
        """These repositories do have the tag."""
        body = _visible(kweb2.repo_with_tag(_request(), tag="AI").body.decode())

        assert "Tag: AI" in body
        assert "is not present" not in body

    def test_does_not_explain_the_files_column(self, fake_query):
        """Here # Files *is* the tagged-file count, so the absent page's
        explanation of it would be wrong."""
        body = _visible(kweb2.repo_with_tag(_request(), tag="AI").body.decode())

        assert "The '# Files' column" not in body

    def test_links_to_the_absent_listing(self, fake_query):
        fake_query()
        body = kweb2.repo_with_tag(_request(), tag="AI").body.decode()

        assert 'href="/tag/AI?absent=true"' in body

    def test_plural_when_more_than_one_repository_lacks_it(
            self, fake_query, monkeypatch):
        fake = _FakeQuery()
        monkeypatch.setattr(kweb2, "KospexQuery", lambda *a, **k: fake)
        monkeypatch.setattr(fake, "count_repos_with_file_metadata", lambda: 5)

        body = kweb2.repo_with_tag(_request(), tag="AI").body.decode()

        assert "3 repositories do not have files with this tag" in _visible(body)


class TestTechnologyAbsentView:
    """`/tech/{tech}?absent=true`: the same question for a Language.

    The tag side answers "who has no Dependabot file"; this answers "who has
    no Dockerfile, no tests in this language". Same template, same universe,
    same wording -- so the differences are what is worth asserting.
    """

    def _tech_request(self, query_string=b"absent=true"):
        return Request({
            "type": "http", "method": "GET", "path": "/tech/Python",
            "headers": [], "query_string": query_string, "app": kweb2.app,
        })

    def test_lists_the_repositories_without_the_technology(self, fake_query):
        fake = fake_query()

        response = kweb2.repo_with_tech(self._tech_request(), tech="Python")

        assert response.status_code == 200
        assert ("repos_without_tech", "Python") in fake.calls

    def test_renders_the_repo_listing_template(self, fake_query):
        fake_query()

        response = kweb2.repo_with_tech(self._tech_request(), tech="Python")

        assert response.template.name == "repos-tech.html"

    def test_the_heading_says_these_lack_the_technology(self, fake_query):
        fake_query()
        body = kweb2.repo_with_tech(self._tech_request(), tech="Python").body

        assert "1 repository has no files with this technology" in _text(
            body.decode())

    def test_the_title_itself_says_the_technology_is_not_present(
            self, fake_query):
        fake_query()
        body = kweb2.repo_with_tech(self._tech_request(), tech="Python").body

        assert "Technology: Python is not present" in _visible(body.decode())

    def test_reports_the_same_denominator_as_the_tag_page(self, fake_query):
        fake_query()
        body = kweb2.repo_with_tech(self._tech_request(), tech="Python").body

        assert "of 3 repositories that have file metadata" in _text(
            body.decode())

    def test_offers_no_see_files_link(self, fake_query):
        fake_query()
        body = kweb2.repo_with_tech(self._tech_request(), tech="Python").body

        assert "See Files" not in body.decode()

    def test_a_technology_with_no_files_anywhere_is_a_404(self, fake_query):
        """Its complement is every repository, so a typo would render a
        confident list of repositories "missing" something that does not
        exist."""
        fake_query(reject=True)

        with pytest.raises(HTTPException) as raised:
            kweb2.repo_with_tech(self._tech_request(), tech="Fortran")

        assert raised.value.status_code == 404


    def test_a_repo_id_takes_precedence(self, fake_query):
        fake = fake_query()
        request = self._tech_request(
            b"absent=true&repo_id=github.com~acme~one")

        response = kweb2.repo_with_tech(request, tech="Python")

        assert response.template.name == "repo_files.html"
        assert ("repos_without_tech", "Python") not in fake.calls

    @pytest.mark.parametrize("query_string", [b"absent=true", b"absent=1",
                                              b"absent=yes", b"absent="])
    def test_the_usual_spellings_of_yes_are_accepted(
            self, fake_query, query_string):
        fake = fake_query()

        kweb2.repo_with_tech(self._tech_request(query_string), tech="Python")

        assert ("repos_without_tech", "Python") in fake.calls

    @pytest.mark.parametrize("query_string", [b"absent=false", b"absent=0",
                                              b"absent=no", b""])
    def test_not_asking_for_absent_gives_the_positive_listing(
            self, fake_query, query_string):
        fake = fake_query()

        kweb2.repo_with_tech(self._tech_request(query_string), tech="Python")

        assert ("repos_with_tech", "Python") in fake.calls
        assert ("repos_without_tech", "Python") not in fake.calls


class TestUnknownTechnologyIsA404:
    """A technology no current file has is a bad URL on every view.

    The positive page used to return 200 with an empty table, which reads as
    "no repositories have Fortran" when the truth is that this database has
    never seen it -- the same confusion #293 exists to remove for tags.
    """

    _MESSAGE = "Fortran is an unknown Technology or Language."

    def _request(self, query_string=b""):
        return Request({
            "type": "http", "method": "GET", "path": "/tech/Fortran",
            "headers": [], "query_string": query_string, "app": kweb2.app,
        })

    @pytest.mark.parametrize("query_string", [
        b"",
        b"absent=true",
        b"repo_id=github.com~acme~one",
    ])
    def test_every_view_is_a_404(self, fake_query, query_string):
        fake_query(reject=True)

        with pytest.raises(HTTPException) as raised:
            kweb2.repo_with_tech(self._request(query_string), tech="Fortran")

        assert raised.value.status_code == 404

    @pytest.mark.parametrize("query_string", [
        b"",
        b"absent=true",
        b"repo_id=github.com~acme~one",
    ])
    def test_the_message_names_both_words_a_reader_might_have_used(
            self, fake_query, query_string):
        """`/tech/` serves both scc Languages and the Landscape's
        "technology", so the message has to cover whichever the reader thought
        they were asking for -- and names what they asked for, since the URL
        may have come from a paste they cannot see the end of."""
        fake_query(reject=True)

        with pytest.raises(HTTPException) as raised:
            kweb2.repo_with_tech(self._request(query_string), tech="Fortran")

        assert raised.value.detail == self._MESSAGE

    def test_the_drill_down_does_not_list_files_for_an_unknown_technology(
            self, fake_query):
        fake = fake_query(reject=True)

        with pytest.raises(HTTPException):
            kweb2.repo_with_tech(
                self._request(b"repo_id=github.com~acme~one"), tech="Fortran")

        assert not any(call[0] == "repo_files" for call in fake.calls)

    def test_a_known_technology_is_unaffected(self, fake_query):
        fake_query()

        response = kweb2.repo_with_tech(
            Request({"type": "http", "method": "GET", "path": "/tech/Python",
                     "headers": [], "query_string": b"", "app": kweb2.app}),
            tech="Python")

        assert response.status_code == 200


class TestUnknownTagMessage:

    def test_names_the_tag_that_was_asked_for(self, fake_query):
        fake_query(reject=True)
        request = Request({
            "type": "http", "method": "GET", "path": "/tag/NotATag",
            "headers": [], "query_string": b"", "app": kweb2.app,
        })

        with pytest.raises(HTTPException) as raised:
            kweb2.repo_with_tag(request, tag="NotATag")

        assert raised.value.detail == "NotATag is an unknown Tag."


class TestThe404PageShowsWhy:
    """The 404 template has an `error` callout that nothing ever filled.

    Every route's `detail` was discarded by the handler, so `/tech/Fortran`,
    `/tag/NotATag` and a plain mistyped path all rendered the identical
    "Oops! Page not found." -- and a reader could not tell a bad tag from a
    bad URL.
    """

    def _page(self, detail):
        exc = HTTPException(status_code=404, detail=detail)
        request = Request({
            "type": "http", "method": "GET", "path": "/tech/Fortran",
            "headers": [], "query_string": b"", "app": kweb2.app,
        })
        import asyncio
        response = asyncio.run(kweb2.custom_404_handler(request, exc))
        body = response.body.decode()
        # From <body> on: the <title> is "404 Not Found - Kospex Web", which
        # would satisfy an assertion about the callout's text.
        return response.status_code, _visible(body[body.index("<body"):])

    def test_shows_the_routes_own_message(self):
        status, body = self._page("This is an unknown Technology or Language.")

        assert status == 404
        assert "This is an unknown Technology or Language." in body

    def test_still_shows_the_generic_explanation(self):
        """The callout adds to the page, it does not replace it."""
        _, body = self._page("This is an unknown Tag.")

        assert "Oops! Page not found." in body
        assert "This is an unknown Tag." in body

    @pytest.mark.parametrize("detail", [
        "Not Found",      # starlette's default for an unmatched route
        "Page not found",  # the catch-all route's own detail
        "page not found",
        "",
        None,
    ])
    def test_says_nothing_extra_when_the_detail_restates_the_heading(
            self, detail):
        """The callout is for information the heading does not already give.

        The page already says "Oops! Page not found." in 3xl bold; repeating
        it in an orange warning box below reads as a second, different
        problem.
        """
        _, body = self._page(detail)

        assert "Oops! Page not found." in body
        # Once, in the heading -- a second occurrence is the callout saying it
        # again.
        assert body.count("not found") == 1, (
            f"the callout restates the heading: {body}")
        assert "Not Found" not in body

    @pytest.mark.parametrize("detail", [
        "Repository not in the kospex database: github.com~acme~one",
        "Organisation not found",
        "Summary pages are per repo, org or server.",
    ])
    def test_keeps_the_details_other_routes_already_wrote(self, detail):
        """Those routes have always set a useful detail and the handler has
        always thrown it away; the filter must not swallow them too."""
        _, body = self._page(detail)

        assert detail in body

    def test_centres_the_message_in_the_callout(self):
        """The rest of the page is centred; a left-aligned icon and sentence
        in the middle of it read as a misplaced block."""
        exc = HTTPException(status_code=404, detail="Fortran is unknown.")
        request = Request({
            "type": "http", "method": "GET", "path": "/tech/Fortran",
            "headers": [], "query_string": b"", "app": kweb2.app,
        })
        import asyncio
        raw = asyncio.run(
            kweb2.custom_404_handler(request, exc)).body.decode()

        callout = re.search(r'<div class="bg-orange-100[^"]*".*?</div>\s*</div>',
                            raw, re.S)
        assert callout, "no error callout rendered"
        assert "justify-center" in callout.group(0)
        assert "text-center" in callout.group(0)


class TestTheMessageDoesNotTrustTheUrl:
    """The name in the message comes from the URL path.

    Reflecting a path segment into the page is the classic reflected-markup
    injection shape (CWE-79), so the escaping is worth a test rather than an
    assumption about the template engine's defaults.
    """

    _PAYLOAD = '<script>alert(1)</script>'

    def _page_for(self, tech):
        fake = _FakeQuery(reject=True)
        request = Request({
            "type": "http", "method": "GET", "path": f"/tech/{tech}",
            "headers": [], "query_string": b"", "app": kweb2.app,
        })
        import asyncio
        import pytest as _pytest
        with _pytest.MonkeyPatch.context() as mp:
            mp.setattr(kweb2, "KospexQuery", lambda *a, **k: fake)
            with pytest.raises(HTTPException) as raised:
                kweb2.repo_with_tech(request, tech=tech)
        return asyncio.run(
            kweb2.custom_404_handler(request, raised.value)).body.decode()

    def test_markup_in_the_name_is_escaped(self):
        body = self._page_for(self._PAYLOAD)

        assert self._PAYLOAD not in body
        assert "&lt;script&gt;" in body

    def test_the_name_is_still_readable_once_escaped(self):
        body = self._page_for("C++")

        assert "C++ is an unknown Technology or Language." in body

class TestPositiveTechnologyViewMentionsAbsent:

    def _body(self, fake_query):
        fake_query()
        request = Request({
            "type": "http", "method": "GET", "path": "/tech/Python",
            "headers": [], "query_string": b"", "app": kweb2.app,
        })
        return kweb2.repo_with_tech(request, tech="Python").body.decode()

    def test_says_how_many_repositories_lack_the_technology(self, fake_query):
        body = _visible(self._body(fake_query))

        assert ("2 repositories with this technology, "
                "1 repository does not have files with this technology") in body

    def test_links_to_the_absent_listing(self, fake_query):
        assert 'href="/tech/Python?absent=true"' in self._body(fake_query)

    def test_the_title_does_not_say_not_present(self, fake_query):
        body = _visible(self._body(fake_query))

        assert "Technology: Python" in body
        assert "is not present" not in body


class TestVocabulary:
    """A guard on the two tags this suite hard-codes as real."""

    @pytest.mark.parametrize("tag", ["AI", "Git", "GitHub"])
    def test_the_tags_used_here_are_ones_panopticas_defines(self, tag):
        assert tag in panopticas.get_tags()
