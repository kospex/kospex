# HabitatConfig Implementation

**Date:** 2026-01-22
**Issue:** [GitHub #85](https://github.com/kospex/kospex/issues/85)
**Status:** Implemented

## Summary

Created a new `HabitatConfig` class in the `kospex` namespace to centralize configuration and initialization details. This addresses fragmented configuration management across the codebase.

## Problem

Configuration management was fragmented:
- 14+ hardcoded `os.path.expanduser("~/kospex")` calls across 8 files
- 22+ direct `os.getenv()` calls scattered throughout modules
- Duplicate `get_krunner_directory()` functions in `kospex_core.py` and `kospex_utils.py`
- No clear configuration precedence (env vars vs config file vs defaults)

## Solution

### HabitatConfig Class

A centralized singleton class that:
1. Defines all filesystem paths in one place
2. Implements clear configuration precedence: **Env vars > Config file > Defaults**
3. Provides validation for the kospex "habitat"
4. Supports testing through `reset_instance()` and `with_overrides()`

### Key Features

- **Singleton pattern** with thread-safe `get_instance()`
- **Path properties** returning `Path` objects:
  - `home` - KOSPEX_HOME directory (default: ~/kospex)
  - `code_dir` - KOSPEX_CODE directory (default: ~/code)
  - `db_path` - SQLite database path
  - `duckdb_path` - DuckDB database path
  - `logs_dir` - Logs directory
  - `config_file` - kospex.env config file path
  - `krunner_dir` - Krunner batch processing directory
  - `staging_dir` - Staging directory for sync operations
- **Validation** via `validate()` method
- **Directory creation** via `ensure_directories()` method
- **Lazy loading** of config file on first access
- **Testing support** via `reset_instance()` and `with_overrides()` context manager

### Staging Directory for Sync Operations

For improved sync work with large repositories, HabitatConfig now includes staging directory management. This allows commits to be staged to file during synchronization operations, which is essential for handling large repositories efficiently.

- **`staging_dir`** - Path to sync staging directory (default: `~/code/_sync-staging`)
- **`KOSPEX_STAGING`** - Environment variable override for staging path
- **`ensure_staging_dir()`** - Create staging directory if it doesn't exist
- **`is_staging_dir_writable()`** - Check if staging directory is writable
- **Validation** - `staging_dir_exists` and `staging_dir_writable` added to `validate()`

### Configuration Precedence

1. **Environment variables** (highest priority)
2. **Config file** (~/kospex/kospex.env)
3. **Default values** (lowest priority)

## Files Changed

| File | Change |
|------|--------|
| `src/kospex/habitat_config.py` | **Created** - New HabitatConfig class |
| `src/kospex/__init__.py` | **Modified** - Added HabitatConfig export |
| `src/kospex_utils.py` | **Modified** - Delegated helpers to HabitatConfig |
| `tests/test_habitat_config.py` | **Created** - 40 unit tests |

## Usage

```python
from kospex import HabitatConfig

# Get singleton instance
config = HabitatConfig.get_instance()

# Access paths
print(config.home)        # Path('~/kospex')
print(config.code_dir)    # Path('~/code')
print(config.db_path)     # Path('~/kospex/kospex.db')
print(config.duckdb_path) # Path('~/kospex/kospex-git.duckdb')
print(config.staging_dir) # Path('~/code/_sync-staging')

# Validate setup
result = config.validate()
if not result['valid']:
    print(result['errors'])

# Ensure directories exist
config.ensure_directories(verbose=True)

# Get all paths as dict
paths = config.get_all_paths()
```

### Testing with Overrides

```python
from kospex.habitat_config import HabitatConfig

def test_my_feature():
    HabitatConfig.reset_instance()  # Ensure clean state
    config = HabitatConfig.get_instance()

    with config.with_overrides(KOSPEX_HOME='/tmp/test'):
        assert str(config.home) == '/tmp/test'
        # Run tests with custom paths

    # Values restored after context exits
```

## Backward Compatibility

Existing helper functions in `kospex_utils.py` remain as wrappers that delegate to HabitatConfig:

- `get_kospex_db_path()` - returns `str(config.db_path)`
- `get_kospex_code_path()` - returns `str(config.code_dir)`
- `get_kospex_config()` - returns `str(config.config_file)`
- `get_krunner_directory()` - returns `str(config.krunner_dir)`

No breaking changes for existing code.

## Future Work

Optional migration of other modules to use HabitatConfig directly:
- `kospex_core.py` - Remove duplicate `get_krunner_directory()`
- `kospex_logging.py` - Use `config.home` for log paths
- `kospex/git_duckdb.py` - Use `config.duckdb_path`
- `repo_sync.py` - Update `RepoSyncConfig` to use HabitatConfig

## Testing

```bash
# Run HabitatConfig tests
pytest tests/test_habitat_config.py -v

# Verify all tests pass
pytest tests/ -v
```

All 99 tests pass including 40 new HabitatConfig tests.
