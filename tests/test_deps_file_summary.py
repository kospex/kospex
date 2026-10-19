"""`kospex deps -file` summary counts unresolved packages separately (#264).

depsdev_record() sets versions_behind = None for a lookup it could not resolve
(#106), meaning "we could not tell". The summary compared it with `> 2` and
crashed on any manifest holding an unpinned or unknown package. Unresolved
packages are neither outdated nor current, so the percentages are taken over
resolved packages only. What "compliance" should mean is left to #265.
"""
import pytest
from click.testing import CliRunner

from kospex_dependencies import KospexDependencies

# versions_behind by package name; None is how depsdev_record() reports an
# unresolved lookup.
VERSIONS_BEHIND = {"requests": 5, "click": 0, "flask": 1, "pyyaml": None}


@pytest.fixture
def run_deps(monkeypatch, tmp_path):
    """Invoke `kospex deps -file` on a requirements.txt with deps.dev stubbed."""
    from kospex.habitat_config import HabitatConfig

    home = tmp_path / "kospex_home"
    home.mkdir()
    monkeypatch.setenv("KOSPEX_HOME", str(home))
    monkeypatch.setenv("KOSPEX_DB", str(home / "kospex.db"))
    HabitatConfig.reset_instance()

    def depsdev_record(self, pt, pn, pv):
        return {"package_name": pn, "package_version": pv, "package_type": pt,
                "versions_behind": VERSIONS_BEHIND[pn], "advisories": 0}

    monkeypatch.setattr(KospexDependencies, "depsdev_record", depsdev_record)
    monkeypatch.setattr(KospexDependencies, "get_pypi_source_repo", lambda self, n: "")

    def run(lines, name="requirements.txt"):
        import kospex_cli

        path = tmp_path / name
        path.write_text("".join(f"{line}\n" for line in lines))
        return CliRunner().invoke(kospex_cli.cli, ["deps", "-file", str(path)])

    return run


def test_unresolved_package_does_not_crash(run_deps):
    result = run_deps(["requests==2.0.0", "pyyaml"])

    assert result.exit_code == 0, result.output
    assert result.exception is None


def test_percentages_are_over_resolved_packages_only(run_deps):
    result = run_deps(["requests==2.0.0", "click==8.1.7", "flask==3.0.0", "pyyaml"])

    assert ("Total dependencies: 4 | Resolved: 3 | Unresolved: 1 | Outdated(>2): 1"
            in result.output)
    assert "Non Compliant: 33.33% (of resolved)" in result.output
    assert "Compliant: 66.67% (of resolved)" in result.output


def test_all_unresolved_skips_percentages(run_deps):
    result = run_deps(["pyyaml"])

    assert result.exit_code == 0, result.output
    assert "Total dependencies: 1 | Resolved: 0 | Unresolved: 1 | Outdated(>2): 0" in result.output
    assert "Compliant" not in result.output


def test_unsupported_file_exits_1_instead_of_crashing(run_deps):
    result = run_deps(["anything"], name="notes.txt")

    assert result.exit_code == 1
    assert not isinstance(result.exception, TypeError)
