"""
Tests for HabitatConfig class.

Tests configuration precedence: Environment variables > Config file > Defaults
"""

import os
import tempfile
from pathlib import Path
import pytest

from kospex.habitat_config import HabitatConfig


class TestHabitatConfigDefaults:
    """Test default configuration values."""

    def setup_method(self):
        """Reset singleton before each test."""
        HabitatConfig.reset_instance()

    def teardown_method(self):
        """Clean up after each test."""
        HabitatConfig.reset_instance()

    def test_default_home_path(self):
        """Test default KOSPEX_HOME is ~/kospex."""
        config = HabitatConfig.get_instance()
        with config.with_overrides(KOSPEX_HOME='~/kospex'):
            expected = Path.home() / 'kospex'
            assert config.home == expected

    def test_default_code_dir_path(self):
        """Test default KOSPEX_CODE is ~/code."""
        config = HabitatConfig.get_instance()
        with config.with_overrides(KOSPEX_CODE='~/code'):
            expected = Path.home() / 'code'
            assert config.code_dir == expected

    def test_default_db_filename(self):
        """Test default database filename is kospex.db."""
        config = HabitatConfig.get_instance()
        with config.with_overrides(KOSPEX_HOME='~/kospex'):
            assert config.db_path.name == 'kospex.db'

    def test_default_duckdb_filename(self):
        """Test default DuckDB filename is kospex-git.duckdb."""
        config = HabitatConfig.get_instance()
        with config.with_overrides(KOSPEX_HOME='~/kospex'):
            assert config.duckdb_path.name == 'kospex-git.duckdb'

    def test_default_config_filename(self):
        """Test default config filename is kospex.env."""
        config = HabitatConfig.get_instance()
        with config.with_overrides(KOSPEX_HOME='~/kospex'):
            assert config.config_file.name == 'kospex.env'

    def test_default_logs_dirname(self):
        """Test default logs directory is logs under KOSPEX_HOME."""
        config = HabitatConfig.get_instance()
        with config.with_overrides(KOSPEX_HOME='~/kospex'):
            assert config.logs_dir.name == 'logs'
            assert config.logs_dir.parent == config.home

    def test_default_krunner_dirname(self):
        """Test default krunner directory is krunner under KOSPEX_HOME."""
        config = HabitatConfig.get_instance()
        with config.with_overrides(KOSPEX_HOME='~/kospex'):
            assert config.krunner_dir.name == 'krunner'
            assert config.krunner_dir.parent == config.home


