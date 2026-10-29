"""`kospex deps` finds dependency files; it no longer assesses one.

`deps -file` ran the same deps.dev lookup as `kospex sca FILE`, without -scope,
-json or -save, and was removed in favour of it. These pin that the option is
gone rather than silently ignored, and that the usage message says where it went.

Click reports an unknown single-dash option by its first letter (`No such option
'-f'`), so the tests check the usage error, not the option's spelling.
"""
import pytest
from click.testing import CliRunner


@pytest.fixture
def run_deps(monkeypatch, tmp_path):
    from kospex.habitat_config import HabitatConfig

    home = tmp_path / "kospex_home"
    home.mkdir()
    monkeypatch.setenv("KOSPEX_HOME", str(home))
    monkeypatch.setenv("KOSPEX_DB", str(home / "kospex.db"))
    HabitatConfig.reset_instance()

    def run(*args):
        import kospex_cli

        return CliRunner().invoke(kospex_cli.cli, ["deps", *args])

    return run


def test_file_option_is_rejected(run_deps, tmp_path):
    manifest = tmp_path / "requirements.txt"
    manifest.write_text("requests==2.0.0\n")

    result = run_deps("-file", str(manifest))

    assert result.exit_code == 2
    assert "No such option" in result.output


def test_dev_option_is_rejected(run_deps):
    result = run_deps("-dev")

    assert result.exit_code == 2
    assert "No such option" in result.output


def test_no_option_points_to_sca(run_deps):
    result = run_deps()

    assert result.exit_code == 0, result.output
    assert "kospex sca FILE" in result.output
