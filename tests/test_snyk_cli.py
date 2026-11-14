"""`kospex snyk` commands, driven through Click with a fake client."""
import csv
import io

import pytest
from click.testing import CliRunner

from kospex.integrations.snyk import cli as snyk_cli
from kospex.integrations.snyk import queries
from kospex.integrations.snyk.client import ProbeResult, SnykNotAvailable
from snyk_fixtures import JACKSON, issue, org, project


class FakeClient:
    api_url = "https://api.snyk.io"

    def __init__(self, issues_by_org=None, projects_by_org=None, issues_error=None):
        self.requests = 0
        self.issues_by_org = issues_by_org or {}
        self.projects_by_org = projects_by_org or {}
        self.issues_error = issues_error
        self.issue_calls = []
        self.project_calls = []

    def orgs(self):
        self.requests += 1
        return [org("o1", "acme-payments"), org("o2", "acme-web")]

    def projects(self, org_id, ids=None, issue_counts=False):
        self.requests += 1
        self.project_calls.append((org_id, ids, issue_counts))
        found = self.projects_by_org.get(org_id, [])
        return [p for p in found if ids is None or p["id"] in ids]

    def issues(self, org_id, **kw):
        self.requests += 1
        self.issue_calls.append((org_id, kw))
        if self.issues_error:
            raise self.issues_error
        found = self.issues_by_org.get(org_id, [])
        return iter(found[:kw["max_items"]] if kw.get("max_items") else found)

    def probe(self, org_id=None):
        self.requests += 5
        return [ProbeResult("self", "/self", 200, "1 item(s)"),
                ProbeResult("issues", "/orgs/o1/issues", 403, "Forbidden")]


ESTATE = dict(
    issues_by_org={
        "o1": [issue("i1", "o1", "p1", [(JACKSON, "2.9.8")], problems=("SNYK-J-1",)),
               issue("i2", "o1", "p2", [(JACKSON, "2.9.8")], problems=("SNYK-J-1",)),
               issue("i3", "o1", "p2", [("express", "4.17.1")], problems=("SNYK-JS-2",),
                     title="=HYPERLINK(\"http://example.com\")")],
        "o2": [issue("i4", "o2", "p3", [(JACKSON, "2.10.0")], problems=("SNYK-J-1",))],
    },
    projects_by_org={
        "o1": [project("p1", "o1", "acme/payments"), project("p2", "o1", "acme/ledger")],
        "o2": [project("p3", "o2", "acme/web", counts={"critical": 1, "high": 0,
                                                       "medium": 2, "low": 3})],
    },
)


@pytest.fixture
def fake(monkeypatch):
    client = FakeClient(**ESTATE)
    monkeypatch.setattr(snyk_cli, "_client", lambda: client)
    return client


def run(*args):
    return CliRunner().invoke(snyk_cli.snyk, list(args))


def csv_rows(text):
    return list(csv.DictReader(io.StringIO(text)))


