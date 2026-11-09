"""GitHub repo listings must be complete or fail loudly (#335 prerequisite).

_paginate used to stop at the first failed page, so a timeout, 5xx or rate
limit on page 3 of 5 returned a shorter list that looked complete. Callers
(kgit github, and the sync --org manifest) can't detect a missing repo, so a
failed page must raise instead.
"""
import pytest
import requests

from kospex_github import GitHubListingIncomplete, KospexGithub


def response(status):
    r = requests.Response()
    r.status_code = status
    return r


def page(n, start=0):
    return [{"full_name": f"acme/repo{i}", "name": f"repo{i}"} for i in range(start, start + n)]


def scripted(gh, monkeypatch, replies):
    """_make_request returns each reply in turn; records the page numbers asked for."""
    calls = []
    replies = list(replies)

    def fake(url, params=None, method="GET"):
        calls.append(dict(params or {}).get("page"))
        return replies.pop(0)

    monkeypatch.setattr(gh, "_make_request", fake)
    return calls


class TestPaginate:
    URL = "https://api.github.com/orgs/acme/repos"

    def test_single_short_page(self, monkeypatch):
        gh = KospexGithub()
        calls = scripted(gh, monkeypatch, [(page(3), response(200))])
        assert len(list(gh._paginate(self.URL))) == 3
        assert calls == [1]

    def test_multiple_pages(self, monkeypatch):
        gh = KospexGithub()
        calls = scripted(gh, monkeypatch, [
            (page(100), response(200)), (page(100, 100), response(200)),
            (page(7, 200), response(200))])
        items = list(gh._paginate(self.URL))
        assert len(items) == 207
        assert len({i["full_name"] for i in items}) == 207
        assert calls == [1, 2, 3]

    def test_exact_multiple_of_page_size_ends_on_empty_page(self, monkeypatch):
        gh = KospexGithub()
        calls = scripted(gh, monkeypatch, [(page(100), response(200)), ([], response(200))])
        assert len(list(gh._paginate(self.URL))) == 100
        assert calls == [1, 2]

    @pytest.mark.parametrize("reply, reason", [
        ((None, response(502)), "HTTP 502"),
        ((None, response(404)), "HTTP 404"),
        ((None, None), "no response"),
    ])
    def test_failed_later_page_raises(self, monkeypatch, reply, reason):
        gh = KospexGithub()
        scripted(gh, monkeypatch, [(page(100), response(200)), (page(100, 100), response(200)), reply])
        got = []
        with pytest.raises(GitHubListingIncomplete) as exc:
            for item in gh._paginate(self.URL):
                got.append(item)
        e = exc.value
        assert (e.page, e.items_read, e.reason) == (3, 200, reason)
        assert e.url == self.URL
        assert len(got) == 200  # what was yielded before the failure
        assert "page 3" in str(e) and reason in str(e)

    def test_failed_first_page_raises(self, monkeypatch):
        gh = KospexGithub()
        scripted(gh, monkeypatch, [(None, response(500))])
        with pytest.raises(GitHubListingIncomplete) as exc:
            list(gh._paginate(self.URL))
        assert (exc.value.page, exc.value.items_read) == (1, 0)

    def test_rate_limit_is_named(self, monkeypatch):
        gh = KospexGithub()

        def fake(url, params=None, method="GET"):
            if params["page"] == 1:
                return page(100), response(200)
            gh.throttled = True
            return None, response(403)

        monkeypatch.setattr(gh, "_make_request", fake)
        with pytest.raises(GitHubListingIncomplete) as exc:
            list(gh._paginate(self.URL))
        assert exc.value.reason == "rate limit exceeded"

    def test_non_list_response_still_yields_once(self, monkeypatch):
        gh = KospexGithub()
        scripted(gh, monkeypatch, [({"login": "acme"}, response(200))])
        assert list(gh._paginate(self.URL)) == [{"login": "acme"}]


class TestGetRepos:
    def test_failed_page_propagates(self, monkeypatch):
        gh = KospexGithub()

        def fake(url, params=None, method="GET"):
            if url.endswith("/users/acme"):
                return {"login": "acme", "type": "Organization"}, response(200)
            if params["page"] == 1:
                return page(100), response(200)
            return None, response(502)

        monkeypatch.setattr(gh, "_make_request", fake)
        with pytest.raises(GitHubListingIncomplete):
            gh.get_repos("acme", no_auth=True)

    def test_owner_not_found_is_empty_not_an_error(self, monkeypatch, capsys):
        gh = KospexGithub()
        monkeypatch.setattr(gh, "_make_request", lambda url, **kw: (None, response(404)))
        assert gh.get_repos("nobody-here", no_auth=True) == []
        assert "Could not find user or organization" in capsys.readouterr().out

    @pytest.mark.parametrize("reply, reason", [
        ((None, response(503)), "HTTP 503"),
        ((None, None), "no response"),
    ])
    def test_owner_lookup_failure_raises(self, monkeypatch, capsys, reply, reason):
        # A network blip must not read as "this org doesn't exist" - an empty
        # listing is exactly what a truncated manifest looks like.
        gh = KospexGithub()
        monkeypatch.setattr(gh, "_make_request", lambda url, **kw: reply)
        with pytest.raises(GitHubListingIncomplete) as exc:
            gh.get_repos("acme", no_auth=True)
        assert exc.value.reason == reason
        assert "Could not find" not in capsys.readouterr().out

    def test_complete_listing(self, monkeypatch):
        gh = KospexGithub()

        def fake(url, params=None, method="GET"):
            if url.endswith("/users/acme"):
                return {"login": "acme", "type": "Organization"}, response(200)
            return page(2), response(200)

        monkeypatch.setattr(gh, "_make_request", fake)
        repos = gh.get_repos("acme", no_auth=True)
        assert [r["name"] for r in repos] == ["acme/repo0", "acme/repo1"]


class TestKgitGithubCli:
    """kgit github: no partial table, no partial -out-repo-list file."""

    def run(self, monkeypatch, tmp_path, *args):
        from click.testing import CliRunner
        import kgit

        def incomplete(self, owner, no_auth=False, owned_only=False):
            raise GitHubListingIncomplete(
                "https://api.github.com/orgs/acme/repos", page=3, items_read=200,
                reason="HTTP 502")

        monkeypatch.setattr(KospexGithub, "get_account_type", lambda self, v: "Organization")
        monkeypatch.setattr(KospexGithub, "get_repos", incomplete)
        return CliRunner().invoke(kgit.cli, ["github", "-no-auth", *args, "acme"])

    def test_exits_1_with_reason(self, monkeypatch, tmp_path):
        result = self.run(monkeypatch, tmp_path)
        assert result.exit_code == 1
        assert "incomplete" in result.output
        assert "page 3" in result.output and "HTTP 502" in result.output

    def test_does_not_write_repo_list(self, monkeypatch, tmp_path):
        out = tmp_path / "repos.txt"
        result = self.run(monkeypatch, tmp_path, "-out-repo-list", str(out))
        assert result.exit_code == 1
        assert not out.exists()

    # -sync is now `kgit sync --org`, which refuses an incomplete listing itself
    # before saving or syncing anything (tests/test_kgit_sync_org_cli.py).
