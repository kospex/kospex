"""Regression test for issue #108.

`pypi_assess` handled a requirements.txt line with multiple version specifiers
(e.g. `requests>=1.0,<2.0`) by emitting a record that carried only
`package_name` — `package_version` was dropped and no `resolution` was set. A
multiple-specifier line is simply an unresolved spec, so it should route through
the `depsdev_record` seam like any other non-concrete version: retain the
declared spec as `package_version` and classify it `unresolved_spec` (no deps.dev
call, since the version isn't concrete).
"""
from kospex_dependencies import KospexDependencies


def test_pypi_multi_specifier_retains_version_and_classifies_unresolved(tmp_path):
    req = tmp_path / "requirements.txt"
    req.write_text("requests>=1.0,<2.0\n")

    kd = KospexDependencies()
    records = kd.pypi_assess(
        str(req),
        repo_info={"_repo_id": "s~o~r", "hash": "h", "file_path": "requirements.txt"},
    )

    assert len(records) == 1
    rec = records[0]
    assert rec["package_name"] == "requests"
    assert rec["package_version"] == ">=1.0,<2.0"
    assert rec["resolution"] == "unresolved_spec"
    assert rec.get("versions_behind") is None
