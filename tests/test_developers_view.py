"""Query-string parsing, URLs and paging for /developers/."""
import pytest

from kospex import developers_view as view


def test_constants():
    assert (view.LIST_THRESHOLD, view.PAGE_SIZE, view.BUSIEST_LIMIT) == (250, 50, 10)


def test_defaults():
    assert view.parse_list_params({}) == {
        "status": None, "q": None, "sort": "last_seen", "dir": "asc", "page": 1}


def test_a_sort_brings_its_own_default_direction():
    assert view.parse_list_params({"sort": "commits"})["dir"] == "desc"
    assert view.parse_list_params({"sort": "commits", "dir": "asc"})["dir"] == "asc"


def test_status_and_search_are_normalised():
    assert view.parse_list_params({"status": "Active"})["status"] == "active"
    assert view.parse_list_params({"q": "  paul "})["q"] == "paul"
    assert view.parse_list_params({"q": "   "})["q"] is None


@pytest.mark.parametrize("params", [
    {"status": "bogus"}, {"sort": "bogus"}, {"dir": "up"},
    {"page": "0"}, {"page": "-1"}, {"page": "abc"}, {"page": "1.5"},
    {"page": "²"}, {"page": "99999999999999999999"},
])
def test_bad_values_raise(params):
    with pytest.raises(ValueError):
        view.parse_list_params(params)


@pytest.mark.parametrize("scope, params, url", [
    ({}, {}, "/developers/"),
    ({"server": "github.com"}, {}, "/developers/github.com"),
    ({"org_key": "gitlab.com~beta~~sub"}, {}, "/developers/gitlab.com~beta~~sub"),
    ({"repo_id": "github.com~acme~api"}, {"status": "active", "page": 2},
     "/developers/github.com~acme~api?status=active&page=2"),
    ({}, {"status": None, "q": "", "page": 3}, "/developers/?page=3"),
    ({}, {"q": "a&b c"}, "/developers/?q=a%26b+c"),
])
def test_developers_url(scope, params, url):
    assert view.developers_url(scope, **params) == url


@pytest.mark.parametrize("total, page, expected", [
    (0, 1, {"limit": None, "offset": 0, "page": 1, "pages": 1}),
    (250, 1, {"limit": None, "offset": 0, "page": 1, "pages": 1}),
    (251, 2, {"limit": 50, "offset": 50, "page": 2, "pages": 6}),
    (251, 99, {"limit": 50, "offset": 4900, "page": 99, "pages": 6}),
])
def test_page_window(total, page, expected):
    assert view.page_window(total, page) == expected
