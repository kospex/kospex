"""`kgit github --properties`: GitHub custom property values to JSONL.

The file is loaded by other scripts, so its shape is the contract: one line per
repo, `properties` as a {name: value} object, `error` instead of `properties`
when a repo couldn't be read, and `level` / `fetched_at` on every line so files
from different orgs and runs can be concatenated.
"""
import json

import pytest
import requests
from click.testing import CliRunner

import kospex.github_properties as GP
from kospex_github import GitHubListingIncomplete, KospexGithub

FETCHED_AT = "2026-11-09T03:12:00Z"


def response(status, body=None):
    r = requests.Response()
    r.status_code = status
    if body is not None:
        r._content = json.dumps(body).encode()
    return r


def org_item(full_name, props):
    return {"repository_id": 1, "repository_name": full_name.split("/")[1],
            "repository_full_name": full_name,
            "properties": [{"property_name": k, "value": v} for k, v in props.items()]}


class TestNormalise:
    def test_list_becomes_object_keeping_arrays(self):
        props = [{"property_name": "team", "value": "billing"},
                 {"property_name": "compliance", "value": ["pci", "sox"]}]
        assert GP.normalise(props) == {"team": "billing", "compliance": ["pci", "sox"]}

    def test_unset_is_left_out(self):
        # The org endpoint returns every schema property, null when unset; the repo
        # endpoint omits unset ones. Both must normalise to the same object.
        org_endpoint = [{"property_name": "business_unit", "value": "Retail"},
                        {"property_name": "owner_team", "value": None}]
        repo_endpoint = [{"property_name": "business_unit", "value": "Retail"}]
        assert GP.normalise(org_endpoint) == GP.normalise(repo_endpoint) == {
            "business_unit": "Retail"}

    def test_empty_string_and_empty_list_are_kept(self):
        props = [{"property_name": "a", "value": ""}, {"property_name": "b", "value": []}]
        assert GP.normalise(props) == {"a": "", "b": []}

    def test_nothing_set_is_empty_object(self):
        assert GP.normalise([]) == {}
        assert GP.normalise([{"property_name": "a", "value": None}]) == {}


class TestOrgLevel:
    def test_one_line_per_repo_with_repo_id(self, monkeypatch):
        gh = KospexGithub()
        items = [org_item("Acme/Payments", {"team": "billing"}), org_item("Acme/docs", {})]
        monkeypatch.setattr(gh, "_paginate", lambda url, params=None: iter(items))

        lines = GP.org_level_lines(gh, "acme", FETCHED_AT)

        assert lines == [
            {"repo_id": "github.com~acme~payments", "org": "Acme", "repo": "Payments",
             "level": "org", "fetched_at": FETCHED_AT, "properties": {"team": "billing"},
             "error": None},
            {"repo_id": "github.com~acme~docs", "org": "Acme", "repo": "docs",
             "level": "org", "fetched_at": FETCHED_AT, "properties": {}, "error": None},
        ]

    def test_calls_the_org_properties_endpoint(self, monkeypatch):
        gh = KospexGithub()
        urls = []
        monkeypatch.setattr(gh, "_paginate", lambda url, params=None: urls.append(url) or iter([]))
        GP.org_level_lines(gh, "acme", FETCHED_AT)
        assert urls == ["https://api.github.com/orgs/acme/properties/values"]

    def test_failed_page_raises(self, monkeypatch):
        gh = KospexGithub()

        def failing(url, params=None):
            yield org_item("acme/a", {})
            raise GitHubListingIncomplete(url, 2, 100, "HTTP 502")

        monkeypatch.setattr(gh, "_paginate", failing)
        with pytest.raises(GitHubListingIncomplete):
            GP.org_level_lines(gh, "acme", FETCHED_AT)