class TestIssues:
    def test_package_filter_to_stdout(self, fake):
        result = run("issues", "-package", "jackson-databind", "-stdout")
        assert result.exit_code == 0, result.output
        rows = csv_rows(result.stdout)
        assert rows, result.stdout
        assert list(rows[0]) == list(queries.ISSUE_FIELDS)
        assert {r["issue_id"] for r in rows} == {"i1", "i2", "i4"}
        assert {r["project"] for r in rows} == {"acme/payments", "acme/ledger", "acme/web"}
        # Progress goes to stderr, so stdout stays a clean CSV.
        assert "API request" in result.stderr
        assert "API request" not in result.stdout

    def test_project_names_fetched_by_id_only(self, fake):
        run("issues", "-package", "express", "-stdout")
        assert ("o1", ["p2"], False) in fake.project_calls
        # o2 has no express rows, so no project lookup there.
        assert not any(call[0] == "o2" for call in fake.project_calls)

    def test_filters_reach_the_client(self, fake):
        result = run("issues", "-org", "acme-web", "-severity", "critical",
                     "-severity", "high", "-status", "all", "-exclude-ignored",
                     "-max-issues", "50")
        assert result.exit_code == 0, result.output
        assert len(fake.issue_calls) == 1
        org_id, kw = fake.issue_calls[0]
        assert org_id == "o2"
        assert kw["statuses"] == ("open", "resolved")
        assert kw["severities"] == ("critical", "high")
        assert kw["ignored"] is False
        assert kw["max_items"] == 50

    def test_unknown_org(self, fake):
        result = run("issues", "-org", "nope")
        assert result.exit_code == 2
        assert "kospex snyk orgs" in result.output

    def test_csv_is_spreadsheet_safe_by_default(self, fake):
        rows = csv_rows(run("issues", "-package", "express", "-stdout").stdout)
        assert rows and rows[0]["title"].startswith("'=")

    def test_raw_keeps_values(self, fake):
        rows = csv_rows(run("issues", "-package", "express", "-stdout", "-raw").stdout)
        assert rows and rows[0]["title"].startswith("=")

    def test_table_by_default(self, fake):
        result = run("issues", "-package", "jackson-databind")
        assert result.exit_code == 0, result.output
        assert "SNYK-J-1" in result.stdout

    def test_403_is_a_clean_error(self, monkeypatch):
        client = FakeClient(issues_error=SnykNotAvailable(
            "/orgs/o1/issues: 403 Forbidden. Not available to this token or Snyk plan "
            "(most of the Snyk API needs an Enterprise plan)", 403, "/orgs/o1/issues"))
        monkeypatch.setattr(snyk_cli, "_client", lambda: client)
        result = run("issues")
        assert result.exit_code == 1
        assert "Enterprise" in result.output
        assert "Traceback" not in result.output


class TestVulns:
    def test_counts_projects_per_vuln(self, fake):
        result = run("vulns", "-stdout")
        assert result.exit_code == 0, result.output
        rows = csv_rows(result.stdout)
        assert rows
        assert list(rows[0]) == list(queries.VULN_FIELDS)
        top = rows[0]
        assert (top["vuln_id"], top["projects"], top["orgs"]) == ("SNYK-J-1", "3", "2")
        assert top["versions"] == "2.10.0;2.9.8"

    def test_out_file_and_overwrite(self, fake, tmp_path):
        out = tmp_path / "vulns.csv"
        assert run("vulns", "-out", str(out)).exit_code == 0
        assert csv_rows(out.read_text())
        again = run("vulns", "-out", str(out))
        assert again.exit_code == 2 and "-overwrite" in again.output
        assert run("vulns", "-out", str(out), "-overwrite").exit_code == 0

    def test_out_and_stdout_conflict(self, fake):
        result = run("vulns", "-out", "x.csv", "-stdout")
        assert result.exit_code == 2


class TestOtherCommands:
    def test_projects_with_counts(self, fake):
        result = run("projects", "-org", "o2", "-stdout")
        assert result.exit_code == 0, result.output
        rows = csv_rows(result.stdout)
        assert [r["project"] for r in rows] == ["acme/web"]
        assert rows[0]["critical"] == "1"
        assert fake.project_calls == [("o2", None, True)]

    def test_orgs(self, fake):
        result = run("orgs")
        assert result.exit_code == 0, result.output
        assert "acme-payments" in result.stdout and "acme-web" in result.stdout

    def test_probe_explains_403(self, fake):
        result = run("probe")
        assert result.exit_code == 0, result.output
        assert "403" in result.stdout
        assert "Enterprise" in result.stdout

    def test_missing_token_is_a_clean_error(self, monkeypatch):
        monkeypatch.delenv("SNYK_TOKEN", raising=False)
        result = run("probe")
        assert result.exit_code == 1
        assert "SNYK_TOKEN" in result.output


def test_registered_on_kospex_cli():
    import kospex_cli
    assert kospex_cli.cli.commands.get("snyk") is snyk_cli.snyk
