"""`kgit github --dependabot`: open Dependabot alerts to JSONL, plus a per-repo status file.

The alert file is loaded by other scripts, so its shape is the contract: one
flat line per open alert, every key on every line, and the same `repo_id` /
`github_repo_id` as the custom properties export so the two join.
"""
import json

import pytest
import requests

import kospex.github_dependabot as GD
import kospex.github_properties as GP
from kospex_github import KospexGithub

FETCHED_AT = "2026-11-09T03:12:00Z"
BASE = "https://api.github.com/repos"


def response(status, body=None, link=None):
    r = requests.Response()
    r.status_code = status
    if body is not None:
        r._content = json.dumps(body).encode()
    if link:
        r.headers["Link"] = f'<{link}>; rel="next"'
    return r


def alert(number=1, **over):
    """An alert in the shape the repo alerts endpoint returns (measured 2026-11-09)."""
    a = {
        "number": number,
        "state": "open",
        "html_url": f"https://github.com/acme/payments/security/dependabot/{number}",
        "created_at": "2026-11-01T00:00:00Z",
        "updated_at": "2026-11-02T00:00:00Z",
        "dependency": {
            "package": {"ecosystem": "pip", "name": "requests"},
            "manifest_path": "requirements.txt",
            "scope": "runtime",
            "relationship": "direct",
            "version_requirement": "= 2.19.0",
        },
        "security_vulnerability": {
            "vulnerable_version_range": "< 2.20.0",
            "first_patched_version": {"identifier": "2.20.0"},
        },
        "security_advisory": {
            "ghsa_id": "GHSA-x84v-xcm2-53pg",
            "cve_id": "CVE-2018-18074",
            "summary": "Insufficiently Protected Credentials in Requests",
            "severity": "high",
            "published_at": "2018-10-29T19:06:46Z",
            "cwes": [{"cwe_id": "CWE-522", "name": "Insufficiently Protected Credentials"}],
            "cvss_severities": {
                "cvss_v3": {"vector_string": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
                            "score": 9.8},
                "cvss_v4": {"vector_string": None, "score": 0.0},
            },
            "epss": {"percentage": 0.01134, "percentile": 0.66524},
        },
    }
    a.update(over)
    return a


def scripted(gh, monkeypatch, replies):
    """Fake _make_request: replies is a list of (data, response), consumed in order."""
    calls = []

    def fake(url, params=None, method="GET", quiet_statuses=()):
        calls.append((url, params))
        gh.quiet_seen = quiet_statuses
        return replies.pop(0)

    monkeypatch.setattr(gh, "_make_request", fake)
    return calls


class TestClassifyFailure:
    def test_404_is_not_found(self):
        assert GD.classify_failure(response(404, {"message": "Not Found"})) == (
            "not_found", "HTTP 404 Not Found")

    def test_disabled_403(self):
        r = response(403, {"message": "Dependabot alerts are disabled for this repository."})
        assert GD.classify_failure(r) == ("disabled", None)

    def test_not_authorized_403_is_no_access(self):
        r = response(403, {"message": "You are not authorized to perform this operation."})
        assert GD.classify_failure(r) == (
            "no_access", "HTTP 403 You are not authorized to perform this operation.")

    def test_fine_grained_403_is_no_access(self):
        r = response(403, {"message": "Resource not accessible by personal access token"})
        assert GD.classify_failure(r)[0] == "no_access"

    def test_unknown_403_is_error(self):
        assert GD.classify_failure(response(403, {"message": "Something else"})) == (
            "error", "HTTP 403 Something else")

    def test_no_response_is_error(self):
        assert GD.classify_failure(None) == ("error", "no response")