class TestRepoLevel:
    def scripted(self, gh, monkeypatch, replies):
        calls = []

        def fake(url, params=None, method="GET"):
            calls.append(url)
            return replies[url]

        monkeypatch.setattr(gh, "_make_request", fake)
        return calls

    def test_properties_and_error_lines(self, monkeypatch):
        gh = KospexGithub()
        base = "https://api.github.com/repos"
        calls = self.scripted(gh, monkeypatch, {
            f"{base}/acme/payments/properties/values":
                ([{"property_name": "team", "value": "billing"}], response(200)),
            f"{base}/acme/empty/properties/values": ([], response(200)),
            f"{base}/acme/legacy/properties/values":
                (None, response(404, {"message": "Not Found"})),
        })

        lines = GP.repo_level_lines(
            gh, [("acme", "payments"), ("acme", "empty"), ("acme", "legacy")], FETCHED_AT)

        assert len(calls) == 3
        assert lines[0]["properties"] == {"team": "billing"}
        assert lines[0]["level"] == "repo"
        assert lines[1]["properties"] == {}
        assert lines[2] == {"repo_id": "github.com~acme~legacy", "org": "acme", "repo": "legacy",
                            "level": "repo", "fetched_at": FETCHED_AT,
                            "properties": None, "error": "HTTP 404 Not Found"}
        assert lines[1]["error"] is None

    def test_every_line_has_the_same_keys(self, monkeypatch):
        # sqlite-utils infers columns from the first 100 lines; a key that first
        # appears later aborts the load partway (measured).
        gh = KospexGithub()
        base = "https://api.github.com/repos"
        self.scripted(gh, monkeypatch, {
            f"{base}/acme/ok/properties/values": ([], response(200)),
            f"{base}/acme/bad/properties/values": (None, response(404)),
        })
        lines = GP.repo_level_lines(gh, [("acme", "ok"), ("acme", "bad")], FETCHED_AT)
        assert list(lines[0]) == list(lines[1]) == [
            "repo_id", "org", "repo", "level", "fetched_at", "properties", "error"]

    def test_no_response_is_an_error_line(self, monkeypatch):
        gh = KospexGithub()
        self.scripted(gh, monkeypatch, {
            "https://api.github.com/repos/acme/a/properties/values": (None, None)})
        lines = GP.repo_level_lines(gh, [("acme", "a")], FETCHED_AT)
        assert lines[0]["error"] == "no response"

    def test_rate_limit_stops_the_run(self, monkeypatch):
        gh = KospexGithub()

        def throttled(url, params=None, method="GET"):
            gh.throttled = True
            return None, response(403)

        monkeypatch.setattr(gh, "_make_request", throttled)
        with pytest.raises(GP.RateLimited):
            GP.repo_level_lines(gh, [("acme", "a"), ("acme", "b")], FETCHED_AT)

    def test_list_org_repos_uses_the_org_listing(self, monkeypatch):
        gh = KospexGithub()
        seen = []

        def listing(org, repo_type="all"):
            seen.append((org, repo_type))
            yield {"full_name": "Acme/Payments", "name": "Payments", "owner": {"login": "Acme"}}

        monkeypatch.setattr(gh, "_get_org_repos", listing)
        assert GP.list_org_repos(gh, "acme") == [("Acme", "Payments")]
        assert seen == [("acme", "all")]


class TestParseRepoId:
    def test_github_repo_id(self):
        assert GP.parse_github_repo_id("github.com~acme~payments") == ("acme", "payments")

    @pytest.mark.parametrize("bad", ["", "acme/payments", "gitlab.com~acme~payments",
                                     "github.com~acme", "gitlab.com~group~~sub~repo"])
    def test_refuses_anything_else(self, bad):
        with pytest.raises(ValueError):
            GP.parse_github_repo_id(bad)


class TestWriteJsonl:
    def test_one_object_per_line_newline_terminated_utf8(self, tmp_path):
        out = tmp_path / "props.jsonl"
        lines = [{"repo_id": "a", "properties": {"team": "Zürich"}}, {"repo_id": "b", "error": "x"}]
        GP.write_jsonl(out, lines)

        raw = out.read_bytes()
        assert raw.endswith(b"\n")
        assert "Zürich".encode() in raw  # not ü-escaped
        assert [json.loads(l) for l in raw.decode().splitlines()] == lines

    def test_concatenated_files_stay_valid_jsonl(self, tmp_path):
        a, b = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
        GP.write_jsonl(a, [{"repo_id": "1"}])
        GP.write_jsonl(b, [{"repo_id": "2"}])
        joined = a.read_text() + b.read_text()
        assert [json.loads(l)["repo_id"] for l in joined.splitlines()] == ["1", "2"]

    def test_overwrites(self, tmp_path):
        out = tmp_path / "props.jsonl"
        out.write_text("old\n")
        GP.write_jsonl(out, [{"repo_id": "a"}])
        assert out.read_text() == '{"repo_id": "a"}\n'


class TestSummary:
    def test_counts(self):
        lines = [{"properties": {"a": 1}}, {"properties": {}}, {"error": "HTTP 404"}]
        assert GP.summarise(lines) == {"repos": 3, "with_properties": 1,
                                       "no_properties": 1, "errors": 1}


