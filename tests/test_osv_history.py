"""Pure advisory-history logic: window, per-release rows, summary, next release without
known advisories. Plain dicts, no network. Records are hand-built minimal OSV records."""
import pytest

from kospex.integrations.osv import queries as q


def order(version):
    return tuple(int(p) for p in version.split("."))


def rel(version, published="2022-01-01T00:00:00Z", pre=False, deprecated=False,
        default=False):
    return {"version": version, "published_at": published, "is_default": default,
            "is_prerelease": pre, "is_deprecated": deprecated, "deprecated_reason": ""}


def rec(osv_id, severity=None, withdrawn=None):
    out = {"id": osv_id, "affected": [], "aliases": [], "summary": "s"}
    if severity:
        out["database_specific"] = {"severity": severity}
    if withdrawn:
        out["withdrawn"] = withdrawn
    return out


def row(version, count, pre=False, deprecated=False):
    return {"version": version, "published_at": "", "advisory_count": count,
            "advisory_ids": [] if count is None else [f"A{i}" for i in range(count)],
            "worst_severity": "", "is_current": False, "is_deprecated": deprecated,
            "is_prerelease": pre}


class TestOsvVersion:
    def test_strips_v_for_go_only(self):
        assert q.osv_version("Go", "v1.8.0") == "1.8.0"
        assert q.osv_version("npm", "v1.0.0") == "v1.0.0"
        assert q.osv_version("Go", "1.8.0") == "1.8.0"


class TestWindow:
    def test_goes_back_by_version_number_not_publish_date(self):
        releases = [rel("2.18.0", "2021-01-01T00:00:00Z"),
                    rel("2.17.1", "2022-01-01T00:00:00Z"),   # a backport, published later
                    rel("2.17.0", "2020-06-01T00:00:00Z")]
        got = q.select_window(releases, "2.18.0", 2, False, order)
        assert [r["version"] for r in got["rows"]] == ["2.18.0", "2.17.1"]
        assert got["start"]["version"] == "2.18.0"

    def test_skips_prereleases_and_counts_them(self):
        releases = [rel("3.0.0"), rel("3.0.0-beta", pre=True), rel("2.0.0"),
                    rel("2.0.0-rc1", pre=True)]
        # order() cannot parse "-beta", so give the prereleases numeric forms instead
        releases[1]["version"], releases[3]["version"] = "2.9.9", "1.9.9"
        got = q.select_window(releases, "3.0.0", 5, False, order)
        assert [r["version"] for r in got["rows"]] == ["3.0.0", "2.0.0"]
        assert got["skipped_prereleases"] == 2

    def test_keeps_a_prerelease_start(self):
        releases = [rel("3.0.0", pre=True, default=True), rel("2.0.0")]
        got = q.select_window(releases, "3.0.0", 5, False, order)
        assert [r["version"] for r in got["rows"]] == ["3.0.0", "2.0.0"]

    def test_include_pre(self):
        releases = [rel("3.0.0"), rel("2.9.9", pre=True), rel("2.0.0")]
        got = q.select_window(releases, "3.0.0", 5, True, order)
        assert [r["version"] for r in got["rows"]] == ["3.0.0", "2.9.9", "2.0.0"]
        assert got["skipped_prereleases"] == 0

    def test_unknown_start_raises_valueerror(self):
        with pytest.raises(ValueError, match="9.9.9"):
            q.select_window([rel("1.0.0")], "9.9.9", 5, False, order)