class TestFetchRepoAlerts:
    def test_first_request_asks_for_open_alerts(self, monkeypatch):
        gh = KospexGithub()
        calls = scripted(gh, monkeypatch, [([], response(200, []))])
        GD.fetch_repo_alerts(gh, "acme", "payments")
        url, params = calls[0]
        assert url == f"{BASE}/acme/payments/dependabot/alerts"
        assert params == {"state": "open", "per_page": 100}

    def test_follows_next_link_until_none(self, monkeypatch):
        gh = KospexGithub()
        nxt = "https://api.github.com/repositories/1/dependabot/alerts?state=open&per_page=100&after=X"
        page1 = [alert(n) for n in range(100)]
        page2 = [alert(n) for n in range(100, 103)]
        calls = scripted(gh, monkeypatch, [(page1, response(200, [], link=nxt)),
                                           (page2, response(200, []))])
        status, alerts, error = GD.fetch_repo_alerts(gh, "acme", "payments")
        assert (status, len(alerts), error) == ("enabled", 103, None)
        assert calls[1] == (nxt, None)

    def test_expected_failures_are_quiet(self, monkeypatch):
        # 403 and 404 become a status line, so they shouldn't also print "HTTP Error".
        gh = KospexGithub()
        scripted(gh, monkeypatch, [([], response(200, []))])
        GD.fetch_repo_alerts(gh, "acme", "payments")
        assert set(gh.quiet_seen) == {403, 404}

    def test_enabled_with_no_alerts(self, monkeypatch):
        gh = KospexGithub()
        scripted(gh, monkeypatch, [([], response(200, []))])
        assert GD.fetch_repo_alerts(gh, "acme", "payments") == ("enabled", [], None)

    def test_first_page_failure_is_classified(self, monkeypatch):
        gh = KospexGithub()
        scripted(gh, monkeypatch, [(None, response(
            403, {"message": "Dependabot alerts are disabled for this repository."}))])
        assert GD.fetch_repo_alerts(gh, "acme", "payments") == ("disabled", None, None)

    def test_later_page_failure_is_error_without_alerts(self, monkeypatch):
        gh = KospexGithub()
        nxt = "https://api.github.com/repositories/1/dependabot/alerts?after=X"
        scripted(gh, monkeypatch, [([alert(1)], response(200, [], link=nxt)),
                                   (None, response(502, {"message": "Bad Gateway"}))])
        assert GD.fetch_repo_alerts(gh, "acme", "payments") == (
            "error", None, "HTTP 502 Bad Gateway (page 2)")

    def test_rate_limit_raises(self, monkeypatch):
        gh = KospexGithub()

        def throttled(url, params=None, method="GET", quiet_statuses=()):
            gh.throttled = True
            return None, response(429)

        monkeypatch.setattr(gh, "_make_request", throttled)
        with pytest.raises(GP.RateLimited):
            GD.fetch_repo_alerts(gh, "acme", "payments")


class TestCurrentVersion:
    def test_exact_pin(self):
        assert GD.current_version("= 2.2.0") == "2.2.0"

    def test_bare_lockfile_version(self):
        # npm lockfile alerts carry the resolved version with no operator (measured).
        assert GD.current_version("0.4.0") == "0.4.0"

    def test_range_is_none(self):
        assert GD.current_version(">= 2.10, < 3.0") is None
        assert GD.current_version("< 1.25") is None
        assert GD.current_version("^4.17.1") is None
        assert GD.current_version("~= 1.4") is None

    @pytest.mark.parametrize("requirement", [
        "*", "1.0.*", "1.x", "1.2.X", "latest", "[1.2.3]", "${dep.version}", "= 1.x"])
    def test_wildcards_and_placeholders_are_none(self, requirement):
        assert GD.current_version(requirement) is None

    @pytest.mark.parametrize("requirement,expected", [
        ("1.0.0-beta.1", "1.0.0-beta.1"), ("= 2.0.0rc1", "2.0.0rc1"),
        ("1.2.3+build.5", "1.2.3+build.5"), ("v1.4.2", "v1.4.2")])
    def test_real_versions_with_suffixes_are_kept(self, requirement, expected):
        assert GD.current_version(requirement) == expected

    def test_empty_and_none_are_none(self):
        # A ranged pip requirement comes back as "" (measured).
        assert GD.current_version("") is None
        assert GD.current_version(None) is None


