# Assessments

**Date:** 2026-01-25
**Status:** Phase 1 Complete

## Objective

To track assessments and generate files in a common directory, without placing in current directory.

## Phase 1: Define assessments directory in HabitatConfig

**Status:** Complete

### Implementation

Added assessments directory support to HabitatConfig:
- Default path: `~/kospex/assessments`
- Environment variable override: `KOSPEX_ASSESSMENTS`
- Directory created automatically via `ensure_directories()`

### Files Changed

| File | Change |
|------|--------|
| `src/kospex/habitat_config.py` | Added `assessments_dir` property and support |
| `tests/test_habitat_config.py` | Added 4 new tests (43 total HabitatConfig tests) |

### Changes in habitat_config.py

- Added `KOSPEX_ASSESSMENTS_DIRNAME: 'assessments'` to DEFAULTS
- Added `assessments_dir` property returning `Path` object
- Added `KOSPEX_ASSESSMENTS` environment variable override support
- Added `assessments_dir_exists` to `validate()` result
- Added assessments directory creation in `ensure_directories()`
- Added `assessments_dir` to `get_all_paths()`

### Usage

```python
from kospex import HabitatConfig

config = HabitatConfig.get_instance()

# Get assessments directory path
assessments_path = config.assessments_dir
print(assessments_path)  # Path('~/kospex/assessments')

# Ensure directories exist (includes assessments)
config.ensure_directories()

# Validate setup
result = config.validate()
print(result['assessments_dir_exists'])  # True/False

# Override via environment variable
# export KOSPEX_ASSESSMENTS=/custom/path/assessments
```

### Testing

```bash
# Run HabitatConfig tests
pytest tests/test_habitat_config.py -v

# Run specific assessments tests
pytest tests/test_habitat_config.py -v -k assessments
```

## Phase 2: Track assessment runs in the database

**Status:** Future Work

Tasks:
- Define assessment metadata schema
- Track assessment runs with timestamps
- Link assessments to repositories
- Support krunner osi outputting to the ~/kospex/assessments
