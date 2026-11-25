"""`Enricher(next_clean=True)`: the next release above `current_version` with no known
advisories, from OSV. deps.dev (a stub) and OSV (a fake) are both in-process."""
import datetime

import pytest

from kospex.dependabot_enrich import SCA_FIELDS, Enricher
from kospex.integrations.osv.client import OsvError
from kospex_dependencies import KospexDependencies

NOW = datetime.datetime(2026, 11, 25, tzinfo=datetime.timezone.utc)
NEXT_CLEAN_KEYS = ("sca_next_clean_version", "sca_next_clean_age_days",
                   "sca_next_clean_affected_between", "sca_next_clean_status")


def rel(version, published="2026-01-01T00:00:00Z", pre=False, deprecated=False):
    return {"version": version, "published_at": published, "is_default": False,
            "is_prerelease": pre, "is_deprecated": deprecated, "deprecated_reason": ""}


class _Query:
    last_url_error = None
    last_url_status = 200


class Deps:
    """Just the parts of KospexDependencies the enricher calls."""
    _age_days = staticmethod(KospexDependencies._age_days)
    _version_order_key = staticmethod(KospexDependencies._version_order_key)

    def __init__(self, releases_by_package=None, permitted=(True, "")):
        self.releases_by_package = releases_by_package or {}
        self.permitted = permitted
        self.kospex_query = _Query()
        self.release_calls = []

    def lookup_permitted(self, package_type, name):
        return self.permitted

    def release_list(self, package_type, name, cache=None):
        self.release_calls.append((package_type, name))
        return self.releases_by_package.get(name)

    def behind_fields(self, *a, **k):
        return {}

    def version_detail(self, *a, **k):
        return None, 404

    def releases_between(self, *a, **k):
        return None


class FakeOsv:
    """`batch`: {package name: {osv version: [ids]}}. `fail`: package names that raise."""

    def __init__(self, batch=None, fail=(), vulns=None):
        self.batch = batch or {}
        self.fail = set(fail)
        self.vulns = vulns or {}
        self.batch_calls, self.vuln_calls = [], []

    def querybatch(self, ecosystem, name, versions):
        self.batch_calls.append((ecosystem, name, list(versions)))
        if name in self.fail:
            raise OsvError("POST /querybatch answered HTTP 503")
        known = self.batch.get(name, {})
        return {v: known.get(v, []) for v in versions}

    def vuln(self, osv_id):
        self.vuln_calls.append(osv_id)
        found = self.vulns.get(osv_id, {"id": osv_id})
        if isinstance(found, Exception):
            raise found
        return found


def alert(name="pkg", current="1.0.0", patched="1.1.0", ecosystem="pip"):
    return {"package_ecosystem": ecosystem, "package_name": name,
            "current_version": current, "patched_version": patched}


def enricher(releases, osv, **kw):
    return Enricher(Deps(releases), now=NOW, next_clean=True, osv_client=osv, **kw)


LINE = [rel(f"1.{i}.0", published=f"2026-0{i + 1}-01T00:00:00Z") for i in range(5)]
PKG = {"pkg": LINE}


class TestFields:
    def test_off_by_default_and_the_output_is_unchanged(self):
        osv = FakeOsv()
        row = Enricher(Deps(PKG), now=NOW, osv_client=osv).enrich(alert())
        assert not any(key in row for key in NEXT_CLEAN_KEYS)
        assert osv.batch_calls == []

    def test_on_adds_exactly_the_four_keys_after_the_sca_fields(self):
        row = enricher(PKG, FakeOsv()).enrich(alert())
        assert list(row)[-4:] == list(NEXT_CLEAN_KEYS)
        assert set(SCA_FIELDS) <= set(row)

    def test_found_with_what_it_skips(self):
        osv = FakeOsv({"pkg": {"1.0.0": ["A"], "1.1.0": ["A"], "1.2.0": ["B"]}})
        row = enricher(PKG, osv).enrich(alert(current="1.0.0"))
        assert row["sca_next_clean_status"] == "found"
        assert row["sca_next_clean_version"] == "1.3.0"
        assert row["sca_next_clean_affected_between"] == 2
        assert row["sca_next_clean_age_days"] == (NOW - datetime.datetime(
            2026, 4, 1, tzinfo=datetime.timezone.utc)).days

    def test_the_installed_version_is_already_clean(self):
        row = enricher(PKG, FakeOsv({"pkg": {"1.0.0": []}})).enrich(alert(current="1.0.0"))
        assert row["sca_next_clean_status"] == "start_clean"
        assert row["sca_next_clean_version"] is None
        assert row["sca_next_clean_affected_between"] == 0

    def test_nothing_later_is_clean(self):
        osv = FakeOsv({"pkg": {v["version"]: ["A"] for v in LINE}})
        row = enricher(PKG, osv).enrich(alert(current="1.2.0"))
        assert row["sca_next_clean_status"] == "none"
        assert row["sca_next_clean_version"] is None
        assert row["sca_next_clean_affected_between"] == 2     # 1.3.0 and 1.4.0

    def test_an_unreadable_release_is_never_offered_as_clean(self):
        osv = FakeOsv({"pkg": {"1.0.0": ["A"], "1.1.0": ["X"]}},
                      vulns={"X": OsvError("boom")})
        row = enricher(PKG, osv).enrich(alert(current="1.0.0"))
        assert row["sca_next_clean_version"] == "1.2.0"       # 1.1.0 skipped, not clean

    def test_pre_releases_and_deprecated_are_not_candidates(self):
        releases = {"pkg": [rel("1.0.0"), rel("1.1.0", pre=True),
                            rel("1.2.0", deprecated=True), rel("1.3.0")]}
        osv = FakeOsv({"pkg": {"1.0.0": ["A"]}})
        row = enricher(releases, osv).enrich(alert(current="1.0.0"))
        assert row["sca_next_clean_version"] == "1.3.0"


