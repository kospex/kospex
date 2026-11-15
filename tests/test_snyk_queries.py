"""kospex.integrations.snyk.queries over spec-shaped fixtures."""
from kospex.integrations.snyk import queries
from snyk_fixtures import JACKSON, issue, org, project

ORGS = queries.index_by_id([org("o1", "acme-payments"), org("o2", "acme-web")])
PROJECTS = queries.index_by_id([project("p1", "o1", "acme/payments:pom.xml"),
                                project("p2", "o1", "acme/ledger:pom.xml"),
                                project("p3", "o2", "acme/web:package.json")])


class TestMatchesPackage:
    def test_exact_case_insensitive(self):
        assert queries.matches_package("Express", "express")

    def test_artifact_id_matches_maven_coordinate(self):
        assert queries.matches_package(JACKSON, "jackson-databind")

    def test_full_maven_coordinate(self):
        assert queries.matches_package(JACKSON, JACKSON.upper())

    def test_partial_name_does_not_match(self):
        assert not queries.matches_package(JACKSON, "databind")
        assert not queries.matches_package("express-session", "express")

    def test_coordinate_with_colon_needs_exact(self):
        assert not queries.matches_package(JACKSON, "other.group:jackson-databind")

    def test_npm_scoped(self):
        assert queries.matches_package("@acme/util", "@acme/util")
        assert not queries.matches_package("@acme/util", "util")

    def test_blank(self):
        assert not queries.matches_package("", "express")
        assert not queries.matches_package("express", "")


class TestIssueRows:
    def test_row_fields_from_spec_shape(self):
        rows = queries.issue_rows(
            [issue("i1", "o1", "p1", [(JACKSON, "2.9.8")],
                   problems=("CVE-2019-12384", "SNYK-JAVA-COMFASTERXMLJACKSONCORE-450207"),
                   severity="critical", reachability="function")],
            PROJECTS, ORGS)
        assert len(rows) == 1
        r = rows[0]
        assert set(r) == set(queries.ISSUE_FIELDS)
        assert r["org"] == "acme-payments"
        assert r["project"] == "acme/payments:pom.xml"
        assert (r["package"], r["version"]) == (JACKSON, "2.9.8")
        # The Snyk id is preferred over the CVE, whatever order problems[] is in.
        assert r["vuln_id"] == "SNYK-JAVA-COMFASTERXMLJACKSONCORE-450207"
        assert r["cves"] == "CVE-2019-12384"
        assert r["severity"] == "critical"
        assert r["reachability"] == "function"
        assert r["upgradeable"] is True and r["fixable"] is True

    def test_cve_used_when_no_snyk_id(self):
        rows = queries.issue_rows([issue("i1", "o1", "p1", [("x", "1")],
                                         problems=("CVE-2024-1",))])
        assert rows and rows[0]["vuln_id"] == "CVE-2024-1"

    def test_unknown_project_and_org_fall_back_to_ids(self):
        rows = queries.issue_rows([issue("i1", "o9", "p9", [("x", "1")])])
        assert rows and (rows[0]["org"], rows[0]["project"]) == ("o9", "p9")

    def test_one_row_per_distinct_package_version(self):
        rows = queries.issue_rows([issue("i1", "o1", "p1",
                                         [(JACKSON, "2.9.8"), (JACKSON, "2.9.8"),
                                          (JACKSON, "2.9.10")])])
        assert [r["version"] for r in rows] == ["2.9.8", "2.9.10"]

    def test_package_filter(self):
        issues = [issue("i1", "o1", "p1", [(JACKSON, "2.9.8")]),
                  issue("i2", "o1", "p1", [("express", "4.17.1")])]
        rows = queries.issue_rows(issues, package="jackson-databind")
        assert [r["issue_id"] for r in rows] == ["i1"]

    def test_issue_without_packages_kept_unless_filtering(self):
        bare = issue("i1", "o1", "p1", [])
        rows = queries.issue_rows([bare])
        assert len(rows) == 1 and rows[0]["package"] == ""
        assert queries.issue_rows([bare], package="express") == []

    def test_non_project_scan_item_has_no_project(self):
        env = issue("i1", "o1", "e1", [("x", "1")])
        env["relationships"]["scan_item"]["data"]["type"] = "environment"
        rows = queries.issue_rows([env])
        assert rows and rows[0]["project_id"] == ""


class TestVulnSummary:
    def _rows(self):
        return queries.issue_rows([
            # Same vuln in three projects across two orgs; p1 has it on two versions.
            issue("i1", "o1", "p1", [(JACKSON, "2.9.8"), (JACKSON, "2.9.10")],
                  problems=("SNYK-J-1", "CVE-1"), severity="high"),
            issue("i2", "o1", "p2", [(JACKSON, "2.9.8")], problems=("SNYK-J-1", "CVE-1"),
                  severity="critical", upgradeable=False),
            issue("i3", "o2", "p3", [(JACKSON, "2.10.0")], problems=("SNYK-J-1",)),
            # A different vuln in one project.
            issue("i4", "o2", "p3", [("express", "4.17.1")], problems=("SNYK-JS-2",),
                  severity="critical"),
        ], PROJECTS, ORGS)

    def test_counts_distinct_projects_and_orgs(self):
        summary = queries.vuln_summary(self._rows())
        assert [s["vuln_id"] for s in summary] == ["SNYK-J-1", "SNYK-JS-2"]
        top = summary[0]
        assert set(top) == set(queries.VULN_FIELDS)
        assert top["projects"] == 3
        assert top["orgs"] == 2
        assert top["issues"] == 3
        assert top["versions"] == "2.10.0;2.9.10;2.9.8"
        assert top["cves"] == "CVE-1"
        assert top["upgradeable_projects"] == 2

    def test_severity_is_the_highest_seen(self):
        top = queries.vuln_summary(self._rows())[0]
        assert top["severity"] == "critical"

    def test_ties_on_projects_sort_by_severity(self):
        rows = queries.issue_rows([
            issue("i1", "o1", "p1", [("a", "1")], problems=("SNYK-A",), severity="low"),
            issue("i2", "o1", "p1", [("b", "1")], problems=("SNYK-B",), severity="critical"),
        ])
        assert [s["vuln_id"] for s in queries.vuln_summary(rows)] == ["SNYK-B", "SNYK-A"]

    def test_empty(self):
        assert queries.vuln_summary([]) == []


class TestProjectRows:
    def test_with_counts(self):
        rows = queries.project_rows(
            [project("p1", "o1", "acme/payments", counts={"critical": 1, "high": 2,
                                                          "medium": 3, "low": 4})], ORGS)
        assert len(rows) == 1
        r = rows[0]
        assert set(r) == set(queries.PROJECT_FIELDS)
        assert (r["org"], r["critical"], r["high"], r["medium"], r["low"]) == \
            ("acme-payments", 1, 2, 3, 4)

    def test_without_counts_is_blank_not_zero(self):
        rows = queries.project_rows([project("p1", "o1", "acme/payments")], ORGS)
        assert rows and rows[0]["critical"] == ""