class TestAlertLine:
    def line(self, a=None):
        return GD.alert_line(a or alert(7), "Acme", "Payments", 101, FETCHED_AT)

    def test_keys_and_order(self):
        assert tuple(self.line()) == GD.ALERT_KEYS

    def test_fields_map_from_alert(self):
        line = self.line()
        assert line["repo_id"] == "github.com~acme~payments"
        assert line["github_repo_id"] == 101
        assert (line["org"], line["repo"]) == ("Acme", "Payments")
        assert line["manifest_path"] == "requirements.txt"
        assert (line["package_ecosystem"], line["package_name"]) == ("pip", "requests")
        assert (line["relationship"], line["scope"]) == ("direct", "runtime")
        assert line["version_requirement"] == "= 2.19.0"
        assert line["current_version"] == "2.19.0"
        assert line["vulnerable_range"] == "< 2.20.0"
        assert line["patched_version"] == "2.20.0"
        assert (line["ghsa_id"], line["cve_id"]) == ("GHSA-x84v-xcm2-53pg", "CVE-2018-18074")
        assert line["severity"] == "high"
        assert line["published_at"] == "2018-10-29T19:06:46Z"
        assert line["cvss_v3_score"] == 9.8
        assert line["cvss_v3_vector"].startswith("CVSS:3.1/")
        assert (line["epss_percentage"], line["epss_percentile"]) == (0.01134, 0.66524)
        assert line["alert_number"] == 7
        assert line["alert_url"].endswith("/dependabot/7")
        assert line["state"] == "open"
        assert (line["created_at"], line["updated_at"]) == ("2026-11-01T00:00:00Z",
                                                             "2026-11-02T00:00:00Z")
        assert line["fetched_at"] == FETCHED_AT

    def test_cwes_are_ids(self):
        assert self.line()["cwes"] == ["CWE-522"]
        a = alert()
        a["security_advisory"]["cwes"] = []
        assert self.line(a)["cwes"] == []

    def test_absent_cvss_is_null(self):
        # GitHub reports an unscored version as score 0.0 with no vector (measured).
        line = self.line()
        assert line["cvss_v4_score"] is None
        assert line["cvss_v4_vector"] is None

    def test_zero_score_with_vector_is_kept(self):
        a = alert()
        a["security_advisory"]["cvss_severities"]["cvss_v3"] = {
            "vector_string": "CVSS:3.1/AV:N/AC:H/PR:H/UI:R/S:U/C:N/I:N/A:N", "score": 0.0}
        assert self.line(a)["cvss_v3_score"] == 0.0

    def test_missing_cvss_severities_and_epss(self):
        a = alert()
        del a["security_advisory"]["cvss_severities"]
        del a["security_advisory"]["epss"]
        line = self.line(a)
        for key in ("cvss_v3_score", "cvss_v3_vector", "cvss_v4_score", "cvss_v4_vector",
                    "epss_percentage", "epss_percentile"):
            assert line[key] is None, key

    def test_no_fix_patched_version_is_none(self):
        a = alert()
        a["security_vulnerability"]["first_patched_version"] = None
        assert self.line(a)["patched_version"] is None