class TestNotAttempted:
    def test_no_current_version(self):
        osv = FakeOsv()
        row = enricher(PKG, osv).enrich(alert(current=None))
        assert row["sca_next_clean_status"] == "no_current_version"
        assert osv.batch_calls == []

    def test_a_pre_release_installed_version(self):
        osv = FakeOsv()
        row = enricher({"pkg": [rel("1.0.0rc1", pre=True), rel("1.1.0")]}, osv).enrich(
            alert(current="1.0.0rc1"))
        assert row["sca_next_clean_status"] == "current_is_prerelease"
        assert row["sca_next_clean_version"] is None

    def test_a_version_deps_dev_does_not_list(self):
        row = enricher(PKG, FakeOsv()).enrich(alert(current="9.9.9"))
        assert row["sca_next_clean_status"] == "not_in_release_list"

    def test_private_and_unsupported_rows_never_reach_osv(self):
        osv = FakeOsv()
        private = Enricher(Deps(PKG, permitted=(False, "private")), now=NOW,
                           next_clean=True, osv_client=osv).enrich(alert())
        unsupported = enricher(PKG, osv).enrich(alert(ecosystem="nosuch"))
        for row in (private, unsupported):
            assert row["sca_next_clean_status"] is None
        assert private["sca_status"] == "private_skipped"
        assert osv.batch_calls == []


class TestPerPackage:
    def test_many_alerts_on_one_package_cost_one_batch_and_one_fetch_per_record(self):
        osv = FakeOsv({"pkg": {"1.0.0": ["A"], "1.1.0": ["A"], "1.2.0": ["B"]}})
        e = enricher(PKG, osv)
        for current in ("1.0.0", "1.1.0", "1.2.0"):
            e.enrich(alert(current=current))
        assert len(osv.batch_calls) == 1
        assert sorted(osv.vuln_calls) == ["A", "B"]

    def test_go_versions_are_looked_up_as_deps_dev_spells_them(self):
        releases = {"github.com/a/b": [rel("v1.0.0"), rel("v1.1.0")]}
        osv = FakeOsv({"github.com/a/b": {"1.0.0": ["A"]}})
        row = enricher(releases, osv).enrich(
            alert(name="github.com/a/b", current="1.0.0", ecosystem="go"))
        assert osv.batch_calls[0][2] == ["1.0.0", "1.1.0"]     # sent without the v
        assert row["sca_next_clean_version"] == "v1.1.0"       # reported as deps.dev has it


class TestFailures:
    def test_an_osv_failure_sets_only_the_next_clean_status(self):
        with_osv = enricher(PKG, FakeOsv(fail={"pkg"})).enrich(alert())
        without = Enricher(Deps(PKG), now=NOW).enrich(alert())
        assert with_osv["sca_next_clean_status"] == "lookup_error"
        assert {k: v for k, v in with_osv.items() if k in without} == without

    def test_it_stops_asking_after_three_failures_in_a_row(self):
        names = [f"p{i}" for i in range(6)]
        osv = FakeOsv(fail=set(names))
        e = enricher({n: LINE for n in names}, osv)
        rows = [e.enrich(alert(name=n)) for n in names]
        assert len(osv.batch_calls) == 3
        assert e.osv_stopped is True
        assert all(r["sca_next_clean_status"] == "lookup_error" for r in rows)

    def test_a_success_resets_the_count(self):
        osv = FakeOsv(fail={"p0", "p1", "p3", "p4"})
        e = enricher({n: LINE for n in ("p0", "p1", "p2", "p3", "p4", "p5")}, osv)
        for n in ("p0", "p1", "p2", "p3", "p4", "p5"):
            e.enrich(alert(name=n))
        assert e.osv_stopped is False
        assert len(osv.batch_calls) == 6

    def test_status_counts_are_kept_for_the_summary(self):
        e = enricher(PKG, FakeOsv({"pkg": {"1.0.0": ["A"]}}))
        e.enrich(alert(current="1.0.0"))
        e.enrich(alert(current=None))
        assert dict(e.next_clean_counts) == {"found": 1, "no_current_version": 1}
