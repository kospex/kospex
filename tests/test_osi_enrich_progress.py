"""Enrichment must say what it is doing — #224.

A repository with many packages produced no output at all while being enriched, so a
working run was indistinguishable from a hung one. On a real run
`github.com~chartjs~chart.js` went 12+ minutes with the last log line 12 minutes
old; establishing it was alive rather than deadlocked needed a stack sample.

The cause is that `log.info` is only reached inside the *unresolved* branches of
`depsdev_record`, so a **successful** lookup logs nothing. The quiet runs are the
healthy ones, and the long quiet runs are exactly the ones needing progress.

Two requirements, and the tension between them is the whole design:

* Say the size of the work **before** starting, so the duration is explicable from
  the first line.
* Report movement during, but **not one line per package** — 1101 packages must not
  become 1101 log lines.
"""
import pytest

from krunner import enrich_dependency_records


class _Kdeps:
    """Minimal stand-in: records lookups, never touches the network."""

    def __init__(self):
        self.lookups = 0

    def clean_version_spec(self, version, package_type):
        return version

    def depsdev_record(self, package_type, name, version):
        self.lookups += 1
        return {"package_name": name, "package_version": version,
                "package_type": package_type, "versions_behind": 0,
                "advisories": 0, "resolution": "resolved",
                "published_at": "2026-01-01T00:00:00Z"}


def _records(n):
    return [{"package_name": f"p{i}", "package_version": "1.0.0",
             "package_type": "npm", "requirements_type": "direct"} for i in range(n)]


def test_the_size_of_the_work_is_announced_before_it_starts():
    """The line that would have explained the 12-minute repository."""
    said = []

    enrich_dependency_records(_records(312), _Kdeps(), progress=said.append)

    assert said, "enrichment announced nothing"
    first = said[0]
    assert "312" in first, f"the first line must state the work ahead: {first!r}"


def test_progress_is_reported_without_one_line_per_package():
    """1101 packages must not become 1101 log lines."""
    said = []

    enrich_dependency_records(_records(1101), _Kdeps(), progress=said.append)

    assert 1 < len(said) < 60, f"expected a handful of lines, got {len(said)}"


def test_a_small_repo_does_not_spam():
    """Below the reporting interval, the opening line is enough."""
    said = []

    enrich_dependency_records(_records(3), _Kdeps(), progress=said.append)

    assert len(said) <= 2, said


def test_progress_is_optional():
    """Callers that pass nothing must behave exactly as before."""
    kdeps = _Kdeps()

    out = enrich_dependency_records(_records(5), kdeps)

    assert len(out) == 5
    assert kdeps.lookups == 5


def test_every_record_is_still_enriched():
    """Progress reporting must not change what enrichment produces."""
    kdeps = _Kdeps()

    out = enrich_dependency_records(_records(25), kdeps, progress=lambda _: None)

    assert kdeps.lookups == 25
    for r in out:
        assert r["resolution"] == "resolved"
        assert r["versions_behind"] == 0
        assert r["package_use"] == "direct"


def test_nothing_is_announced_for_an_empty_batch():
    said = []

    enrich_dependency_records([], _Kdeps(), progress=said.append)

    assert said == []


def test_the_batch_runs_when_the_progress_callback_raises():
    """Reporting is bookkeeping; it must not cost the enrichment."""
    kdeps = _Kdeps()

    def boom(_):
        raise RuntimeError("logging blew up")

    out = enrich_dependency_records(_records(10), kdeps, progress=boom)

    assert kdeps.lookups == 10
    assert len(out) == 10
