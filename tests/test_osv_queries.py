"""Pure OSV record handling. Real fixtures: tests/data/osv/ (captured by
tests/manual/osv_capture.py). Expected values were read from the fixtures, not computed
by the code under test. Records for cases real data does not show (withdrawn, no fix,
a GIT range) are built by hand and say so."""
import copy
import json
from pathlib import Path

import pytest

from kospex.extractors.registry import package_types
from kospex.integrations.osv import queries as q

DATA = Path(__file__).parent / "data" / "osv"


def load(name):
    with open(DATA / f"{name}.json") as handle:
        return json.load(handle)["response"]["vulns"]


def by_id(records, osv_id):
    return next(r for r in records if r["id"] == osv_id)


@pytest.fixture(scope="module")
def requests_records():
    return load("pypi_requests_2.25.0")


@pytest.fixture(scope="module")
def log4j_records():
    return load("maven_log4j_core_2.14.1")


@pytest.fixture(scope="module")
def lodash_records():
    return load("npm_lodash_4.17.15")


def record(**fields):
    """A hand-built minimal record."""
    base = {"id": "TEST-1", "affected": [], "aliases": [], "summary": "s"}
    base.update(fields)
    return base


def affected(name="pkg", ecosystem="npm", events=(), versions=None, range_type="ECOSYSTEM"):
    entry = {"package": {"name": name, "ecosystem": ecosystem},
             "ranges": [{"type": range_type, "events": list(events)}]}
    if versions is not None:
        entry["versions"] = versions
    return entry


class TestEcosystem:
    def test_every_package_type_is_mapped_or_deliberately_not(self):
        deliberately_unmapped = set()
        missing = [t for t in package_types()
                   if q.ecosystem_for(t) is None and t not in deliberately_unmapped]
        assert not missing, f"decide what OSV calls: {missing}"

    @pytest.mark.parametrize("package_type,expected", [
        ("pypi", "PyPI"), ("npm", "npm"), ("maven", "Maven"), ("go", "Go"),
        ("cargo", "crates.io"), ("nuget", "NuGet"), ("rubygems", "RubyGems"),
        ("PyPI", "PyPI"),
    ])
    def test_names(self, package_type, expected):
        assert q.ecosystem_for(package_type) == expected

    @pytest.mark.parametrize("value", ["nope", "", None])
    def test_unknown_is_none(self, value):
        assert q.ecosystem_for(value) is None


class TestAffectedFor:
    def test_other_ecosystems_and_packages_are_left_out(self, lodash_records):
        # GHSA-xxjr-mmjv-4gpg names lodash, lodash-amd, lodash-es, ... and the
        # RubyGems lodash-rails.
        found = q.affected_for(by_id(lodash_records, "GHSA-xxjr-mmjv-4gpg"), "npm", "lodash")
        assert found
        assert {(e["package"]["ecosystem"], e["package"]["name"]) for e in found} == {
            ("npm", "lodash")}

    def test_an_ecosystem_mismatch_finds_nothing(self, lodash_records):
        assert q.affected_for(by_id(lodash_records, "GHSA-xxjr-mmjv-4gpg"),
                              "RubyGems", "lodash") == []

    @pytest.mark.parametrize("asked", ["Typing_Extensions", "typing-extensions", "typing.extensions"])
    def test_pypi_names_compare_as_pep_503_does(self, asked):
        rec = record(affected=[affected("typing-extensions", "PyPI")])
        assert len(q.affected_for(rec, "PyPI", asked)) == 1

    def test_other_ecosystems_only_ignore_case(self):
        rec = record(affected=[affected("my_pkg", "npm")])
        assert q.affected_for(rec, "npm", "MY_PKG")
        assert not q.affected_for(rec, "npm", "my-pkg")