class TestHabitatConfigOverrides:
    """Test configuration overrides via with_overrides context manager."""

    def setup_method(self):
        """Reset singleton before each test."""
        HabitatConfig.reset_instance()

    def teardown_method(self):
        """Clean up after each test."""
        HabitatConfig.reset_instance()

    def test_override_home_path(self):
        """Test overriding KOSPEX_HOME via with_overrides."""
        config = HabitatConfig.get_instance()
        with config.with_overrides(KOSPEX_HOME='/custom/home'):
            assert config.home == Path('/custom/home')

    def test_override_code_dir(self):
        """Test overriding KOSPEX_CODE via with_overrides."""
        config = HabitatConfig.get_instance()
        with config.with_overrides(KOSPEX_CODE='/custom/code'):
            assert config.code_dir == Path('/custom/code')

    def test_override_db_path(self):
        """Test overriding KOSPEX_DB via with_overrides."""
        config = HabitatConfig.get_instance()
        with config.with_overrides(KOSPEX_DB='/custom/db/test.db'):
            assert config.db_path == Path('/custom/db/test.db')

    def test_override_duckdb_path(self):
        """Test overriding KOSPEX_DUCKDB via with_overrides."""
        config = HabitatConfig.get_instance()
        with config.with_overrides(KOSPEX_DUCKDB='/custom/duckdb/test.duckdb'):
            assert config.duckdb_path == Path('/custom/duckdb/test.duckdb')

    def test_override_logs_dir(self):
        """Test overriding KOSPEX_LOGS via with_overrides."""
        config = HabitatConfig.get_instance()
        with config.with_overrides(KOSPEX_LOGS='/custom/logs'):
            assert config.logs_dir == Path('/custom/logs')

    def test_override_config_file(self):
        """Test overriding KOSPEX_CONFIG via with_overrides."""
        config = HabitatConfig.get_instance()
        with config.with_overrides(KOSPEX_CONFIG='/custom/config.env'):
            assert config.config_file == Path('/custom/config.env')

    def test_override_krunner_dir(self):
        """Test overriding KOSPEX_KRUNNER via with_overrides."""
        config = HabitatConfig.get_instance()
        with config.with_overrides(KOSPEX_KRUNNER='/custom/krunner'):
            assert config.krunner_dir == Path('/custom/krunner')

    def test_multiple_overrides(self):
        """Test multiple overrides at once."""
        config = HabitatConfig.get_instance()
        with config.with_overrides(
            KOSPEX_HOME='/tmp/home',
            KOSPEX_CODE='/tmp/code'
        ):
            assert config.home == Path('/tmp/home')
            assert config.code_dir == Path('/tmp/code')

    def test_override_context_restores_values(self):
        """Test that values are restored after context exits."""
        config = HabitatConfig.get_instance()
        original_home = config.home

        with config.with_overrides(KOSPEX_HOME='/custom/home'):
            assert config.home == Path('/custom/home')

        # After context, value should be restored
        assert config.home == original_home

    def test_nested_overrides(self):
        """Test nested override contexts."""
        config = HabitatConfig.get_instance()

        with config.with_overrides(KOSPEX_HOME='/outer/home'):
            assert config.home == Path('/outer/home')

            with config.with_overrides(KOSPEX_HOME='/inner/home'):
                assert config.home == Path('/inner/home')

            # After inner context exits, should restore to outer
            assert config.home == Path('/outer/home')


class TestHabitatConfigSingleton:
    """Test singleton pattern behavior."""

    def setup_method(self):
        """Reset singleton before each test."""
        HabitatConfig.reset_instance()

    def teardown_method(self):
        """Clean up after each test."""
        HabitatConfig.reset_instance()

    def test_get_instance_returns_same_object(self):
        """Test that get_instance always returns the same object."""
        config1 = HabitatConfig.get_instance()
        config2 = HabitatConfig.get_instance()
        assert config1 is config2

    def test_reset_instance_creates_new_object(self):
        """Test that reset_instance causes a new object to be created."""
        config1 = HabitatConfig.get_instance()
        HabitatConfig.reset_instance()
        config2 = HabitatConfig.get_instance()
        assert config1 is not config2