class TestCollect:
    """The run: one status line per repo, alert lines for enabled repos, early stop for -org."""

    def fake_fetch(self, monkeypatch, results):
        """results: {repo: (status, alerts, error)}; records the order repos were fetched."""
        seen = []

        def fetch(gh, owner, repo):
            seen.append(repo)
            return results[repo]

        monkeypatch.setattr(GD, "fetch_repo_alerts", fetch)
        return seen

    def test_status_line_keys_and_order(self):
        line = GD.status_line("Acme", "Payments", 101, "enabled", 3, None, FETCHED_AT)
        assert tuple(line) == GD.STATUS_KEYS
        assert line["repo_id"] == "github.com~acme~payments"

    def test_enabled_counts_alerts(self, monkeypatch):
        self.fake_fetch(monkeypatch, {"pay": ("enabled", [alert(1), alert(2), alert(3)], None)})
        alerts, statuses = GD.collect(KospexGithub(), [("acme", "pay", 101)], FETCHED_AT, False)
        assert statuses[0]["dependabot_status"] == "enabled"
        assert statuses[0]["open_alerts"] == 3
        assert statuses[0]["error"] is None
        assert [a["alert_number"] for a in alerts] == [1, 2, 3]
        assert {a["github_repo_id"] for a in alerts} == {101}

    @pytest.mark.parametrize("status,error", [
        ("disabled", None), ("no_access", "HTTP 403 x"), ("not_found", "HTTP 404"),
        ("error", "HTTP 502")])
    def test_open_alerts_null_unless_enabled(self, monkeypatch, status, error):
        self.fake_fetch(monkeypatch, {"pay": (status, None, error)})
        _, statuses = GD.collect(KospexGithub(), [("acme", "pay", 1)], FETCHED_AT, False)
        assert statuses[0]["open_alerts"] is None
        assert statuses[0]["error"] == error

    def test_failed_repo_has_status_but_no_alerts(self, monkeypatch):
        self.fake_fetch(monkeypatch, {"a": ("enabled", [alert(1)], None),
                                      "b": ("error", None, "HTTP 502")})
        alerts, statuses = GD.collect(
            KospexGithub(), [("acme", "a", 1), ("acme", "b", 2)], FETCHED_AT, False)
        assert [s["repo"] for s in statuses] == ["a", "b"]
        assert {a["repo"] for a in alerts} == {"a"}

    def test_one_fetched_at_on_every_line(self, monkeypatch):
        self.fake_fetch(monkeypatch, {"a": ("enabled", [alert(1)], None)})
        alerts, statuses = GD.collect(KospexGithub(), [("acme", "a", 1)], FETCHED_AT, False)
        assert {l["fetched_at"] for l in alerts + statuses} == {FETCHED_AT}

    def test_early_stop_when_first_five_no_access(self, monkeypatch):
        repos = [f"r{i}" for i in range(7)]
        seen = self.fake_fetch(monkeypatch, {r: ("no_access", None, "HTTP 403") for r in repos})
        with pytest.raises(GD.NoAlertAccess):
            GD.collect(KospexGithub(), [("acme", r, i) for i, r in enumerate(repos)],
                       FETCHED_AT, True)
        assert seen == repos[:5]

    def test_no_early_stop_if_any_of_first_five_ok(self, monkeypatch):
        repos = [f"r{i}" for i in range(7)]
        results = {r: ("no_access", None, "HTTP 403") for r in repos}
        results["r2"] = ("enabled", [], None)
        self.fake_fetch(monkeypatch, results)
        _, statuses = GD.collect(KospexGithub(), [("acme", r, i) for i, r in enumerate(repos)],
                                 FETCHED_AT, True)
        assert len(statuses) == 7

    def test_early_stop_with_fewer_than_five(self, monkeypatch):
        self.fake_fetch(monkeypatch, {"a": ("no_access", None, "x"), "b": ("no_access", None, "x")})
        with pytest.raises(GD.NoAlertAccess):
            GD.collect(KospexGithub(), [("acme", "a", 1), ("acme", "b", 2)], FETCHED_AT, True)

    def test_no_early_stop_for_single_repo(self, monkeypatch):
        self.fake_fetch(monkeypatch, {"a": ("no_access", None, "HTTP 403 x")})
        _, statuses = GD.collect(KospexGithub(), [("acme", "a", 1)], FETCHED_AT, False)
        assert statuses[0]["dependabot_status"] == "no_access"

    def test_rate_limit_propagates(self, monkeypatch):
        def limited(gh, owner, repo):
            raise GP.RateLimited(f"{owner}/{repo}")

        monkeypatch.setattr(GD, "fetch_repo_alerts", limited)
        with pytest.raises(GP.RateLimited):
            GD.collect(KospexGithub(), [("acme", "a", 1)], FETCHED_AT, False)

    def test_on_repo_called_after_each_repo_in_order(self, monkeypatch):
        self.fake_fetch(monkeypatch, {"a": ("enabled", [alert(1), alert(2)], None),
                                      "b": ("disabled", None, None)})
        calls = []
        GD.collect(KospexGithub(), [("acme", "a", 1), ("acme", "b", 2)], FETCHED_AT, False,
                   on_repo=lambda i, n, s, a: calls.append((i, n, s["repo"], len(a))))
        assert calls == [(1, 2, "a", 2), (2, 2, "b", 0)]

    def test_on_repo_sees_repos_done_before_a_rate_limit(self, monkeypatch):
        def fetch(gh, owner, repo):
            if repo == "b":
                raise GP.RateLimited(f"{owner}/{repo}")
            return "enabled", [alert(1)], None

        monkeypatch.setattr(GD, "fetch_repo_alerts", fetch)
        done = []
        with pytest.raises(GP.RateLimited):
            GD.collect(KospexGithub(), [("acme", "a", 1), ("acme", "b", 2)], FETCHED_AT, False,
                       on_repo=lambda i, n, s, a: done.append(s["repo"]))
        assert done == ["a"]

    def test_on_repo_sees_early_stopped_repos(self, monkeypatch):
        repos = [f"r{i}" for i in range(7)]
        self.fake_fetch(monkeypatch, {r: ("no_access", None, "HTTP 403") for r in repos})
        done = []
        with pytest.raises(GD.NoAlertAccess):
            GD.collect(KospexGithub(), [("acme", r, i) for i, r in enumerate(repos)],
                       FETCHED_AT, True, on_repo=lambda i, n, s, a: done.append(s["repo"]))
        assert done == repos[:5]


