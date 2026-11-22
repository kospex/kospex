"""KospexUtils.init() must not override the path settings (#466).

HabitatConfig resolves every path setting as: environment, then kospex.env,
then the default. init() used to write KOSPEX_DB and KOSPEX_CONFIG into
os.environ unconditionally, and the environment is the top of that order, so
a user's KOSPEX_DB was replaced by ~/kospex/kospex.db before any command ran.
"""
import os
from pathlib import Path

import pytest

import kospex_utils as KospexUtils
from kospex.habitat_config import HabitatConfig

PATH_VARS = ("KOSPEX_HOME", "KOSPEX_DB", "KOSPEX_CONFIG", "KOSPEX_CODE", "KOSPEX_LOGS")


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A throwaway KOSPEX_HOME with no other path variable set."""
    kospex_home = tmp_path / "kospex"
    kospex_home.mkdir()
    for var in PATH_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("KOSPEX_HOME", str(kospex_home))
    HabitatConfig.reset_instance()
    return kospex_home


def _env_file(home, text):
    (home / "kospex.env").write_text(text)


def _config():
    HabitatConfig.reset_instance()
    return HabitatConfig.get_instance()


# --- init() leaves the environment's path settings alone -------------------


def test_init_keeps_kospex_db_from_the_environment(home, tmp_path, monkeypatch):
    copy = tmp_path / "copy.db"
    monkeypatch.setenv("KOSPEX_DB", str(copy))

    KospexUtils.init(create_directories=False, setup_logging=False)

    assert os.environ["KOSPEX_DB"] == str(copy)
    assert _config().db_path == copy
    assert KospexUtils.get_kospex_db_path() == str(copy)


def test_init_keeps_kospex_config_from_the_environment(home, tmp_path, monkeypatch):
    custom = tmp_path / "custom.env"
    custom.write_text(f"KOSPEX_CODE={tmp_path}\n")
    monkeypatch.setenv("KOSPEX_CONFIG", str(custom))

    KospexUtils.init(create_directories=False, setup_logging=False)

    assert os.environ["KOSPEX_CONFIG"] == str(custom)
    config = _config()
    assert config.config_file == custom
    assert config.code_dir == tmp_path          # the custom file was read


def test_init_exports_no_path_variable(home, monkeypatch):
    monkeypatch.delenv("KOSPEX_HOME")
    monkeypatch.setenv("HOME", str(home.parent))   # default ~/kospex -> tmp

    KospexUtils.init(create_directories=False, setup_logging=False)

    for var in PATH_VARS:
        assert var not in os.environ, var


# --- kospex.env -------------------------------------------------------------


def test_kospex_db_in_kospex_env_takes_effect(home, tmp_path):
    target = tmp_path / "from-env-file.db"
    _env_file(home, f"KOSPEX_DB={target}\n")

    KospexUtils.init(create_directories=False, setup_logging=False)

    assert _config().db_path == target
    assert KospexUtils.get_kospex_db_path() == str(target)


def test_kospex_db_in_kospex_env_without_init(home, tmp_path):
    """HabitatConfig reads kospex.env itself; it does not depend on init()."""
    target = tmp_path / "from-env-file.db"
    _env_file(home, f"KOSPEX_DB={target}\n")

    config = _config()
    assert config.db_path == target
    assert config.source("KOSPEX_DB") == "kospex.env"


def test_environment_beats_kospex_env(home, tmp_path, monkeypatch):
    _env_file(home, f"KOSPEX_DB={tmp_path / 'file.db'}\n")
    monkeypatch.setenv("KOSPEX_DB", str(tmp_path / "env.db"))

    KospexUtils.init(create_directories=False, setup_logging=False)

    config = _config()
    assert config.db_path == tmp_path / "env.db"
    assert config.source("KOSPEX_DB") == "environment"


def test_tilde_kospex_code_in_kospex_env_validates_as_existing(home, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "mycode").mkdir()
    _env_file(home, "KOSPEX_CODE=~/mycode\n")

    status = KospexUtils.init(create_directories=False, setup_logging=False)
    HabitatConfig.reset_instance()
    validation = KospexUtils.validate_kospex_setup()

    code = validation["directories"]["kospex_code"]
    assert code["path"] == str(tmp_path / "mycode")
    assert code["exists"] is True
    assert not any("KOSPEX_CODE" in w for w in status["warnings"])


def test_missing_code_dir_warning_names_the_configured_path(home, tmp_path, monkeypatch):
    nope = tmp_path / "nope"
    monkeypatch.setenv("KOSPEX_CODE", str(nope))

    status = KospexUtils.init(create_directories=False, setup_logging=False)

    assert any(str(nope) in w for w in status["warnings"]), status["warnings"]


@pytest.mark.parametrize("var", ["KOSPEX_HOME", "KOSPEX_CONFIG"])
def test_kospex_env_cannot_relocate_itself(home, tmp_path, var):
    _env_file(home, f"{var}={tmp_path / 'elsewhere'}\n")

    status = KospexUtils.init(create_directories=False, setup_logging=False)

    assert var not in os.environ or os.environ[var] == str(home)
    config = _config()
    assert config.home == home
    assert config.config_file == home / "kospex.env"
    assert any(var in w for w in status["warnings"]), status["warnings"]


def test_home_tilde_is_expanded(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "user"))
    monkeypatch.setenv("KOSPEX_HOME", "~/khome")
    work = tmp_path / "cwd"
    work.mkdir()
    monkeypatch.chdir(work)   # unexpanded, the old init() made ./~/khome here

    status = KospexUtils.init(create_directories=True, setup_logging=False)

    assert status["kospex_home"] == str(tmp_path / "user" / "khome")
    assert (tmp_path / "user" / "khome" / "logs").is_dir()
    assert not (work / "~").exists()


def test_one_parser_for_kospex_env(home, tmp_path):
    """init() and HabitatConfig read an `export` prefix and inline comment alike."""
    code = tmp_path / "exported"
    _env_file(home, f"export KOSPEX_CODE={code}  # my code\n")

    assert _config().code_dir == code


def test_init_loads_other_settings_but_not_paths(home, tmp_path, monkeypatch):
    """kospex.env still reaches the environment for settings kospex reads from
    os.environ (tokens, REQUESTS_CA_BUNDLE); path settings stay in the file,
    so they are reported as coming from kospex.env."""
    # setenv first so monkeypatch restores what load_config() writes.
    for var in ("KOSPEX_TEST_ONLY_SETTING", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"):
        monkeypatch.setenv(var, "x")
        monkeypatch.delenv(var)
    monkeypatch.setenv("HOME", str(tmp_path))
    _env_file(home, "KOSPEX_TEST_ONLY_SETTING=yes\n"
                    f"KOSPEX_DB={tmp_path / 'f.db'}\n"
                    "REQUESTS_CA_BUNDLE=~/ca.pem\n")

    status = KospexUtils.init(create_directories=False, setup_logging=False)

    assert os.environ["KOSPEX_TEST_ONLY_SETTING"] == "yes"
    assert os.environ["REQUESTS_CA_BUNDLE"] == str(tmp_path / "ca.pem")
    assert "KOSPEX_DB" not in os.environ
    assert sorted(status["environment_vars_set"]) == [
        "KOSPEX_TEST_ONLY_SETTING", "REQUESTS_CA_BUNDLE"]
    assert _config().source("KOSPEX_DB") == "kospex.env"


def test_logs_follow_kospex_logs(home, tmp_path, monkeypatch):
    from kospex_logging import KospexLogger
    logs = tmp_path / "elsewhere" / "logs"
    monkeypatch.setenv("KOSPEX_LOGS", str(logs))
    HabitatConfig.reset_instance()

    assert KospexLogger().logs_dir == logs


def test_validate_reports_the_source(home, tmp_path, monkeypatch):
    _env_file(home, f"KOSPEX_DB={tmp_path / 'f.db'}\n")
    monkeypatch.setenv("KOSPEX_CODE", str(tmp_path))
    HabitatConfig.reset_instance()

    paths = KospexUtils.validate_kospex_setup()["environment_vars"]

    assert paths["KOSPEX_DB"] == {"set": True, "value": str(tmp_path / "f.db"),
                                  "source": "kospex.env"}
    assert paths["KOSPEX_CODE"]["source"] == "environment"
    assert paths["KOSPEX_LOGS"] == {"set": False, "value": str(home / "logs"),
                                    "source": "default"}
    assert list(paths) == ["KOSPEX_HOME", "KOSPEX_CONFIG", "KOSPEX_DB",
                           "KOSPEX_CODE", "KOSPEX_LOGS"]


# End-to-end tests through each CLI are in test_cli_path_precedence.py.
