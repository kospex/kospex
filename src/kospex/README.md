# Kospex Package - Modern Python Structure

This directory contains the new package-based structure for Kospex, following Python best practices.

## Structure

```
/src/
  kospex/                 # New package directory
    __init__.py           # Package initialization and public API
    git_ingest.py         # GitIngest class (git history extraction + DuckDB)
    git_duckdb.py         # GitDuckDB class (DuckDB operations)
    <future modules>      # Additional refactored modules

  kospex.py               # Legacy flat files (maintained for compatibility)
  kospex_core.py
  kospex_git.py
  ...
```

## Usage

### Importing from the new package

```python
# Import the GitIngest class
from kospex import GitIngest, GitDuckDB

# Initialize DuckDB connection
db = GitDuckDB()
db.connect(force=False, verbose=True)
db.create_schema(verbose=True)

# Extract git history
ingest = GitIngest(db)
stats = ingest.sync("/path/to/repo", verbose=True)

# Incremental sync
stats = ingest.sync("/path/to/repo", last_commit="2025-01-01T00:00:00", verbose=True)

# Close connection
db.close()
```

### Importing from legacy modules (during migration)

```python
# Old style - still works
from kospex_core import KospexCore
from kospex_git import process_git_repo
```

## Adding New Modules

When creating new modules in this package:

1. **Create the module file** in `/src/kospex/your_module.py`
2. **Add imports to `__init__.py`** to expose your classes/functions
3. **Use modern Python practices**:
   - Type hints for function signatures
   - Docstrings for classes and methods
   - Class-based design where appropriate
   - Use the logging system: `from kospex_utils import get_kospex_logger`

### Example Module Structure

```python
"""
Module docstring explaining what this module does.
"""

import sys
from pathlib import Path
from typing import Optional, Dict, List

# Import from legacy modules during migration
sys.path.insert(0, str(Path(__file__).parent.parent))
from kospex_utils import get_kospex_logger

class YourNewClass:
    """Class docstring."""

    def __init__(self, param: str):
        """Initialize with parameters."""
        self.logger = get_kospex_logger('module_name')
        self.param = param

    def process(self) -> Dict[str, Any]:
        """Process and return results."""
        self.logger.info("Processing...")
        return {"status": "success"}
```

Then in `__init__.py`:

```python
from .your_module import YourNewClass

__all__ = [
    "GitIngest",
    "YourNewClass",  # Add new exports here
    "__version__",
]
```

## Migration Strategy

1. **New features** → Build in `/src/kospex/` using modern structure
2. **Refactoring** → Gradually move code from flat files to package
3. **Compatibility** → Keep old imports working during transition
4. **Testing** → Write tests for new package structure
5. **Documentation** → Update docs as migration progresses

## Logging

All modules should use the centralized logging system:

```python
from kospex_utils import get_kospex_logger

logger = get_kospex_logger('your_module_name')
logger.info("Log message")
logger.debug("Debug info")
logger.error("Error message")
```

Log files will be created in `~/kospex/logs/` with the module name.

## Testing

When writing tests for new package modules:

```python
# In tests/test_git_ingest.py
from kospex import GitIngest

def test_git_ingest():
    ingest = GitIngest("/path/to/repo")
    assert ingest.repo_id is not None
```

## GitIngest & GitDuckDB Features

The `git_ingest` and `git_duckdb` modules provide DuckDB-based git analytics:

- **Incremental sync** - Sync only commits after a specific date
- **Duplicate handling** - Automatic `INSERT OR REPLACE` with composite key `(hash, _repo_id)`
- **Date range filtering** - Extract commits using `git log --since`
- **Branch tracking** - Track which branches contain each commit
- **Parent tracking** - Record commit parent hashes and counts (detect merges)
- **Performance optimization** - Auto-disable replacement logging for initial syncs
- **Columnar storage** - 10-100x faster GROUP BY queries vs SQLite
- **Compression** - 30-50% smaller database size

See `duckdb_poc.py` in FoundationX for complete CLI example.

## Benefits of This Approach

- ✅ No breaking changes to existing code
- ✅ New code follows Python best practices
- ✅ Gradual migration at your own pace
- ✅ Easy to import: `from kospex import GitIngest, GitDuckDB`
- ✅ Proper package structure for future growth
- ✅ Better IDE support and auto-completion
