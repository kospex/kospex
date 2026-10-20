"""`kospex advisory-history` is disabled pending its redesign (#282).

It used to crash on scoped npm and Maven packages. Until the redesign lands it
must exit 1 with a notice on stderr, for every invocation, and make no
deps.dev calls.
"""
import pytest
from click.testing import CliRunner


@pytest.fixture(autouse=True)
def _current_db(monkeypatch, tmp_path):
    """A fresh, fully migrated DB in place of the one `kospex_cli` bound at
    import, so a developer's out-of-date database (and its pending-migration
    banner on stderr) never reaches the output being asserted. Setting
    KOSPEX_HOME alone is not enough: under the full suite another module
    imports kospex_cli first, binding its Kospex() to the real DB."""
    import kospex_cli
    import kospex_schema as KospexSchema
    from kospex.habitat_config import HabitatConfig

    monkeypatch.setenv("KOSPEX_HOME", str(tmp_path))
    HabitatConfig.reset_instance()
    monkeypatch.setattr(kospex_cli.kospex, "kospex_db",
                        KospexSchema.connect_or_create_kospex_db())


@pytest.mark.parametrize("args", [
    [],
    ["-ecosystem", "npm", "-package", "@babel/core"],
    ["-ecosystem", "pypi"],
])
def test_exits_1_with_notice_on_stderr(monkeypatch, args):
    import kospex_cli
    from kospex_dependencies import KospexDependencies

    def _no_network(*_a, **_kw):
        raise AssertionError("advisory-history must not call deps.dev while disabled")

    monkeypatch.setattr(KospexDependencies, "deps_dev_package", _no_network)
    monkeypatch.setattr(KospexDependencies, "deps_dev", _no_network)

    result = CliRunner().invoke(kospex_cli.cli, ["advisory-history", *args])

    assert result.exit_code == 1
    assert result.stderr.strip() == kospex_cli.ADVISORY_HISTORY_NOTICE
    assert result.stdout == ""


def test_help_does_not_advertise_a_working_command():
    import kospex_cli

    result = CliRunner().invoke(kospex_cli.cli, ["--help"])

    line = next(l for l in result.output.splitlines() if "advisory-history" in l)
    assert "redesigned" in line
