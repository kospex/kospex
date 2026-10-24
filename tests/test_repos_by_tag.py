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


class _FakeQuery:
    """Stands in for KospexQuery so the route can be driven without a database.

    The route's own behaviour is what is under test here: which template it
    picks, what it puts in the context, and that a rejected tag becomes a 404
    rather than a 500.
    """

    def __init__(self, reject=False):
        self.reject = reject
        self.calls = []

    def repos_with_tag(self, tag):
        if self.reject:
            raise ValueError(f"Tag '{tag}' is not a known tag")
        self.calls.append(("repos_with_tag", tag))
        return [{"_repo_id": "github.com~acme~one", "_git_server": "github.com",
                 "_git_owner": "acme", "_git_repo": "one", "count": 2}]

    def repo_files_by_tag(self, tag, repo_id=None):
        if self.reject:
            raise ValueError(f"Tag '{tag}' is not a known tag")
        self.calls.append(("repo_files_by_tag", tag, repo_id))
        return [{"Provider": "CLAUDE.md", "_repo_id": repo_id}]


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
        assert fake.calls == [("repos_with_tag", "AI")]

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


class TestVocabulary:
    """A guard on the two tags this suite hard-codes as real."""

    @pytest.mark.parametrize("tag", ["AI", "Git", "GitHub"])
    def test_the_tags_used_here_are_ones_panopticas_defines(self, tag):
        assert tag in panopticas.get_tags()