class TestNotes:
    def test_org_scope_warns_listing_is_only_what_the_token_sees(self):
        text = " ".join(GP.notes([{"properties": {"a": 1}}], org_scope=True))
        assert "public repos only" in text
        assert "error line, not {}" in text

    def test_single_repo_has_no_listing_warning(self):
        text = " ".join(GP.notes([{"properties": {"a": 1}}], org_scope=False))
        assert "public repos only" not in text

    def test_all_empty_says_so(self):
        lines = [{"properties": {}}] * 37
        assert any("None of the 37 repos" in n for n in GP.notes(lines, True))

    def test_all_empty_note_skipped_when_some_errored(self):
        lines = [{"properties": {}}, {"error": "HTTP 404"}]
        assert not any("None of the" in n for n in GP.notes(lines, True))


class TestCli:
    """The options: one of -org / -repo_id, -filename mandatory, nothing written on failure."""

    @pytest.fixture
    def kgit_cli(self, monkeypatch):
        import kgit
        monkeypatch.setattr(KospexGithub, "get_env_credentials", lambda self: True)
        return kgit.cli

    def run(self, cli, args):
        return CliRunner().invoke(cli, ["github", *args])

    def test_needs_filename(self, kgit_cli):
        result = self.run(kgit_cli, ["--properties", "-org", "acme"])
        assert result.exit_code != 0
        assert "-filename" in result.output

    @pytest.mark.parametrize("args", [[], ["-org", "acme", "-repo_id", "github.com~acme~a"]])
    def test_needs_exactly_one_scope(self, kgit_cli, tmp_path, args):
        result = self.run(kgit_cli, ["--properties", "-filename", str(tmp_path / "p.jsonl"), *args])
        assert result.exit_code != 0
        assert "-org" in result.output and "-repo_id" in result.output
        assert not (tmp_path / "p.jsonl").exists()

    def test_single_dash_properties_accepted(self, kgit_cli, tmp_path, monkeypatch):
        out = tmp_path / "p.jsonl"
        monkeypatch.setattr(GP, "repo_level_lines",
                            lambda gh, repos, fetched_at: [{"repo_id": "x", "properties": {}}])
        result = self.run(kgit_cli, ["-properties", "-repo_id", "github.com~acme~a",
                                     "-filename", str(out)])
        assert result.exit_code == 0, result.output
        assert out.exists()

    def test_repo_id_cannot_use_org_level(self, kgit_cli, tmp_path):
        result = self.run(kgit_cli, ["--properties", "-repo_id", "github.com~acme~a",
                                     "-level", "org", "-filename", str(tmp_path / "p.jsonl")])
        assert result.exit_code != 0

    def test_org_level_failure_writes_nothing_and_points_at_repo_level(
            self, kgit_cli, tmp_path, monkeypatch):
        out = tmp_path / "p.jsonl"

        def fail(gh, org, fetched_at):
            raise GitHubListingIncomplete("u", 1, 0, "HTTP 404")

        monkeypatch.setattr(GP, "org_level_lines", fail)
        result = self.run(kgit_cli, ["--properties", "-org", "acme", "-filename", str(out)])
        assert result.exit_code != 0
        assert not out.exists()
        assert "-level repo" in result.output

    def test_repo_level_org_walks_the_listing(self, kgit_cli, tmp_path, monkeypatch):
        out = tmp_path / "p.jsonl"
        monkeypatch.setattr(GP, "list_org_repos", lambda gh, org: [("acme", "a"), ("acme", "b")])
        seen = []

        def lines(gh, repos, fetched_at):
            seen.extend(repos)
            return [{"repo_id": f"github.com~acme~{r}", "level": "repo",
                     "fetched_at": fetched_at, "properties": {}} for _, r in repos]

        monkeypatch.setattr(GP, "repo_level_lines", lines)
        result = self.run(kgit_cli, ["--properties", "-org", "acme", "-level", "repo",
                                     "-filename", str(out)])
        assert result.exit_code == 0, result.output
        assert seen == [("acme", "a"), ("acme", "b")]
        written = [json.loads(l) for l in out.read_text().splitlines()]
        assert [w["repo_id"] for w in written] == ["github.com~acme~a", "github.com~acme~b"]
        assert len({w["fetched_at"] for w in written}) == 1

    def test_rate_limit_writes_nothing(self, kgit_cli, tmp_path, monkeypatch):
        out = tmp_path / "p.jsonl"
        monkeypatch.setattr(GP, "list_org_repos", lambda gh, org: [("acme", "a")])

        def limited(gh, repos, fetched_at):
            raise GP.RateLimited("acme/a")

        monkeypatch.setattr(GP, "repo_level_lines", limited)
        result = self.run(kgit_cli, ["--properties", "-org", "acme", "-level", "repo",
                                     "-filename", str(out)])
        assert result.exit_code != 0
        assert not out.exists()

    def test_owner_still_required_without_properties(self, kgit_cli):
        result = self.run(kgit_cli, [])
        assert "You must specify an owner" in result.output