class TestProgress:
    def test_enabled_shows_alert_count(self):
        line = GD.status_line("acme", "payments", 1, "enabled", 7, None, FETCHED_AT)
        assert GD.progress_line(412, 1300, line) == "[412/1300] acme/payments: enabled, 7 open alerts"

    def test_one_alert_is_singular(self):
        line = GD.status_line("acme", "payments", 1, "enabled", 1, None, FETCHED_AT)
        assert GD.progress_line(1, 2, line).endswith("enabled, 1 open alert")

    def test_disabled_has_no_count(self):
        line = GD.status_line("acme", "docs", 1, "disabled", None, None, FETCHED_AT)
        assert GD.progress_line(3, 9, line) == "[3/9] acme/docs: disabled"

    def test_error_shows_the_error(self):
        line = GD.status_line("acme", "x", 1, "error", None, "HTTP 502", FETCHED_AT)
        assert GD.progress_line(3, 9, line) == "[3/9] acme/x: error (HTTP 502)"


class TestRunFiles:
    """Lines go to .partial files as each repo finishes; only a finished run renames them."""

    def lines(self, path):
        return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]

    def test_lines_are_readable_before_the_run_finishes(self, tmp_path):
        out, status = tmp_path / "a.jsonl", tmp_path / "a-status.jsonl"
        with GD.RunFiles(out, status) as files:
            files.add(GD.status_line("acme", "a", 1, "enabled", 1, None, FETCHED_AT),
                      [GD.alert_line(alert(1), "acme", "a", 1, FETCHED_AT)])
            assert len(self.lines(tmp_path / "a.jsonl.partial")) == 1
            assert len(self.lines(tmp_path / "a-status.jsonl.partial")) == 1
            assert not out.exists() and not status.exists()

    def test_finish_renames_partials_to_the_final_names(self, tmp_path):
        out, status = tmp_path / "a.jsonl", tmp_path / "a-status.jsonl"
        with GD.RunFiles(out, status) as files:
            files.add(GD.status_line("acme", "a", 1, "enabled", 1, None, FETCHED_AT),
                      [GD.alert_line(alert(1), "acme", "a", 1, FETCHED_AT)])
            files.finish()
        assert tuple(self.lines(out)[0]) == GD.ALERT_KEYS
        assert tuple(self.lines(status)[0]) == GD.STATUS_KEYS
        assert sorted(p.name for p in tmp_path.iterdir()) == ["a-status.jsonl", "a.jsonl"]

    def test_unfinished_run_keeps_partials_and_leaves_old_files_alone(self, tmp_path):
        out, status = tmp_path / "a.jsonl", tmp_path / "a-status.jsonl"
        out.write_text("previous\n")
        with pytest.raises(GP.RateLimited):
            with GD.RunFiles(out, status) as files:
                files.add(GD.status_line("acme", "a", 1, "disabled", None, None, FETCHED_AT), [])
                raise GP.RateLimited("acme/b")
        assert out.read_text() == "previous\n"
        assert not status.exists()
        assert self.lines(tmp_path / "a-status.jsonl.partial")[0]["repo"] == "a"

    def test_counts_repos_and_alerts_written(self, tmp_path):
        with GD.RunFiles(tmp_path / "a.jsonl", tmp_path / "s.jsonl") as files:
            files.add(GD.status_line("acme", "a", 1, "enabled", 2, None, FETCHED_AT),
                      [GD.alert_line(alert(n), "acme", "a", 1, FETCHED_AT) for n in (1, 2)])
            files.add(GD.status_line("acme", "b", 2, "disabled", None, None, FETCHED_AT), [])
            assert (files.repos, files.alerts) == (2, 2)

    def test_unicode_kept_and_newline_terminated(self, tmp_path):
        with GD.RunFiles(tmp_path / "a.jsonl", tmp_path / "s.jsonl") as files:
            files.add(GD.status_line("acme", "a", 1, "error", None, "café", FETCHED_AT), [])
            files.finish()
        text = (tmp_path / "s.jsonl").read_text(encoding="utf-8")
        assert "café" in text and text.endswith("\n")


