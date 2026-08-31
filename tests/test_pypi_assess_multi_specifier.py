"""Regression test for issue #108.

`pypi_assess` handled a requirements.txt line with multiple version specifiers
(e.g. `requests>=1.0,<2.0`) by emitting a record that carried only
`package_name` — `package_version` was dropped and no `resolution` was set. A
multiple-specifier line is simply an unresolved spec, so it should route through
the `depsdev_record` seam like any other non-concrete version: retain the
declared spec as `package_version` and classify it `unresolved_spec` (no deps.dev
call, since the version isn't concrete).

STATUS: xfail pending a decision on #187.

`assess()` now normalises the lookup through `clean_version_spec()`, which
reduces `>=1.0,<2.0` to its floor `1.0`. That is concrete, so the row gets a
real deps.dev lookup and `resolution='resolved'` instead of `unresolved_spec`.

This is not simply a regression to revert. `krunner osi` has always behaved this
way, so #188 aligned `assess()` to it rather than breaking it — and the floor
lookup yields real advisory data that the old path discarded. What is genuinely
wrong is that `resolution` cannot distinguish "the declaration was a range" from
"the declaration was a pin", which is the column #187 exists to add.

Marked strict so it fails loudly if the behaviour changes: whoever resolves #187
must either delete this marker (behaviour restored) or rewrite the assertions
(behaviour deliberately kept). It must not quietly start passing.
"""
import pytest

from kospex_dependencies import KospexDependencies


@pytest.mark.xfail(
    strict=True,
    reason="#187: floor-resolution makes a range look resolved; decision pending",
)
def test_pypi_multi_specifier_retains_version_and_classifies_unresolved(tmp_path):
    req = tmp_path / "requirements.txt"
    req.write_text("requests>=1.0,<2.0\n")

    # Previously called pypi_assess() directly; that became dead when assess()
    # moved to registry dispatch (sub-project C2), so this now exercises the
    # path that actually runs.
    import contextlib
    import io

    kd = KospexDependencies()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        records = kd.assess(
            str(req),
            repo_info={"_repo_id": "s~o~r", "hash": "h", "file_path": "requirements.txt"},
        )

    assert len(records) == 1
    rec = records[0]
    assert rec["package_name"] == "requests"
    assert rec["package_version"] == ">=1.0,<2.0"
    assert rec["resolution"] == "unresolved_spec"
    assert rec.get("versions_behind") is None
