"""`/tags/` lists the tag vocabulary, and gives `/tag/{tag}` an entry point.

The vocabulary is `panopticas.get_tags()` -- the tags panopticas *can* assign,
derived from its detection rules. That is deliberately not the same as the tags
present in a given database: a local database holds ~63 of the ~123, so some
entries here lead to a page with no repositories. That is honest, and it is the
same set `where_tag()` validates against, so every tag listed resolves rather
than 404ing.

No database, no counts. Counting repositories per tag is the part #293 keeps out
of scope -- `tech_type` holds several tags per row, and the obvious
`GROUP BY tech_type` double-counts repositories.

Assertions here derive the expected list from panopticas rather than hard-coding
it, so this stays a test of the page and not of someone else's data.
"""
import os
import re

import panopticas
import pytest
from starlette.requests import Request

import kweb2


@pytest.fixture(autouse=True)
def _preserve_kospex_env():
    keys = ("KOSPEX_CODE", "KOSPEX_DB", "KOSPEX_CONFIG", "KOSPEX_HOME", "KOSPEX_LOGS")
    saved = {k: os.environ.get(k) for k in keys}
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


@pytest.fixture
def vocabulary(monkeypatch):
    """Pin the vocabulary, for the cases that need a known shape or a tag
    panopticas does not currently define."""
    def _set(*tags):
        monkeypatch.setattr(panopticas, "get_tags", lambda: list(tags))
    return _set


def _page(path="/tags/"):
    request = Request({
        "type": "http", "method": "GET", "path": path,
        "headers": [], "query_string": b"", "app": kweb2.app,
    })
    return kweb2.tags_index(request)


def _text(body):
    return re.sub(r"\s+", " ", body)


class TestPage:

    def test_renders_the_tag_index(self):
        response = _page()

        assert response.status_code == 200
        assert response.template.name == "tags.html"

    def test_lists_every_tag_panopticas_defines(self):
        assert set(_page().context["tags"]) == set(panopticas.get_tags())

    def test_sorted_alphabetically_ignoring_case(self):
        """Case-sensitive sorting would file every lowercase tag after `yarn`,
        splitting the alphabet in two."""
        tags = _page().context["tags"]

        assert tags == sorted(tags, key=str.lower)

    def test_says_how_many_tags_there_are(self):
        body = _text(_page().body.decode())

        assert f"{len(panopticas.get_tags())} tags" in body


class TestColumns:

    def test_lays_the_tags_out_in_three_columns(self, vocabulary):
        vocabulary("alpha", "beta", "gamma", "delta", "epsilon", "zeta")

        body = _page().body.decode()

        assert len(re.findall(r"data-tag-column", body)) == 3

    def test_the_alphabet_runs_down_each_column_not_across(self, vocabulary):
        """Reading order matters for scanning: the first column should hold the
        first third of the alphabet, not every third tag."""
        # Deliberately unsorted, so this covers the ordering as well as the
        # column fill: sorted, these are aaa .. fff.
        vocabulary("fff", "aaa", "eee", "bbb", "ddd", "ccc")

        body = _page().body.decode()
        columns = re.findall(r"data-tag-column.*?</ul>", body, re.S)

        assert ["aaa", "bbb"] == re.findall(r">([a-z]+)</a>", columns[0])
        assert ["ccc", "ddd"] == re.findall(r">([a-z]+)</a>", columns[1])
        assert ["eee", "fff"] == re.findall(r">([a-z]+)</a>", columns[2])

    def test_every_tag_appears_once(self, vocabulary):
        vocabulary("alpha", "beta", "gamma", "delta", "epsilon")

        body = _page().body.decode()

        for tag in ("alpha", "beta", "gamma", "delta", "epsilon"):
            assert body.count(f">{tag}</a>") == 1, f"{tag} rendered twice"


class TestLinks:

    def test_each_tag_links_to_its_own_page(self, vocabulary):
        vocabulary("AI")

        body = _page().body.decode()

        assert 'href="/tag/AI"' in body

    def test_a_hash_in_a_tag_is_percent_encoded(self, vocabulary):
        """`#` unencoded is a fragment delimiter -- the tag would never reach
        the server, and /tag/C would 404."""
        vocabulary("C#")

        body = _page().body.decode()

        assert 'href="/tag/C%23"' in body

    def test_a_space_in_a_tag_is_encoded(self, vocabulary):
        vocabulary("Azure DevOps")

        body = _page().body.decode()

        assert 'href="/tag/Azure%20DevOps"' in body

    def test_the_links_resolve_rather_than_404(self):
        """The page is built from the same vocabulary where_tag() validates
        against, so a listed tag is never rejected as unknown."""
        import sqlite_utils
        from kospex_query import KospexData

        db = sqlite_utils.Database(memory=True)
        for tag in _page().context["tags"]:
            # where_tag raises ValueError for a tag it does not recognise.
            KospexData(db).where_tag(tag)