class TestFilesAndSummary:
    @pytest.mark.parametrize("given,expected", [
        ("alerts.jsonl", "alerts-status.jsonl"),
        ("out/a.jsonl", "out/a-status.jsonl"),
        ("alerts", "alerts-status.jsonl"),
        ("a.json", "a-status.json"),
    ])
    def test_status_filename(self, given, expected):
        assert GD.status_filename(given) == expected

    def test_summarise(self):
        statuses = [{"dependabot_status": s} for s in ("enabled", "enabled", "disabled")]
        alerts = [{"severity": s} for s in ("high", "high", "critical")]
        assert GD.summarise(alerts, statuses) == {
            "repos": 3, "alerts": 3,
            "by_status": {"enabled": 2, "disabled": 1},
            "by_severity": {"high": 2, "critical": 1}}

    def test_notes_org_scope(self):
        text = " ".join(GD.notes([{"dependabot_status": "enabled"}], org_scope=True))
        assert "public repos only" in text

    def test_notes_majority_no_access(self):
        statuses = [{"dependabot_status": "no_access"}] * 3 + [{"dependabot_status": "enabled"}]
        text = " ".join(GD.notes(statuses, org_scope=False))
        assert "Write, Maintain, Admin or Security Manager" in text

    def test_notes_warn_ranged_requirements_are_invisible(self):
        # Measured: ranged pip requirements get no alerts unless the advisory
        # covers every version, so "no alerts" isn't "no vulnerable dependencies".
        text = " ".join(GD.notes([{"dependabot_status": "enabled"}], org_scope=False))
        assert "unpinned" in text

    def test_no_access_note_absent_when_minority(self):
        statuses = [{"dependabot_status": "no_access"}] + [{"dependabot_status": "enabled"}] * 3
        text = " ".join(GD.notes(statuses, org_scope=False))
        assert "Security Manager" not in text


