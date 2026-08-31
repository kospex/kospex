"""Go module versions must keep their `v` prefix for the deps.dev lookup.

deps.dev treats the `v` as part of a Go version. Measured against the live API:

    /systems/go/packages/github.com%2Fpkg%2Ferrors/versions/v0.9.1  -> 200
    /systems/go/packages/github.com%2Fpkg%2Ferrors/versions/0.9.1   -> 404

`clean_version_spec()` strips a leading `v`, which is right for npm and pypi
(`>=v1.2.3` means version `1.2.3` there) and wrong for Go. Both scan paths began
routing every ecosystem's lookup through it — `krunner osi` when go.mod support
landed, and `assess()` when its enrichment was unified — so every Go dependency
silently failed its lookup and was recorded with no advisories, no
versions_behind, and a misleading `resolution`.
"""
import contextlib
import io

import pytest

from kospex_dependencies import KospexDependencies


def _kdeps():
    kd = KospexDependencies()
    calls = []
    kd.depsdev_record = lambda pt, pn, pv: (
        calls.append((pt, pn, pv)),
        {"package_name": pn, "package_version": pv, "package_type": pt,
         "versions_behind": 0, "advisories": 0, "resolution": "resolved",
         "published_at": "", "source_repo": ""},
    )[1]
    return kd, calls


class TestCleanVersionSpec:
    """The `v` is ecosystem-dependent, so the helper has to be told which."""

    def test_go_keeps_the_v_prefix(self):
        kd = KospexDependencies()
        assert kd.clean_version_spec("v0.9.1", package_type="go") == "v0.9.1"

    def test_go_keeps_the_v_after_an_operator(self):
        kd = KospexDependencies()
        assert kd.clean_version_spec(">=v1.2.3", package_type="go") == "v1.2.3"

    def test_non_go_still_strips_the_v(self):
        """npm and pypi treat a leading v as decoration, not part of the version."""
        kd = KospexDependencies()
        assert kd.clean_version_spec("v1.2.3") == "1.2.3"
        assert kd.clean_version_spec("v1.2.3", package_type="npm") == "1.2.3"
        assert kd.clean_version_spec(">=v1.2.3", package_type="pypi") == "1.2.3"

    def test_go_ranges_still_reduce_to_a_single_version(self):
        """Preserving `v` must not disable the rest of the normalisation."""
        kd = KospexDependencies()
        assert kd.clean_version_spec("1.2.3", package_type="go") == "1.2.3"


class TestAssessPath:
    def test_gomod_lookup_keeps_the_v_prefix(self, tmp_path):
        kd, calls = _kdeps()
        p = tmp_path / "go.mod"
        p.write_text("module x\nrequire github.com/pkg/errors v0.9.1\n")

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            kd.assess(str(p))

        assert ("go", "github.com/pkg/errors", "v0.9.1") in calls, (
            f"Go lookup lost the v prefix: {calls}"
        )

    def test_npm_lookup_is_unaffected(self, tmp_path):
        """The fix must not change the ecosystems that were already correct."""
        kd, calls = _kdeps()
        p = tmp_path / "package.json"
        p.write_text('{"name":"d","dependencies":{"express":"^4.18.0"}}')

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            kd.assess(str(p))

        assert ("npm", "express", "4.18.0") in calls, calls


class TestOsiPath:
    """krunner osi enriches a pooled list, so it needs the same ecosystem
    awareness — it hit this first, when go.mod support landed there."""

    def test_gomod_lookup_keeps_the_v_prefix(self):
        import krunner
        kd, calls = _kdeps()
        rows = [{
            "package_name": "github.com/pkg/errors",
            "package_version": "v0.9.1",
            "package_type": "go",
            "requirements_type": "direct",
        }]

        krunner.enrich_dependency_records(rows, kd)

        assert ("go", "github.com/pkg/errors", "v0.9.1") in calls, (
            f"Go lookup lost the v prefix: {calls}"
        )

    def test_package_use_is_still_mapped(self):
        import krunner
        import kospex_schema as KospexSchema
        kd, _calls = _kdeps()
        rows = [
            {"package_name": "a", "package_version": "v1.0.0",
             "package_type": "go", "requirements_type": "indirect"},
            {"package_name": "b", "package_version": "1.0.0",
             "package_type": "npm", "requirements_type": "dev"},
        ]

        krunner.enrich_dependency_records(rows, kd)

        assert rows[0]["package_use"] == KospexSchema.PACKAGE_USE_TRANSITIVE
        assert rows[1]["package_use"] == KospexSchema.PACKAGE_USE_DEV