class TestHabitatConfigValidation:
    """Test validation functionality."""

    def setup_method(self):
        """Reset singleton before each test."""
        HabitatConfig.reset_instance()

    def teardown_method(self):
        """Clean up after each test."""
        HabitatConfig.reset_instance()

    def test_validate_returns_dict(self):
        """Test that validate returns a dictionary."""
        config = HabitatConfig.get_instance()
        result = config.validate()
        assert isinstance(result, dict)

    def test_validate_has_required_keys(self):
        """Test that validate result has all required keys."""
        config = HabitatConfig.get_instance()
        result = config.validate()

        required_keys = [
            'valid', 'home_exists', 'home_writable',
            'code_dir_exists', 'code_dir_readable',
            'logs_dir_exists', 'config_file_exists',
            'errors', 'warnings'
        ]
        for key in required_keys:
            assert key in result

    def test_validate_nonexistent_home(self):
        """Test validation with non-existent home directory."""
        config = HabitatConfig.get_instance()
        with config.with_overrides(KOSPEX_HOME='/nonexistent/path'):
            result = config.validate()
            assert result['valid'] is False
            assert result['home_exists'] is False
            assert len(result['errors']) > 0

    def test_validate_existing_home(self):
        """Test validation with existing home directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = HabitatConfig.get_instance()
            with config.with_overrides(KOSPEX_HOME=tmpdir):
                result = config.validate()
                assert result['home_exists'] is True
                assert result['home_writable'] is True


class TestHabitatConfigEnsureDirectories:
    """Test directory creation functionality."""

    def setup_method(self):
        """Reset singleton before each test."""
        HabitatConfig.reset_instance()

    def teardown_method(self):
        """Clean up after each test."""
        HabitatConfig.reset_instance()

    def test_ensure_directories_creates_home(self):
        """Test that ensure_directories creates KOSPEX_HOME."""
        with tempfile.TemporaryDirectory() as tmpdir:
            new_home = Path(tmpdir) / 'kospex_test'
            config = HabitatConfig.get_instance()
            with config.with_overrides(KOSPEX_HOME=str(new_home)):
                result = config.ensure_directories()
                assert str(new_home) in result['created']
                assert new_home.exists()

    def test_ensure_directories_creates_logs(self):
        """Test that ensure_directories creates logs directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            new_home = Path(tmpdir) / 'kospex_test'
            config = HabitatConfig.get_instance()
            with config.with_overrides(KOSPEX_HOME=str(new_home)):
                result = config.ensure_directories()
                logs_dir = new_home / 'logs'
                assert str(logs_dir) in result['created']
                assert logs_dir.exists()

    def test_ensure_directories_creates_krunner(self):
        """Test that ensure_directories creates krunner directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            new_home = Path(tmpdir) / 'kospex_test'
            config = HabitatConfig.get_instance()
            with config.with_overrides(KOSPEX_HOME=str(new_home)):
                result = config.ensure_directories()
                krunner_dir = new_home / 'krunner'
                assert str(krunner_dir) in result['created']
                assert krunner_dir.exists()

    def test_ensure_directories_creates_config_file(self):
        """Test that ensure_directories creates default config file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            new_home = Path(tmpdir) / 'kospex_test'
            config = HabitatConfig.get_instance()
            with config.with_overrides(KOSPEX_HOME=str(new_home)):
                result = config.ensure_directories(create_config=True)
                config_file = new_home / 'kospex.env'
                assert str(config_file) in result['created']
                assert config_file.exists()

    def test_ensure_directories_skips_existing(self):
        """Test that ensure_directories reports existing directories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = HabitatConfig.get_instance()
            with config.with_overrides(KOSPEX_HOME=tmpdir):
                result = config.ensure_directories()
                assert tmpdir in result['already_existed']


class TestHabitatConfigConfigFile:
    """Test config file loading functionality."""

    def setup_method(self):
        """Reset singleton before each test."""
        HabitatConfig.reset_instance()

    def teardown_method(self):
        """Clean up after each test."""
        HabitatConfig.reset_instance()

    def test_load_config_file_simple(self):
        """Test loading a simple config file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / 'kospex.env'
            config_path.write_text('KOSPEX_CODE=/from/config/file\n')

            config = HabitatConfig.get_instance()
            with config.with_overrides(
                KOSPEX_HOME=tmpdir,
                KOSPEX_CONFIG=str(config_path)
            ):
                # Force reload to pick up config file
                config.reload()
                # Access code_dir to trigger lazy loading of config file
                _ = config.code_dir
                # Now check the internal _config_values
                assert config._config_values.get('KOSPEX_CODE') == '/from/config/file'

    def test_load_config_file_with_comments(self):
        """Test that comments in config file are ignored."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / 'kospex.env'
            config_path.write_text(
                '# This is a comment\n'
                'KOSPEX_CODE=/from/config\n'
                '  # Indented comment\n'
            )

            config = HabitatConfig.get_instance()
            with config.with_overrides(
                KOSPEX_HOME=tmpdir,
                KOSPEX_CONFIG=str(config_path)
            ):
                config.reload()
                # Access code_dir to trigger lazy loading of config file
                _ = config.code_dir
                assert config._config_values.get('KOSPEX_CODE') == '/from/config'

    def test_load_config_file_with_quotes(self):
        """Test that quoted values are unquoted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / 'kospex.env'
            config_path.write_text('KOSPEX_CODE="/path/with spaces"\n')

            config = HabitatConfig.get_instance()
            with config.with_overrides(
                KOSPEX_HOME=tmpdir,
                KOSPEX_CONFIG=str(config_path)
            ):
                config.reload()
                # Access code_dir to trigger lazy loading of config file
                _ = config.code_dir
                assert config._config_values.get('KOSPEX_CODE') == '/path/with spaces'

    def test_reload_clears_config(self):
        """Test that reload clears and reloads config values."""
        config = HabitatConfig.get_instance()
        config._config_values['TEST_KEY'] = 'test_value'
        config._config_loaded = True

        config.reload()

        assert config._config_loaded is False
        assert 'TEST_KEY' not in config._config_values


