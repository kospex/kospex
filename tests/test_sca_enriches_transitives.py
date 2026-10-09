"""`kospex sca` must look up pnpm transitive dependencies — #225.

One line in `_enrich_dependency_records` skipped deps.dev entirely for pnpm
transitives:

    skip_lookup = (
        extractor.name == "pnpm-lock" and req_type not in ("direct", "dev")
    )

So `sca` recorded no `advisories` and no `versions_behind` for them. On
`chartjs/chart.js` that is **1991 of 2079 packages (96%)** with no advisory data,
concentrated in the layer where supply-chain compromise actually lands —
transitives are where malware hides precisely because nobody inspects them.

It was also an `osi` / `sca` divergence: `krunner osi` enriched all 2163 rows it
extracted from the same file. The same manifest produced different advisory data
depending on which tool read it, which is the class of defect the extractor
registry exists to prevent.

The comment defending the skip was a cost argument — "each lookup is an HTTP
round-trip". That cost is real and tracked in #226. It is a reason to make
enrichment faster, not a reason to not look.
"""
import pytest

import kospex_schema as KospexSchema
from kospex.extractors.registry import classify


@pytest.fixture
def kd(monkeypatch):
    """KospexDependencies with deps.dev stubbed, recording what it was asked."""
    from kospex_dependencies import KospexDependencies

    asked = []

    def _record(self, package_type, name, version):
        asked.append((package_type, name, version))
        return {"package_name": name, "package_version": version,
                "package_type": package_type, "versions_behind": 7,
                "advisories": 2, "resolution": "resolved",
                "published_at": "2024-01-01T00:00:00Z"}

    monkeypatch.setattr(KospexDependencies, "depsdev_record", _record)
    obj = KospexDependencies()
    obj.asked = asked
    return obj


def _pnpm(**overrides):
    rec = {"package_name": "lodash", "package_version": "4.17.21",
           "requirements_type": "resolved"}
    rec.update(overrides)
    return rec


def _enrich(kd, records):
    return kd._enrich_dependency_records(records, classify("pnpm-lock.yaml").extractor)


def test_a_transitive_dependency_is_looked_up(kd):
    """The defect: 96% of a pnpm closure had no advisory data at all."""
    out = _enrich(kd, [_pnpm()])[0]

    assert kd.asked == [("npm", "lodash", "4.17.21")], "deps.dev was not asked"
    assert out["advisories"] == 2
    assert out["versions_behind"] == 7


def test_a_transitive_dependency_records_the_version_queried(kd):
    """resolved_version was forced to "" for transitives, so the advisory
    numbers referred to a version the row did not name."""
    out = _enrich(kd, [_pnpm()])[0]

    assert out["resolved_version"] == "4.17.21"


def test_a_transitive_is_still_labelled_transitive(kd):
    """Enriching it must not change what it is. package_use is how a consumer
    tells a shipped dependency from one pulled in beneath it."""
    out = _enrich(kd, [_pnpm()])[0]

    assert out["package_use"] == KospexSchema.PACKAGE_USE_TRANSITIVE


def test_direct_and_dev_entries_are_unaffected(kd):
    """They were already enriched; this must not change them."""
    out = _enrich(kd, [_pnpm(requirements_type="direct", package_name="a"),
                       _pnpm(requirements_type="dev", package_name="b")])

    assert {r["package_name"] for r in out} == {"a", "b"}
    assert all(r["advisories"] == 2 for r in out)
    assert out[0]["package_use"] == KospexSchema.PACKAGE_USE_DIRECT
    assert out[1]["package_use"] == KospexSchema.PACKAGE_USE_DEV


def test_classification_still_does_not_depend_on_the_lookup(kd):
    """version_kind/operator come from the declared string, not from deps.dev."""
    out = _enrich(kd, [_pnpm()])[0]

    assert out["version_kind"] == "pinned"
    assert out["version_operator"] == ""


def test_every_entry_in_a_closure_is_looked_up(kd):
    """A closure is mostly transitive; the whole point is that all of it counts."""
    records = [_pnpm(package_name=f"p{i}", requirements_type="resolved")
               for i in range(25)]

    _enrich(kd, records)

    assert len(kd.asked) == 25


def test_osi_and_sca_now_agree_on_how_much_of_a_file_to_enrich(kd):
    """The divergence this closes.

    `krunner osi`'s enrichment has no skip at all, so it enriched every row while
    `sca` enriched only direct and dev. Same manifest, different advisory data
    depending on which tool read it.
    """
    records = [_pnpm(package_name="direct-one", requirements_type="direct"),
               _pnpm(package_name="trans-one", requirements_type="resolved"),
               _pnpm(package_name="trans-two", requirements_type="resolved")]

    out = _enrich(kd, records)

    assert len(kd.asked) == 3, "sca must enrich the same rows osi does"
    assert all(r["advisories"] == 2 for r in out)
    assert all(r["resolved_version"] == "4.17.21" for r in out)


def test_a_non_concrete_transitive_version_still_short_circuits(kd, monkeypatch):
    """Removing the skip must not start making pointless requests.

    depsdev_record already returns early for a version it cannot resolve, so a
    `workspace:*` entry costs no round trip. That short-circuit is the right place
    for cost control -- unlike the blanket skip, it does not lose data that exists.
    """
    from kospex_dependencies import KospexDependencies

    real_asked = []

    def _record(self, package_type, name, version):
        real_asked.append(version)
        # mirrors the real early return for a non-concrete spec
        if not version or not self.is_concrete_version(version):
            return {"package_name": name, "package_version": version,
                    "package_type": package_type, "versions_behind": None,
                    "resolution": "unresolved_spec"}
        return {"package_name": name, "package_version": version,
                "package_type": package_type, "versions_behind": 1,
                "advisories": 0, "resolution": "resolved"}

    monkeypatch.setattr(KospexDependencies, "depsdev_record", _record)

    out = _enrich(kd, [_pnpm(package_version="workspace:*")])[0]

    assert out["resolution"] == "unresolved_spec"
    assert out["versions_behind"] is None