class TestFixedVersions:
    def test_a_simple_range(self, requests_records):
        got = q.fixed_versions(by_id(requests_records, "GHSA-9hjg-9r4m-mvj7"),
                               "PyPI", "requests", "2.25.0")
        assert got == {"fixed": ["2.32.4"], "last_affected": [], "matched": True}

    def test_the_release_line_is_picked_by_the_versions_list(self, log4j_records):
        # GHSA-jfh8 has 2.0-beta9..2.3.1, 2.4..2.12.2 and 2.13.0..2.15.0 for log4j-core.
        rec = by_id(log4j_records, "GHSA-jfh8-c2jp-5v3q")
        assert q.fixed_versions(rec, "Maven", "org.apache.logging.log4j:log4j-core",
                                "2.14.1") == {
            "fixed": ["2.15.0"], "last_affected": [], "matched": True}

    def test_without_a_match_every_fixed_version_is_returned_and_not_matched(self, log4j_records):
        rec = by_id(log4j_records, "GHSA-jfh8-c2jp-5v3q")
        got = q.fixed_versions(rec, "Maven", "org.apache.logging.log4j:log4j-core", "9.9.9")
        assert set(got["fixed"]) == {"2.15.0", "2.3.1", "2.12.2"}
        assert got["matched"] is False

    def test_another_package_in_the_record_is_not_used(self, log4j_records):
        # The same record fixes pax-logging-log4j2 in 1.9.2, 1.10.8, ... not ours.
        got = q.fixed_versions(by_id(log4j_records, "GHSA-jfh8-c2jp-5v3q"),
                               "Maven", "org.apache.logging.log4j:log4j-core", "2.14.1")
        assert not {"1.9.2", "1.10.8", "1.11.10", "2.0.11"} & set(got["fixed"])

    def test_last_affected_only_when_there_is_no_fix(self):
        rec = record(affected=[affected(events=[{"introduced": "0"}, {"last_affected": "1.4"}])])
        assert q.fixed_versions(rec, "npm", "pkg") == {
            "fixed": [], "last_affected": ["1.4"], "matched": False}

    def test_last_affected_is_ignored_when_the_range_has_a_fix(self):
        rec = record(affected=[affected(events=[{"introduced": "0"}, {"fixed": "2.0"},
                                                {"last_affected": "1.9"}])])
        assert q.fixed_versions(rec, "npm", "pkg")["last_affected"] == []

    def test_git_ranges_are_commits_not_versions(self):
        rec = record(affected=[affected(range_type="GIT", events=[
            {"introduced": "0"}, {"fixed": "3f2a9b1c0d4e5f60718293a4b5c6d7e8f9012345"}])])
        assert q.fixed_versions(rec, "npm", "pkg")["fixed"] == []

    def test_no_ranges_at_all(self):
        rec = record(affected=[{"package": {"name": "pkg", "ecosystem": "npm"}}])
        assert q.fixed_versions(rec, "npm", "pkg") == {
            "fixed": [], "last_affected": [], "matched": False}

    def test_repeated_fixed_versions_are_listed_once(self):
        rec = record(affected=[affected(events=[{"fixed": "1.2"}]),
                               affected(events=[{"fixed": "1.2"}])])
        assert q.fixed_versions(rec, "npm", "pkg")["fixed"] == ["1.2"]


