# Tech Landscape Assessment Types

## Overview

Added two new assessment types for technology landscape reporting to the kospex assessment framework. These types support exporting technology usage data across repositories, with options for organization-wide or developer-specific views.

## New Assessment Types

Two constants were added to `AssessmentTypes`:

| Constant | Value | Description |
|----------|-------|-------------|
| `TECH_LANDSCAPE` | `"TECH-LANDSCAPE"` | Organization/repo technology landscape aggregated by language |
| `TECH_LANDSCAPE_DEV` | `"TECH-LANDSCAPE-DEV"` | Developer-specific technology landscape by author and language |

## Files Modified

### `src/kospex/assessment_types.py`

Added two new assessment type constants:

```python
TECH_LANDSCAPE = "TECH-LANDSCAPE"  # Organization/repo tech landscape (by language)
TECH_LANDSCAPE_DEV = "TECH-LANDSCAPE-DEV"  # Developer-specific tech landscape (by author + language)
```

### `src/krunner.py`

Updated the `developer-tech` command to use the new assessment types:

- Added import: `from kospex.assessment_types import AssessmentTypes`
- Uses `TECH_LANDSCAPE_DEV` when `-developers` flag or individual developer argument is provided
- Uses `TECH_LANDSCAPE` for organization-wide technology reports
- Writes output to both current directory and `~/kospex/assessments/` directory (except for individual developer reports)

### `tests/test_assessment_types.py`

Added tests for the new constants:

- `test_tech_landscape_key_defined()` - verifies `TECH_LANDSCAPE == "TECH-LANDSCAPE"`
- `test_tech_landscape_dev_key_defined()` - verifies `TECH_LANDSCAPE_DEV == "TECH-LANDSCAPE-DEV"`
- `test_generate_filename_tech_landscape()` - tests filename generation with year scoping
- `test_generate_filename_tech_landscape_dev()` - tests dev filename generation with year scoping

## Output Formats

### TECH-LANDSCAPE CSV Columns

| Column | Description |
|--------|-------------|
| `language` | Programming language/technology name |
| `commits` | Total number of commits for this technology |
| `repos` | Number of repositories using this technology |
| `files` | Number of unique files |
| `years_active` | Number of years with activity |
| `last_commit` | Date of most recent commit |

### TECH-LANDSCAPE-DEV CSV Columns

Same columns as `TECH-LANDSCAPE`, but includes developer-level breakdown when `-developers` flag is used.

## Usage Examples

### Organization-wide technology landscape

```bash
# Display technology summary
krunner developer-tech

# Export to CSV (writes TECH-LANDSCAPE-all.csv)
krunner developer-tech -csv

# Export for specific year
krunner developer-tech -csv -year 2024
# Writes: TECH-LANDSCAPE-all-2024.csv
```

### Developer-specific technology landscape

```bash
# Export all developers' technology usage
krunner developer-tech -developers -csv
# Writes: TECH-LANDSCAPE-DEV-all.csv

# Export specific developer's technology
krunner developer-tech developer@example.com -csv
# Writes: TECH-LANDSCAPE-DEV-all.csv (to current directory only)
```

## File Naming Convention

Output files follow the standard assessment naming pattern:

```
{ASSESSMENT_TYPE}-{scope}.csv
```

Examples:
- `TECH-LANDSCAPE-all.csv` - All technology across all repos
- `TECH-LANDSCAPE-all-2024.csv` - Technology for year 2024
- `TECH-LANDSCAPE-DEV-all.csv` - Developer-level technology breakdown

## Assessments Directory Behavior

- Organization-wide reports (`TECH_LANDSCAPE`) are saved to both current directory and `~/kospex/assessments/`
- Developer breakdown reports (`TECH_LANDSCAPE_DEV` with `-developers`) are saved to both locations
- Individual developer reports (specific email argument) are saved to current directory only, with a warning message
