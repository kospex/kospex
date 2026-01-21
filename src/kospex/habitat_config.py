"""
HabitatConfig - Centralized configuration management for kospex habitat.

This module provides a singleton class that manages all filesystem paths
and configuration settings for the kospex environment. It implements a
clear configuration precedence: Environment variables > Config file > Defaults.

Usage:
    from kospex.habitat_config import HabitatConfig

    config = HabitatConfig.get_instance()
    print(config.home)        # Path to ~/kospex
    print(config.code_dir)    # Path to ~/code
    print(config.db_path)     # Path to kospex.db
"""

import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Optional, Any


class HabitatConfig:
    """Centralized configuration management for kospex habitat.

    This singleton class manages all filesystem paths and configuration
    settings for the kospex environment.

    Configuration Precedence (highest to lowest):
        1. Environment variables (e.g., KOSPEX_HOME, KOSPEX_CODE)
        2. Config file values (~/kospex/kospex.env)
        3. Default values

    Attributes:
        home: Path to KOSPEX_HOME directory (default: ~/kospex)
        code_dir: Path to KOSPEX_CODE directory (default: ~/code)
        db_path: Path to SQLite database file
        duckdb_path: Path to DuckDB database file
        logs_dir: Path to logs directory
        config_file: Path to kospex.env config file
        krunner_dir: Path to krunner directory
    """

    _instance: Optional['HabitatConfig'] = None
    _lock = threading.Lock()

    # Default configuration values
    DEFAULTS: Dict[str, str] = {
        'KOSPEX_HOME': '~/kospex',
        'KOSPEX_CODE': '~/code',
        'KOSPEX_DB_FILENAME': 'kospex.db',
        'KOSPEX_DUCKDB_FILENAME': 'kospex-git.duckdb',
        'KOSPEX_CONFIG_FILENAME': 'kospex.env',
        'KOSPEX_LOGS_DIRNAME': 'logs',
        'KOSPEX_KRUNNER_DIRNAME': 'krunner',
    }

    def __init__(self) -> None:
        """Initialize HabitatConfig.

        Note: Use get_instance() instead of direct instantiation.
        """
        self._config_loaded = False
        self._config_values: Dict[str, str] = {}
        self._overrides: Dict[str, Any] = {}

    @classmethod
    def get_instance(cls) -> 'HabitatConfig':
        """Get the singleton instance of HabitatConfig.

        Returns:
            The HabitatConfig singleton instance.

        Thread-safe implementation using double-checked locking.
        """
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton instance.

        This method is primarily for testing purposes to ensure
        test isolation. It clears the singleton instance so a fresh
        one will be created on next access.
        """
        with cls._lock:
            cls._instance = None

    def _get_value(self, key: str) -> str:
        """Get a configuration value following the precedence rules.

        Precedence (highest to lowest):
            1. Test overrides (if set via with_overrides)
            2. Environment variables
            3. Config file values
            4. Default values

        Args:
            key: The configuration key to look up.

        Returns:
            The configuration value as a string.
        """
        # Check overrides first (for testing)
        if key in self._overrides:
            return self._overrides[key]

        # Check environment variables
        env_value = os.getenv(key)
        if env_value is not None:
            return env_value

        # Lazy load config file if not yet loaded
        if not self._config_loaded:
            self._load_config_file()

        # Check config file values
        if key in self._config_values:
            return self._config_values[key]

        # Return default
        return self.DEFAULTS.get(key, '')

    def _load_config_file(self) -> None:
        """Load configuration from the kospex.env file.

        Uses dotenv-style parsing for the config file. The config file
        is loaded lazily on first access to any config value.
        """
        self._config_loaded = True
        config_path = self._get_config_file_path()

        if not config_path.exists():
            return

        try:
            with open(config_path, 'r') as f:
                for line in f:
                    line = line.strip()
                    # Skip empty lines and comments
                    if not line or line.startswith('#'):
                        continue
                    # Parse KEY=VALUE format
                    if '=' in line:
                        key, value = line.split('=', 1)
                        key = key.strip()
                        value = value.strip()
                        # Remove quotes if present
                        if value and value[0] in ('"', "'") and value[-1] == value[0]:
                            value = value[1:-1]
                        self._config_values[key] = value
        except (IOError, OSError):
            # If we can't read the config file, just continue with defaults
            pass

    def _get_config_file_path(self) -> Path:
        """Get the path to the config file without triggering config load.

        This is needed to avoid infinite recursion when loading the config file.
        """
        # Check override first
        if 'KOSPEX_CONFIG' in self._overrides:
            return Path(self._overrides['KOSPEX_CONFIG']).expanduser()

        # Check environment
        env_config = os.getenv('KOSPEX_CONFIG')
        if env_config:
            return Path(env_config).expanduser()

        # Build from home path
        home_override = self._overrides.get('KOSPEX_HOME')
        if home_override:
            home = Path(home_override).expanduser()
        else:
            home_env = os.getenv('KOSPEX_HOME')
            home = Path(home_env).expanduser() if home_env else Path(self.DEFAULTS['KOSPEX_HOME']).expanduser()

        return home / self.DEFAULTS['KOSPEX_CONFIG_FILENAME']

    def reload(self) -> None:
        """Reload configuration from the config file.

        Call this method to pick up changes to the config file
        or environment variables.
        """
        self._config_loaded = False
        self._config_values = {}

    @contextmanager
    def with_overrides(self, **overrides):
        """Context manager for temporarily overriding configuration values.

        This is primarily useful for testing to set specific configuration
        values without modifying environment variables or config files.

        Args:
            **overrides: Key-value pairs to override.

        Yields:
            The HabitatConfig instance with overrides applied.

        Example:
            with config.with_overrides(KOSPEX_HOME='/tmp/test'):
                assert str(config.home) == '/tmp/test'
        """
        old_overrides = self._overrides.copy()
        old_loaded = self._config_loaded
        old_values = self._config_values.copy()

        try:
            self._overrides.update(overrides)
            # Reset config loading to pick up new paths
            self._config_loaded = False
            self._config_values = {}
            yield self
        finally:
            self._overrides = old_overrides
            self._config_loaded = old_loaded
            self._config_values = old_values

    # =========================================================================
    # Path Properties
    # =========================================================================

    @property
    def home(self) -> Path:
        """Path to KOSPEX_HOME directory.

        Default: ~/kospex
        Override: Set KOSPEX_HOME environment variable or in config file.
        """
        return Path(self._get_value('KOSPEX_HOME')).expanduser()

    @property
    def code_dir(self) -> Path:
        """Path to KOSPEX_CODE directory for git repositories.

        Default: ~/code
        Override: Set KOSPEX_CODE environment variable or in config file.
        """
        return Path(self._get_value('KOSPEX_CODE')).expanduser()

    @property
    def db_path(self) -> Path:
        """Path to SQLite database file.

        Default: ~/kospex/kospex.db
        Override: Set KOSPEX_DB environment variable.
        """
        # Check for explicit KOSPEX_DB override
        db_override = self._overrides.get('KOSPEX_DB')
        if db_override:
            return Path(db_override).expanduser()

        db_env = os.getenv('KOSPEX_DB')
        if db_env:
            return Path(db_env).expanduser()

        return self.home / self._get_value('KOSPEX_DB_FILENAME')

    @property
    def duckdb_path(self) -> Path:
        """Path to DuckDB database file.

        Default: ~/kospex/kospex-git.duckdb
        Override: Set KOSPEX_DUCKDB environment variable.
        """
        # Check for explicit KOSPEX_DUCKDB override
        duckdb_override = self._overrides.get('KOSPEX_DUCKDB')
        if duckdb_override:
            return Path(duckdb_override).expanduser()

        duckdb_env = os.getenv('KOSPEX_DUCKDB')
        if duckdb_env:
            return Path(duckdb_env).expanduser()

        return self.home / self._get_value('KOSPEX_DUCKDB_FILENAME')

    @property
    def logs_dir(self) -> Path:
        """Path to logs directory.

        Default: ~/kospex/logs
        Override: Set KOSPEX_LOGS environment variable.
        """
        logs_override = self._overrides.get('KOSPEX_LOGS')
        if logs_override:
            return Path(logs_override).expanduser()

        logs_env = os.getenv('KOSPEX_LOGS')
        if logs_env:
            return Path(logs_env).expanduser()

        return self.home / self._get_value('KOSPEX_LOGS_DIRNAME')

    @property
    def config_file(self) -> Path:
        """Path to kospex.env configuration file.

        Default: ~/kospex/kospex.env
        Override: Set KOSPEX_CONFIG environment variable.
        """
        return self._get_config_file_path()

    @property
    def krunner_dir(self) -> Path:
        """Path to krunner directory for batch processing.

        Default: ~/kospex/krunner
        Override: Set KOSPEX_KRUNNER environment variable.
        """
        krunner_override = self._overrides.get('KOSPEX_KRUNNER')
        if krunner_override:
            return Path(krunner_override).expanduser()

        krunner_env = os.getenv('KOSPEX_KRUNNER')
        if krunner_env:
            return Path(krunner_env).expanduser()

        return self.home / self._get_value('KOSPEX_KRUNNER_DIRNAME')

    # =========================================================================
    # Validation and Directory Management
    # =========================================================================

    def validate(self) -> Dict[str, Any]:
        """Validate the kospex habitat configuration.

        Checks that required directories exist and are accessible.

        Returns:
            Dict containing validation results with keys:
                - valid: bool indicating overall validity
                - home_exists: bool
                - home_writable: bool
                - code_dir_exists: bool
                - code_dir_readable: bool
                - logs_dir_exists: bool
                - config_file_exists: bool
                - errors: list of error messages
                - warnings: list of warning messages
        """
        result = {
            'valid': True,
            'home_exists': False,
            'home_writable': False,
            'code_dir_exists': False,
            'code_dir_readable': False,
            'logs_dir_exists': False,
            'config_file_exists': False,
            'errors': [],
            'warnings': [],
        }

        # Check home directory
        if self.home.exists():
            result['home_exists'] = True
            if os.access(self.home, os.W_OK):
                result['home_writable'] = True
            else:
                result['errors'].append(f"KOSPEX_HOME '{self.home}' is not writable")
                result['valid'] = False
        else:
            result['errors'].append(f"KOSPEX_HOME '{self.home}' does not exist")
            result['valid'] = False

        # Check code directory
        if self.code_dir.exists():
            result['code_dir_exists'] = True
            if os.access(self.code_dir, os.R_OK):
                result['code_dir_readable'] = True
            else:
                result['warnings'].append(f"KOSPEX_CODE '{self.code_dir}' is not readable")
        else:
            result['warnings'].append(f"KOSPEX_CODE '{self.code_dir}' does not exist")

        # Check logs directory
        if self.logs_dir.exists():
            result['logs_dir_exists'] = True
        else:
            result['warnings'].append(f"Logs directory '{self.logs_dir}' does not exist")

        # Check config file
        if self.config_file.exists():
            result['config_file_exists'] = True

        return result

    def ensure_directories(self, create_config: bool = True, verbose: bool = False) -> Dict[str, Any]:
        """Ensure all required directories exist.

        Creates KOSPEX_HOME and logs directories if they don't exist.
        Optionally creates a default config file.

        Args:
            create_config: If True, create a default kospex.env if it doesn't exist.
            verbose: If True, print status messages.

        Returns:
            Dict with keys:
                - created: list of paths that were created
                - already_existed: list of paths that already existed
                - errors: list of error messages
        """
        result = {
            'created': [],
            'already_existed': [],
            'errors': [],
        }

        # Create home directory
        try:
            if not self.home.exists():
                self.home.mkdir(parents=True, mode=0o750)
                result['created'].append(str(self.home))
                if verbose:
                    print(f"✓ Created KOSPEX_HOME: {self.home}")
            else:
                result['already_existed'].append(str(self.home))
        except OSError as e:
            result['errors'].append(f"Failed to create KOSPEX_HOME: {e}")

        # Create logs directory
        try:
            if not self.logs_dir.exists():
                self.logs_dir.mkdir(parents=True, mode=0o750)
                result['created'].append(str(self.logs_dir))
                if verbose:
                    print(f"✓ Created logs directory: {self.logs_dir}")
            else:
                result['already_existed'].append(str(self.logs_dir))
        except OSError as e:
            result['errors'].append(f"Failed to create logs directory: {e}")

        # Create krunner directory
        try:
            if not self.krunner_dir.exists():
                self.krunner_dir.mkdir(parents=True, mode=0o750)
                result['created'].append(str(self.krunner_dir))
                if verbose:
                    print(f"✓ Created krunner directory: {self.krunner_dir}")
            else:
                result['already_existed'].append(str(self.krunner_dir))
        except OSError as e:
            result['errors'].append(f"Failed to create krunner directory: {e}")

        # Create default config file
        if create_config and not self.config_file.exists():
            try:
                default_config = """# Kospex Configuration
# Uncomment and modify settings as needed

# KOSPEX_CODE=~/code
# KOSPEX_HOME=~/kospex
"""
                self.config_file.write_text(default_config)
                result['created'].append(str(self.config_file))
                if verbose:
                    print(f"✓ Created config file: {self.config_file}")
            except OSError as e:
                result['errors'].append(f"Failed to create config file: {e}")

        return result

    def get_all_paths(self) -> Dict[str, Path]:
        """Get all configured paths as a dictionary.

        Returns:
            Dict mapping path names to Path objects.
        """
        return {
            'home': self.home,
            'code_dir': self.code_dir,
            'db_path': self.db_path,
            'duckdb_path': self.duckdb_path,
            'logs_dir': self.logs_dir,
            'config_file': self.config_file,
            'krunner_dir': self.krunner_dir,
        }

    def __repr__(self) -> str:
        """Return string representation of HabitatConfig."""
        return (
            f"HabitatConfig("
            f"home={self.home}, "
            f"code_dir={self.code_dir}, "
            f"db_path={self.db_path})"
        )