class TestRows:
    def build(self, ids, records, releases=None):
        releases = releases or [rel(v) for v in ids]
        return {r["version"]: r for r in q.release_rows(
            releases, ids, records, "npm", "pkg", current_version="1.0.0")}

    def test_drops_withdrawn_and_marks_unknown(self):
        ids = {"1.0.0": ["A"], "2.0.0": ["B"], "3.0.0": ["C"], "4.0.0": None}
        records = {"A": rec("A", withdrawn="2024-01-01T00:00:00Z"), "B": None,
                   "C": rec("C")}
        got = self.build(ids, records)
        assert (got["1.0.0"]["advisory_count"], got["1.0.0"]["advisory_ids"]) == (0, [])
        assert (got["2.0.0"]["advisory_count"], got["2.0.0"]["advisory_ids"]) == (None, None)
        assert (got["3.0.0"]["advisory_count"], got["3.0.0"]["advisory_ids"]) == (1, ["C"])
        assert (got["4.0.0"]["advisory_count"], got["4.0.0"]["advisory_ids"]) == (None, None)

    def test_one_failed_record_leaves_other_releases_alone(self):
        ids = {"1.0.0": ["A", "B"], "2.0.0": ["B"]}
        got = self.build(ids, {"A": None, "B": rec("B")})
        assert got["1.0.0"]["advisory_count"] is None
        assert got["2.0.0"]["advisory_count"] == 1

    def test_worst_severity_and_flags(self):
        ids = {"1.0.0": ["A", "B"], "2.0.0": []}
        got = self.build(ids, {"A": rec("A", "LOW"), "B": rec("B", "HIGH")},
                         [rel("1.0.0", deprecated=True), rel("2.0.0", pre=True)])
        assert got["1.0.0"]["worst_severity"] == "HIGH"
        assert got["1.0.0"]["is_current"] is True
        assert got["1.0.0"]["is_deprecated"] is True
        assert got["2.0.0"]["worst_severity"] == ""
        assert got["2.0.0"]["is_prerelease"] is True
        assert set(got["1.0.0"]) == {"version", "published_at", "advisory_count",
                                     "advisory_ids", "worst_severity", "is_current",
                                     "is_deprecated", "is_prerelease"}


class TestSummary:
    def test_percent_and_by_major(self):
        rows = ([row(f"3.0.{i}", 1 if i < 2 else 0) for i in range(4)]
                + [row("2.0.0", 3), row("2.0.1", 1)])
        got = q.summarise(rows, include_pre=False)
        assert got["releases"] == 6 and got["affected"] == 4 and got["unknown"] == 0
        assert got["percent"] == pytest.approx(4 / 6 * 100)
        assert got["by_major"] == [{"major": "2", "releases": 2, "affected": 2},
                                   {"major": "3", "releases": 4, "affected": 2}]

    def test_a_major_where_every_release_is_affected_is_100_percent(self):
        got = q.summarise([row("2.0.0", 1), row("2.1.0", 2)], include_pre=False)
        assert got["percent"] == 100.0
        assert got["by_major"] == [{"major": "2", "releases": 2, "affected": 2}]

    def test_no_stable_releases_is_not_a_division_by_zero(self):
        got = q.summarise([row("1.0.0", 0, pre=True)], include_pre=False)
        assert got["releases"] == 0 and got["percent"] is None and got["by_major"] == []

    def test_include_pre_counts_them(self):
        got = q.summarise([row("1.0.0", 0, pre=True), row("0.9.0", 1)], include_pre=True)
        assert got["releases"] == 2 and got["affected"] == 1

    def test_unknown_is_excluded_from_percent(self):
        got = q.summarise([row("1.0.0", None), row("1.1.0", 1), row("1.2.0", 0)], False)
        assert got["unknown"] == 1 and got["releases"] == 2 and got["percent"] == 50.0

    def test_majors_sort_numerically_and_strip_v(self):
        got = q.summarise([row("v10.0.0", 0), row("v9.0.0", 0)], False)
        assert [m["major"] for m in got["by_major"]] == ["9", "10"]


class TestNextClean:
    def test_found_skips_unknown_and_deprecated(self):
        rows = [row("1.0.0", 2), row("1.1.0", None), row("1.2.0", 0, deprecated=True),
                row("1.3.0", 0), row("1.4.0", 0)]
        got = q.next_clean(rows, "1.0.0", order)
        assert got["status"] == "found" and got["release"]["version"] == "1.3.0"
        assert got["skipped_unknown"] == ["1.1.0"]

    def test_none_when_every_later_release_is_affected(self):
        got = q.next_clean([row("2.0.0", 1), row("2.1.0", 3), row("2.2.0", 2)], "2.0.0", order)
        assert got == {"status": "none", "release": None, "skipped_unknown": []}

    def test_start_clean(self):
        got = q.next_clean([row("1.0.0", 0), row("1.1.0", 0)], "1.0.0", order)
        assert got["status"] == "start_clean" and got["release"]["version"] == "1.0.0"

    def test_ignores_older_releases_and_prereleases(self):
        rows = [row("0.9.0", 0), row("1.0.0", 1), row("1.1.0", 0, pre=True), row("1.2.0", 0)]
        got = q.next_clean(rows, "1.0.0", order)
        assert got["release"]["version"] == "1.2.0"
