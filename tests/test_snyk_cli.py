"""`kospex snyk` commands, driven through Click with a fake client."""
import csv
import io
import json
import os
import stat

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


class TestIssuesJson:
    def read_jsonl(self, path):
        return [json.loads(line) for line in path.read_text().splitlines()]

    def test_saves_every_issue_read_before_package_filter(self, fake, tmp_path):
        out = tmp_path / "raw.jsonl"
        result = run("issues", "-package", "express", "-json", str(out), "-stdout")
        assert result.exit_code == 0, result.output
        saved = self.read_jsonl(out)
        # All four issues read, though only i3 matches -package express.
        assert [i["id"] for i in saved] == ["i1", "i2", "i3", "i4"]
        assert saved == ESTATE["issues_by_org"]["o1"] + ESTATE["issues_by_org"]["o2"]
        assert {r["issue_id"] for r in csv_rows(result.stdout)} == {"i3"}
        assert "raw.jsonl" in result.stderr

    def test_file_is_owner_only(self, fake, tmp_path):
        out = tmp_path / "raw.jsonl"
        assert run("issues", "-json", str(out)).exit_code == 0
        assert stat.S_IMODE(os.stat(out).st_mode) == 0o600

    def test_refuses_existing_file_unless_overwrite(self, fake, tmp_path):
        out = tmp_path / "raw.jsonl"
        out.write_text("keep\n")
        result = run("issues", "-json", str(out))
        assert result.exit_code == 2 and "-overwrite" in result.output
        assert out.read_text() == "keep\n"
        assert fake.requests == 0
        assert run("issues", "-json", str(out), "-overwrite").exit_code == 0
        assert len(self.read_jsonl(out)) == 4

    def test_overwrite_tightens_an_existing_files_mode(self, fake, tmp_path):
        """CWE-732: os.open's mode applies only on create."""
        out = tmp_path / "raw.jsonl"
        out.write_text("old\n")
        os.chmod(out, 0o644)
        assert run("issues", "-json", str(out), "-overwrite").exit_code == 0
        assert stat.S_IMODE(os.stat(out).st_mode) == 0o600

    @pytest.mark.parametrize("overwrite", [False, True])
    def test_refuses_a_symlink(self, fake, tmp_path, overwrite):
        """CWE-59: a link at the path must not redirect the write."""
        target = tmp_path / "elsewhere"
        target.write_text("keep\n")
        link = tmp_path / "raw.jsonl"
        link.symlink_to(target)
        args = ["issues", "-json", str(link)] + (["-overwrite"] if overwrite else [])
        result = run(*args)
        assert result.exit_code == 2, result.output
        assert target.read_text() == "keep\n"

    def test_json_and_out_must_differ(self, fake, tmp_path):
        out = tmp_path / "same"
        result = run("issues", "-json", str(out), "-out", str(out))
        assert result.exit_code == 2
        assert not out.exists()

    def test_keeps_what_was_read_before_an_error(self, monkeypatch, tmp_path):
        client = FakeClient(**ESTATE)

        def issues(org_id, **kw):
            client.requests += 1
            if org_id == "o2":
                raise SnykNotAvailable("/orgs/o2/issues: 403 Forbidden", 403,
                                       "/orgs/o2/issues")
            yield from ESTATE["issues_by_org"][org_id]

        client.issues = issues
        monkeypatch.setattr(snyk_cli, "_client", lambda: client)
        out = tmp_path / "raw.jsonl"
        result = run("issues", "-json", str(out))
        assert result.exit_code == 1
        assert [i["id"] for i in self.read_jsonl(out)] == ["i1", "i2", "i3"]


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