class TestCli:
    """kgit github --dependabot: options, progress, and files written as the run goes."""

    @pytest.fixture
    def kgit_cli(self, monkeypatch):
        import kgit
        monkeypatch.setattr(KospexGithub, "get_env_credentials", lambda self: True)
        return kgit.cli

    @pytest.fixture
    def logged(self, monkeypatch):
        """kgit's log.info and log.warning messages."""
        import kgit
        messages = []
        record = lambda msg, *a, **k: messages.append(msg % a if a else msg)  # noqa: E731
        monkeypatch.setattr(kgit.log, "info", record)
        monkeypatch.setattr(kgit.log, "warning", record)
        return messages

    @staticmethod
    def fake_collect(monkeypatch, per_repo, then=None):
        """Fake collect: reports each (status_line, alert_lines) to on_repo, then raises `then`."""

        def fake(gh, targets, fetched_at, early_stop, on_repo=None):
            alerts, statuses = [], []
            for i, (status, repo_alerts) in enumerate(per_repo, start=1):
                statuses.append(status)
                alerts.extend(repo_alerts)
                if on_repo:
                    on_repo(i, len(targets), status, repo_alerts)
            if then:
                raise then
            return alerts, statuses

        monkeypatch.setattr(GD, "collect", fake)

    @pytest.fixture
    def collected(self, monkeypatch):
        """Fake collect; records its arguments and returns one alert and one status line."""
        seen = {}

        def fake(gh, targets, fetched_at, early_stop, on_repo=None):
            seen.update(targets=targets, early_stop=early_stop)
            alerts = [GD.alert_line(alert(1), "acme", "a", 1, fetched_at)]
            statuses = [GD.status_line("acme", "a", 1, "enabled", 1, None, fetched_at)]
            if on_repo:
                on_repo(1, len(targets), statuses[0], alerts)
            return alerts, statuses

        monkeypatch.setattr(GD, "collect", fake)
        return seen

    def run(self, cli, args):
        from click.testing import CliRunner
        return CliRunner().invoke(cli, ["github", *args])

    def test_needs_filename(self, kgit_cli):
        result = self.run(kgit_cli, ["--dependabot", "-org", "acme"])
        assert result.exit_code != 0
        assert "-filename" in result.output

    @pytest.mark.parametrize("args", [[], ["-org", "acme", "-repo_id", "github.com~acme~a"]])
    def test_needs_exactly_one_scope(self, kgit_cli, tmp_path, args):
        out = tmp_path / "a.jsonl"
        result = self.run(kgit_cli, ["--dependabot", "-filename", str(out), *args])
        assert result.exit_code != 0
        assert "-org" in result.output and "-repo_id" in result.output
        assert not out.exists()

    def test_level_is_refused(self, kgit_cli, tmp_path):
        result = self.run(kgit_cli, ["--dependabot", "-org", "acme", "-level", "repo",
                                     "-filename", str(tmp_path / "a.jsonl")])
        assert result.exit_code != 0
        assert "-level" in result.output

    def test_properties_and_dependabot_together_refused(self, kgit_cli, tmp_path):
        result = self.run(kgit_cli, ["--dependabot", "--properties", "-org", "acme",
                                     "-filename", str(tmp_path / "a.jsonl")])
        assert result.exit_code != 0
        assert "--properties" in result.output and "--dependabot" in result.output

    def test_single_dash_accepted(self, kgit_cli, tmp_path, monkeypatch, collected):
        monkeypatch.setattr(GP, "lookup_repo", lambda gh, owner, repo: (owner, repo, 1))
        result = self.run(kgit_cli, ["-dependabot", "-repo_id", "github.com~acme~a",
                                     "-filename", str(tmp_path / "a.jsonl")])
        assert result.exit_code == 0, result.output

    def test_repo_id_uses_lookup_and_no_early_stop(self, kgit_cli, tmp_path, monkeypatch,
                                                   collected):
        monkeypatch.setattr(GP, "lookup_repo",
                            lambda gh, owner, repo: ("Acme", "Payments", 101))
        result = self.run(kgit_cli, ["--dependabot", "-repo_id", "github.com~acme~payments",
                                     "-filename", str(tmp_path / "a.jsonl")])
        assert result.exit_code == 0, result.output
        assert collected == {"targets": [("Acme", "Payments", 101)], "early_stop": False}

    def test_org_walks_listing_with_early_stop(self, kgit_cli, tmp_path, monkeypatch, collected):
        monkeypatch.setattr(GP, "list_org_repos", lambda gh, org: [("acme", "a", 1),
                                                                    ("acme", "b", 2)])
        result = self.run(kgit_cli, ["--dependabot", "-org", "acme",
                                     "-filename", str(tmp_path / "a.jsonl")])
        assert result.exit_code == 0, result.output
        assert collected == {"targets": [("acme", "a", 1), ("acme", "b", 2)],
                             "early_stop": True}

    def test_writes_both_files(self, kgit_cli, tmp_path, monkeypatch, collected):
        monkeypatch.setattr(GP, "list_org_repos", lambda gh, org: [("acme", "a", 1)])
        out = tmp_path / "alerts.jsonl"
        result = self.run(kgit_cli, ["--dependabot", "-org", "acme", "-filename", str(out)])
        assert result.exit_code == 0, result.output
        alerts = [json.loads(l) for l in out.read_text().splitlines()]
        statuses = [json.loads(l) for l in
                    (tmp_path / "alerts-status.jsonl").read_text().splitlines()]
        assert tuple(alerts[0]) == GD.ALERT_KEYS
        assert tuple(statuses[0]) == GD.STATUS_KEYS

    def test_summary_prints_both_paths(self, kgit_cli, tmp_path, monkeypatch, collected):
        monkeypatch.setattr(GP, "list_org_repos", lambda gh, org: [("acme", "a", 1)])
        out = tmp_path / "alerts.jsonl"
        result = self.run(kgit_cli, ["--dependabot", "-org", "acme", "-filename", str(out)])
        flat = result.output.replace("\n", "")
        assert "alerts.jsonl" in flat and "alerts-status.jsonl" in flat

    def two_repos(self, monkeypatch):
        monkeypatch.setattr(GP, "list_org_repos", lambda gh, org: [("acme", "a", 1),
                                                                    ("acme", "b", 2),
                                                                    ("acme", "c", 3)])
        return [
            (GD.status_line("acme", "a", 1, "enabled", 2, None, FETCHED_AT),
             [GD.alert_line(alert(n), "acme", "a", 1, FETCHED_AT) for n in (1, 2)]),
            (GD.status_line("acme", "b", 2, "disabled", None, None, FETCHED_AT), []),
        ]

    def test_prints_a_progress_line_per_repo(self, kgit_cli, tmp_path, monkeypatch):
        self.fake_collect(monkeypatch, self.two_repos(monkeypatch))
        result = self.run(kgit_cli, ["--dependabot", "-org", "acme",
                                     "-filename", str(tmp_path / "a.jsonl")])
        assert result.exit_code == 0, result.output
        assert "[1/3] acme/a: enabled, 2 open alerts" in result.output
        assert "[2/3] acme/b: disabled" in result.output

    def test_logs_start_each_repo_and_finish(self, kgit_cli, tmp_path, monkeypatch, logged):
        self.fake_collect(monkeypatch, self.two_repos(monkeypatch))
        out = tmp_path / "a.jsonl"
        result = self.run(kgit_cli, ["--dependabot", "-org", "acme", "-filename", str(out)])
        assert result.exit_code == 0, result.output
        text = "\n".join(logged)
        assert "3 repos" in text and str(out) in text
        assert "[1/3] acme/a: enabled, 2 open alerts" in text
        assert "[2/3] acme/b: disabled" in text
        assert "finished" in text.lower()

    def test_finished_run_leaves_no_partial_files(self, kgit_cli, tmp_path, monkeypatch):
        self.fake_collect(monkeypatch, self.two_repos(monkeypatch))
        result = self.run(kgit_cli, ["--dependabot", "-org", "acme",
                                     "-filename", str(tmp_path / "a.jsonl")])
        assert result.exit_code == 0, result.output
        assert sorted(p.name for p in tmp_path.iterdir()) == ["a-status.jsonl", "a.jsonl"]
        assert len((tmp_path / "a.jsonl").read_text().splitlines()) == 2

    def test_rate_limit_keeps_the_repos_done_in_partial_files(self, kgit_cli, tmp_path,
                                                              monkeypatch, logged):
        self.fake_collect(monkeypatch, self.two_repos(monkeypatch),
                          then=GP.RateLimited("acme/c"))
        out = tmp_path / "alerts.jsonl"
        out.write_text("previous run\n")
        result = self.run(kgit_cli, ["--dependabot", "-org", "acme", "-filename", str(out)])
        assert result.exit_code != 0
        flat = result.output.replace("\n", " ")
        assert "rate limit" in flat
        assert "2 of 3 repos" in flat and "alerts.jsonl.partial" in flat
        assert "Nothing was written" not in flat
        assert out.read_text() == "previous run\n"
        assert len((tmp_path / "alerts.jsonl.partial").read_text().splitlines()) == 2
        assert len((tmp_path / "alerts-status.jsonl.partial").read_text().splitlines()) == 2
        assert any("rate limit" in m for m in logged)

    def test_no_alert_access_keeps_partial_files(self, kgit_cli, tmp_path, monkeypatch, logged):
        monkeypatch.setattr(GP, "list_org_repos", lambda gh, org: [("acme", "a", 1)])
        denied = [(GD.status_line("acme", "a", 1, "no_access", None, "HTTP 403", FETCHED_AT), [])]
        self.fake_collect(monkeypatch, denied,
                          then=GD.NoAlertAccess("the first 1 repos were all no_access"))
        out = tmp_path / "alerts.jsonl"
        result = self.run(kgit_cli, ["--dependabot", "-org", "acme", "-filename", str(out)])
        assert result.exit_code != 0
        assert "Security Manager" in result.output
        assert not out.exists()
        assert (tmp_path / "alerts-status.jsonl.partial").exists()

    def test_ctrl_c_keeps_partial_files_and_says_where(self, kgit_cli, tmp_path, monkeypatch,
                                                       logged):
        self.fake_collect(monkeypatch, self.two_repos(monkeypatch), then=KeyboardInterrupt())
        out = tmp_path / "alerts.jsonl"
        result = self.run(kgit_cli, ["--dependabot", "-org", "acme", "-filename", str(out)])
        assert result.exit_code != 0
        flat = result.output.replace("\n", " ")
        assert "2 of 3 repos" in flat and "alerts.jsonl.partial" in flat
        assert not out.exists()
        assert len((tmp_path / "alerts.jsonl.partial").read_text().splitlines()) == 2

    def test_listing_failure_writes_nothing(self, kgit_cli, tmp_path, monkeypatch):
        from kospex_github import GitHubListingIncomplete

        def fail(gh, org):
            raise GitHubListingIncomplete("u", 2, 100, "HTTP 502")

        monkeypatch.setattr(GP, "list_org_repos", fail)
        out = tmp_path / "alerts.jsonl"
        result = self.run(kgit_cli, ["--dependabot", "-org", "acme", "-filename", str(out)])
        assert result.exit_code != 0
        assert "Nothing was written" in result.output
        assert not out.exists() and not (tmp_path / "alerts-status.jsonl").exists()