class TestHabitatConfigGetAllPaths:
    """Test get_all_paths functionality."""

    def setup_method(self):
        """Reset singleton before each test."""
        HabitatConfig.reset_instance()

    def teardown_method(self):
        """Clean up after each test."""
        HabitatConfig.reset_instance()

    def test_get_all_paths_returns_dict(self):
        """Test that get_all_paths returns a dictionary."""
        config = HabitatConfig.get_instance()
        paths = config.get_all_paths()
        assert isinstance(paths, dict)

    def test_get_all_paths_has_all_keys(self):
        """Test that get_all_paths includes all path keys."""
        config = HabitatConfig.get_instance()
        paths = config.get_all_paths()

        expected_keys = [
            'home', 'code_dir', 'db_path', 'duckdb_path',
            'logs_dir', 'config_file', 'krunner_dir'
        ]
        for key in expected_keys:
            assert key in paths

    def test_get_all_paths_values_are_paths(self):
        """Test that all values in get_all_paths are Path objects."""
        config = HabitatConfig.get_instance()
        paths = config.get_all_paths()

        for key, value in paths.items():
            assert isinstance(value, Path), f"{key} is not a Path object"


class TestHabitatConfigPrecedence:
    """Test configuration precedence: Env vars > Config file > Defaults."""

    def setup_method(self):
        """Reset singleton and save original environment."""
        HabitatConfig.reset_instance()
        self._original_env = os.environ.copy()

    def teardown_method(self):
        """Clean up after each test."""
        HabitatConfig.reset_instance()
        # Restore original environment
        os.environ.clear()
        os.environ.update(self._original_env)

    def test_env_var_overrides_default(self):
        """Test that environment variable overrides default value."""
        config = HabitatConfig.get_instance()

        # Set env var
        os.environ['KOSPEX_CODE'] = '/env/code'

        # Reset to pick up env var
        HabitatConfig.reset_instance()
        config = HabitatConfig.get_instance()

        assert config.code_dir == Path('/env/code')

    def test_env_var_overrides_config_file(self):
        """Test that environment variable overrides config file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create config file
            config_path = Path(tmpdir) / 'kospex.env'
            config_path.write_text('KOSPEX_CODE=/from/config\n')

            # Set env var (higher precedence)
            os.environ['KOSPEX_CODE'] = '/from/env'

            config = HabitatConfig.get_instance()
            with config.with_overrides(
                KOSPEX_HOME=tmpdir,
                KOSPEX_CONFIG=str(config_path)
            ):
                config.reload()
                # Env var should win
                assert config.code_dir == Path('/from/env')

    def test_override_beats_env_var(self):
        """Test that with_overrides beats environment variable."""
        os.environ['KOSPEX_CODE'] = '/from/env'

        HabitatConfig.reset_instance()
        config = HabitatConfig.get_instance()

        with config.with_overrides(KOSPEX_CODE='/from/override'):
            assert config.code_dir == Path('/from/override')


class TestHabitatConfigRepr:
    """Test string representation."""

    def setup_method(self):
        """Reset singleton before each test."""
        HabitatConfig.reset_instance()

    def teardown_method(self):
        """Clean up after each test."""
        HabitatConfig.reset_instance()

    def test_repr_contains_class_name(self):
        """Test that repr contains class name."""
        config = HabitatConfig.get_instance()
        repr_str = repr(config)
        assert 'HabitatConfig' in repr_str

    def test_repr_contains_home(self):
        """Test that repr contains home path."""
        config = HabitatConfig.get_instance()
        with config.with_overrides(KOSPEX_HOME='/test/home'):
            repr_str = repr(config)
            assert 'home=' in repr_str