class TestSeverity:
    def test_a_v3_vector_is_scored_here(self, requests_records):
        # CVSS:3.1/AV:N/AC:H/PR:N/UI:R/S:U/C:H/I:N/A:N is 5.3 (published by NVD/GitHub).
        got = q.severity(by_id(requests_records, "GHSA-9hjg-9r4m-mvj7"))
        assert got == q.Severity("MEDIUM", 5.3, "CVSS 3.1")

    def test_critical_ten(self, log4j_records):
        got = q.severity(by_id(log4j_records, "GHSA-jfh8-c2jp-5v3q"))
        assert (got.label, got.score) == ("CRITICAL", 10.0)

    def test_a_v4_only_record_uses_the_databases_label(self, log4j_records):
        rec = by_id(log4j_records, "GHSA-3pxv-7cmr-fjr4")
        assert [s["type"] for s in rec["severity"]] == ["CVSS_V4"]
        assert q.severity(rec) == q.Severity("MEDIUM", None, "GitHub")

    def test_a_v4_only_record_with_no_label_is_unscored_not_missing(self, log4j_records):
        rec = copy.deepcopy(by_id(log4j_records, "GHSA-3pxv-7cmr-fjr4"))
        rec["database_specific"].pop("severity")
        assert q.severity(rec) == q.Severity(q.UNSCORED, None, "CVSS 4")

    def test_no_severity_at_all_is_none(self):
        assert q.severity(record()) is None

    def test_cvss_v2_is_ignored(self):
        rec = record(severity=[{"type": "CVSS_V2", "score": "AV:N/AC:L/Au:N/C:P/I:P/A:P"}])
        assert q.severity(rec) is None

    def test_a_malformed_v3_vector_is_unscored_not_zero(self):
        rec = record(severity=[{"type": "CVSS_V3", "score": "CVSS:3.1/AV:N"}])
        assert q.severity(rec) == q.Severity(q.UNSCORED, None, "CVSS 3")

    def test_the_worst_of_several_wins(self):
        # v3 says HIGH (AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:N/A:N = 7.5), the label says CRITICAL.
        rec = record(id="GHSA-x",
                     severity=[{"type": "CVSS_V3",
                                "score": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:N/A:N"}],
                     database_specific={"severity": "CRITICAL"})
        assert q.severity(rec) == q.Severity("CRITICAL", None, "GitHub")

    def test_on_a_tie_the_one_with_a_number_wins(self):
        rec = record(id="GHSA-x",
                     severity=[{"type": "CVSS_V3",
                                "score": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:N/A:N"}],
                     database_specific={"severity": "HIGH"})
        assert q.severity(rec) == q.Severity("HIGH", 7.5, "CVSS 3.1")

    def test_moderate_is_medium(self):
        rec = record(id="GHSA-x", database_specific={"severity": "MODERATE"})
        assert q.severity(rec).label == "MEDIUM"

    def test_a_label_on_a_non_github_record_is_the_database(self):
        rec = record(id="RUSTSEC-1", database_specific={"severity": "HIGH"})
        assert q.severity(rec).source == "database"

    def test_a_label_we_do_not_know_is_ignored(self):
        assert q.severity(record(database_specific={"severity": "WHATEVER"})) is None


class TestAdvisoryRow:
    def test_the_fields_for_a_real_record(self, requests_records):
        row = q.advisory_row(by_id(requests_records, "GHSA-9hjg-9r4m-mvj7"),
                             "PyPI", "requests", "2.25.0")

        assert tuple(row) == q.ROW_FIELDS
        assert row["id"] == "GHSA-9hjg-9r4m-mvj7"
        assert row["aliases"] == ["CVE-2024-47081", "PYSEC-2026-1872"]
        assert row["cves"] == ["CVE-2024-47081"]
        assert row["fixed_in"] == ["2.32.4"]
        assert row["published"] == "2025-06-09"
        assert row["withdrawn"] == ""
        assert row["url"] == "https://osv.dev/vulnerability/GHSA-9hjg-9r4m-mvj7"

    def test_withdrawn_is_a_date(self):
        rec = record(withdrawn="2024-02-03T04:05:06Z")   # hand-built
        assert q.advisory_row(rec, "npm", "pkg")["withdrawn"] == "2024-02-03"


class TestAdvisoryRows:
    def test_one_row_per_vulnerability_not_per_record(self, requests_records):
        # 8 records: each of the 4 vulnerabilities is filed as a GHSA and a PYSEC record.
        assert len(requests_records) == 8
        rows = q.advisory_rows(requests_records, "PyPI", "requests", "2.25.0")

        assert len(rows) == 4
        assert {r["id"] for r in rows} == {
            "GHSA-9hjg-9r4m-mvj7", "GHSA-9wx4-h78v-vm56",
            "GHSA-gc5v-m9x4-r6x2", "GHSA-j8r2-6x86-q33q"}

    def test_a_merged_row_keeps_every_id(self, requests_records):
        rows = q.advisory_rows(requests_records, "PyPI", "requests", "2.25.0")
        row = next(r for r in rows if r["id"] == "GHSA-gc5v-m9x4-r6x2")
        assert set(row["aliases"]) == {"CVE-2026-25645", "PYSEC-2026-2275"}
        assert row["cves"] == ["CVE-2026-25645"]

    def test_a_merged_row_takes_the_worst_severity(self, requests_records):
        # The GHSA record scores 4.4, its PYSEC twin 5.5.
        rows = q.advisory_rows(requests_records, "PyPI", "requests", "2.25.0")
        row = next(r for r in rows if r["id"] == "GHSA-gc5v-m9x4-r6x2")
        assert (row["severity"], row["severity_score"]) == ("MEDIUM", 5.5)

    def test_unrelated_records_are_not_merged(self, log4j_records):
        rows = q.advisory_rows(log4j_records, "Maven",
                               "org.apache.logging.log4j:log4j-core", "2.14.1")
        assert len(rows) == 8

    def test_a_chain_of_aliases_merges_into_one(self):
        a = record(id="A", aliases=["B"])
        b = record(id="B", aliases=[])
        c = record(id="C", aliases=["B"])
        rows = q.advisory_rows([a, c, b], "npm", "pkg")
        assert len(rows) == 1
        assert set(rows[0]["aliases"]) == {"B", "C"}

    def test_withdrawn_only_when_every_record_is(self):
        both = [record(id="A", aliases=["B"], withdrawn="2024-01-01T00:00:00Z"),
                record(id="B", aliases=["A"], withdrawn="2024-02-01T00:00:00Z")]
        one = [record(id="A", aliases=["B"], withdrawn="2024-01-01T00:00:00Z"),
               record(id="B", aliases=["A"])]
        assert q.advisory_rows(both, "npm", "pkg")[0]["withdrawn"] == "2024-01-01"
        assert q.advisory_rows(one, "npm", "pkg")[0]["withdrawn"] == ""

    def test_no_records_no_rows(self):
        assert q.advisory_rows([], "npm", "pkg") == []


class TestCompare:
    @staticmethod
    def rows(*records):
        return q.advisory_rows(list(records), "npm", "pkg")

    @staticmethod
    def deps(advisory_id, also=(), cves=()):
        return {"id": advisory_id, "also": list(also), "cves": list(cves)}

    def test_matches_on_any_shared_id(self):
        osv_rows = self.rows(record(id="GHSA-a", aliases=["CVE-1"]))
        both, only_osv, only_deps = q.compare(osv_rows, [self.deps("PYSEC-1", cves=["CVE-1"])])
        assert (len(both), only_osv, only_deps) == (1, [], [])

    def test_names_what_only_one_side_has(self):
        osv_rows = self.rows(record(id="GHSA-a", aliases=["CVE-1"]),
                             record(id="GHSA-only-osv", aliases=["CVE-2"]))
        both, only_osv, only_deps = q.compare(
            osv_rows, [self.deps("GHSA-a", cves=["CVE-1"]), self.deps("GHSA-only-deps")])

        assert [r["id"] for r in both] == ["GHSA-a"]
        assert [r["id"] for r in only_osv] == ["GHSA-only-osv"]
        assert [a["id"] for a in only_deps] == ["GHSA-only-deps"]

    def test_withdrawn_osv_records_are_not_a_disagreement(self):
        osv_rows = self.rows(record(id="GHSA-w", withdrawn="2024-01-01T00:00:00Z"))
        assert q.compare(osv_rows, []) == ([], [], [])

    def test_deps_dev_having_nothing_is_not_an_error(self):
        osv_rows = self.rows(record(id="GHSA-a"))
        both, only_osv, only_deps = q.compare(osv_rows, None)
        assert (both, len(only_osv), only_deps) == ([], 1, [])

    def test_the_real_requests_advisories_agree_with_deps_dev(self, requests_records):
        osv_rows = q.advisory_rows(requests_records, "PyPI", "requests", "2.25.0")
        deps = [self.deps("GHSA-9hjg-9r4m-mvj7", also=["PYSEC-2026-1872"], cves=["CVE-2024-47081"]),
                self.deps("GHSA-9wx4-h78v-vm56", also=["PYSEC-2026-1873"], cves=["CVE-2024-35195"]),
                self.deps("GHSA-gc5v-m9x4-r6x2", also=["PYSEC-2026-2275"], cves=["CVE-2026-25645"]),
                self.deps("GHSA-j8r2-6x86-q33q", also=["PYSEC-2023-74"], cves=["CVE-2023-32681"])]
        both, only_osv, only_deps = q.compare(osv_rows, deps)
        assert (len(both), only_osv, only_deps) == (4, [], [])
