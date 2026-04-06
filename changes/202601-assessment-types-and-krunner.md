# Assessment Types and krunner OSI Output

## Summary

Introduced a new `assessment_types.py` module to standardize assessment output filenames and updated the `krunner osi` command to output files to both the current directory and the `~/kospex/assessments` directory.

## Changes

### New Module: `/src/kospex/assessment_types.py`

Created a new module that defines:

- **AssessmentTypes class**: Provides constants for assessment type keys and utility methods
- **OSI constant**: Key for Open Source Inventory assessments (`"OSI"`)
- **generate_filename()**: Creates standardized filenames in format `{KEY}-{scope}.csv`
- **get_assessments_path()**: Returns full path to assessment file in assessments directory
- **ensure_assessments_dir()**: Ensures the assessments directory exists

### Updated: `/src/krunner.py`

Modified the `osi` command to:

1. Import `AssessmentTypes` from the new module
2. Determine scope based on `-all` flag or `request_id` parameter
3. Generate filename using `AssessmentTypes.generate_filename()`
4. Write output to both:
   - Current working directory (for backward compatibility)
   - `~/kospex/assessments/` directory (for centralized storage)

### New Tests: `/tests/test_assessment_types.py`

Comprehensive test coverage including:

- Assessment key definitions
- Filename generation with various scopes
- Path generation with HabitatConfig integration
- Directory creation functionality
- Integration tests for full workflow

## Filename Convention

Assessment files follow the naming pattern: `{KEY}-{scope}.csv`

Examples:
- `OSI-all.csv` - All repositories
- `OSI-github.com.csv` - All repos from github.com
- `OSI-github.com~kospex.csv` - All repos from github.com/kospex org
- `OSI-github.com~kospex~kospex.csv` - Specific repository

## Usage

### krunner osi command

```bash
# Run OSI for all repositories
krunner osi -all
# Output: OSI-all.csv (current dir) and ~/kospex/assessments/OSI-all.csv

# Run OSI for specific server
krunner osi github.com
# Output: OSI-github.com.csv (current dir) and ~/kospex/assessments/OSI-github.com.csv

# Run OSI for specific org
krunner osi github.com~kospex
# Output: OSI-github.com~kospex.csv (both locations)
```

### Programmatic Usage

```python
from kospex.assessment_types import AssessmentTypes

# Generate filename
filename = AssessmentTypes.generate_filename(AssessmentTypes.OSI, "all")
# Returns: "OSI-all.csv"

# Get full path
path = AssessmentTypes.get_assessments_path(AssessmentTypes.OSI, "github.com")
# Returns: ~/kospex/assessments/OSI-github.com.csv

# Ensure directory exists
AssessmentTypes.ensure_assessments_dir()
```

## Future Assessment Types

The module is designed to support additional assessment types. Future keys can be added as class constants:

```python
class AssessmentTypes:
    OSI = "OSI"  # Open Source Inventory
    # KEY_PERSON = "KEY-PERSON"
    # TECH_LANDSCAPE = "TECH-LANDSCAPE"
    # DEPENDENCIES = "DEPENDENCIES"
```

## Dependencies

- Uses `HabitatConfig` for assessments directory path (`~/kospex/assessments`)
- `HabitatConfig.assessments_dir` property was already implemented

## Testing

Run the new tests:

```bash
pytest tests/test_assessment_types.py -v
```

Verify existing tests still pass:

```bash
pytest tests/test_habitat_config.py -v
```
