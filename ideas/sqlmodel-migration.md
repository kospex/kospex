# SQLModel Migration Plan for Kospex

## Overview

This document outlines the plan to gradually migrate Kospex from a flat `/src/` structure to a namespaced `/src/kospex/` structure, starting with SQLModel datamodels. The goal is to modernize the codebase while maintaining backward compatibility.

## Current State Analysis

- **29 Python files** in `/src/` directory
- **Extensive cross-dependencies** with relative imports like `from kospex_core import Kospex`
- **Consistent naming convention** with `kospex_` prefix
- **Multiple CLI entry points** defined in pyproject.toml
- **SQLite-based schema** defined in `kospex_schema.py` with 14+ tables

### Key Tables Identified
- commits, commit_files, file_metadata, repos, repo_hotspots
- commit_metadata, branches, branch_history, dependency_data
- krunner, url_cache, observations, kospex_meta, kospex_config, kospex_groups

## Migration Strategy: Gradual Namespace Transition

### Phase 1: Create Initial Namespace Structure
```
src/
├── kospex/
│   ├── __init__.py
│   └── datamodels/
│       ├── __init__.py
│       └── models.py      # SQLModel definitions
├── kospex_*.py           # Existing files (unchanged)
└── ...
```

### Phase 2: Start with Datamodels (First Step)

#### Create Core Files

**`src/kospex/__init__.py`**
```python
"""Kospex - Code and Developer Analytics"""
__version__ = "0.0.26"
```

**`src/kospex/datamodels/__init__.py`**
```python
"""Kospex SQLModel data models"""
from .models import (
    Commit,
    CommitFile,
    FileMetadata,
    Repository,
    RepoHotspot,
    CommitMetadata,
    Branch,
    BranchHistory,
    DependencyData,
    KrunnerData,
    UrlCache,
    Observation,
    KospexMeta,
    KospexConfig,
    Group,
    Mailmap
)

__all__ = [
    "Commit",
    "CommitFile",
    "FileMetadata",
    "Repository",
    "RepoHotspot",
    "CommitMetadata",
    "Branch",
    "BranchHistory",
    "DependencyData",
    "KrunnerData",
    "UrlCache",
    "Observation",
    "KospexMeta",
    "KospexConfig",
    "Group",
    "Mailmap"
]
```

**`src/kospex/datamodels/models.py`**
```python
"""SQLModel definitions for Kospex database tables"""
from typing import Optional
from sqlmodel import SQLModel, Field
from datetime import datetime


class Commit(SQLModel, table=True):
    """Git commit data model"""
    __tablename__ = "commits"

    hash: str = Field(primary_key=True)
    author_email: str
    author_name: str
    author_when: datetime
    committer_email: str
    committer_name: str
    committer_when: datetime
    message: str
    summary: str
    parents: int
    tree: str
    additions: int
    deletions: int
    files_changed: int
    insertions: int
    _repo_id: str
    _git_server: str


class CommitFile(SQLModel, table=True):
    """Files changed in commits"""
    __tablename__ = "commit_files"

    id: Optional[int] = Field(default=None, primary_key=True)
    _repo_id: str
    _git_server: str
    hash: str
    filename: str
    additions: int
    deletions: int
    status: str


class FileMetadata(SQLModel, table=True):
    """File metadata and complexity metrics"""
    __tablename__ = "file_metadata"

    id: Optional[int] = Field(default=None, primary_key=True)
    _repo_id: str
    _git_server: str
    filename: str
    file_type: Optional[str] = None
    lines: Optional[int] = None
    code: Optional[int] = None
    comments: Optional[int] = None
    blanks: Optional[int] = None
    complexity: Optional[int] = None
    bytes: Optional[int] = None
    hash: str
    committer_when: datetime
    author_email: str
    committer_email: str


class Repository(SQLModel, table=True):
    """Repository metadata"""
    __tablename__ = "repos"

    _repo_id: str = Field(primary_key=True)
    _git_server: str
    name: str
    private: bool
    clone_url: str
    default_branch: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    size: Optional[int] = None
    star_count: Optional[int] = None
    fork_count: Optional[int] = None
    open_issues_count: Optional[int] = None


class RepoHotspot(SQLModel, table=True):
    """Repository hotspot analysis"""
    __tablename__ = "repo_hotspots"

    id: Optional[int] = Field(default=None, primary_key=True)
    _repo_id: str
    _git_server: str
    entity: str
    n_revs: int
    code: int
    n_authors: int
    n_minor: int
    n_major: int
    n_refactor: int


class CommitMetadata(SQLModel, table=True):
    """Additional commit metadata"""
    __tablename__ = "commit_metadata"

    id: Optional[int] = Field(default=None, primary_key=True)
    _repo_id: str
    _git_server: str
    hash: str
    key: str
    value: str


class Branch(SQLModel, table=True):
    """Git branch information"""
    __tablename__ = "branches"

    id: Optional[int] = Field(default=None, primary_key=True)
    _repo_id: str
    _git_server: str
    name: str
    hash: str
    type: str
    remote: str
    pushed_at: Optional[datetime] = None
    ahead: Optional[int] = None
    behind: Optional[int] = None


class BranchHistory(SQLModel, table=True):
    """Historical branch data"""
    __tablename__ = "branch_history"

    id: Optional[int] = Field(default=None, primary_key=True)
    _repo_id: str
    _git_server: str
    branch_name: str
    hash: str
    type: str
    remote: str
    pushed_at: Optional[datetime] = None
    ahead: Optional[int] = None
    behind: Optional[int] = None
    created_at: datetime


class DependencyData(SQLModel, table=True):
    """Software composition analysis data"""
    __tablename__ = "dependency_data"

    id: Optional[int] = Field(default=None, primary_key=True)
    _repo_id: str
    _git_server: str
    filename: str
    filetype: str
    language: str
    ecosystem: str
    package_name: str
    current_version: Optional[str] = None
    latest_version: Optional[str] = None
    is_outdated: bool
    is_deprecated: bool
    hash: str
    committer_when: datetime
    author_email: str
    committer_email: str


class KrunnerData(SQLModel, table=True):
    """Batch processing run data"""
    __tablename__ = "krunner"

    id: Optional[int] = Field(default=None, primary_key=True)
    _repo_id: str
    _git_server: str
    start_time: datetime
    end_time: datetime
    duration: float
    status: str
    message: Optional[str] = None


class UrlCache(SQLModel, table=True):
    """URL response caching"""
    __tablename__ = "url_cache"

    id: Optional[int] = Field(default=None, primary_key=True)
    url: str
    response_code: int
    response_text: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    headers: Optional[str] = None


class Observation(SQLModel, table=True):
    """General observations and metrics"""
    __tablename__ = "observations"

    id: Optional[int] = Field(default=None, primary_key=True)
    _repo_id: str
    _git_server: str
    observation_type: str
    category: str
    data: str
    value: Optional[float] = None
    created_at: datetime


class KospexMeta(SQLModel, table=True):
    """Kospex system metadata"""
    __tablename__ = "kospex_meta"

    id: Optional[int] = Field(default=None, primary_key=True)
    key: str
    value: str
    created_at: datetime
    updated_at: datetime


class KospexConfig(SQLModel, table=True):
    """Kospex configuration settings"""
    __tablename__ = "kospex_config"

    id: Optional[int] = Field(default=None, primary_key=True)
    key: str
    value: str
    description: Optional[str] = None


class Group(SQLModel, table=True):
    """Repository grouping"""
    __tablename__ = "kospex_groups"

    id: Optional[int] = Field(default=None, primary_key=True)
    _repo_id: str
    _git_server: str
    group_name: str
    group_type: str


class Mailmap(SQLModel, table=True):
    """Git mailmap for author identity resolution"""
    __tablename__ = "mailmaps"

    id: Optional[int] = Field(default=None, primary_key=True)
    proper_name: str
    proper_email: str
    commit_name: Optional[str] = None
    commit_email: Optional[str] = None
```

#### Update Dependencies

Add SQLModel to `pyproject.toml`:
```toml
dependencies = [
    # ... existing dependencies ...
    "sqlmodel>=0.0.8",  # Add this
]
```

#### Backward Compatibility Bridge

Update `kospex_schema.py` to provide backward compatibility:
```python
# At the top of kospex_schema.py, add:
try:
    from kospex.datamodels import (
        Commit, CommitFile, FileMetadata, Repository,
        # ... other models
    )
    SQLMODEL_AVAILABLE = True
except ImportError:
    SQLMODEL_AVAILABLE = False

# Keep all existing constants and SQL create statements
# Add new functions to bridge old and new:

def get_sqlmodel_classes():
    """Get SQLModel classes if available"""
    if SQLMODEL_AVAILABLE:
        from kospex.datamodels import models
        return models
    return None
```

### Phase 3: Future Migration Phases

**Phase 2**: Gradually update core modules to use namespace imports
**Phase 3**: Move utility functions to `src/kospex/utils/`
**Phase 4**: Move query logic to `src/kospex/query/`
**Phase 5**: Move web components to `src/kospex/web/`

### Final Structure Vision
```
src/
├── kospex/
│   ├── __init__.py
│   ├── datamodels/
│   │   ├── __init__.py
│   │   └── models.py
│   ├── core/
│   │   ├── __init__.py
│   │   └── kospex.py
│   ├── query/
│   │   ├── __init__.py
│   │   └── query.py
│   ├── utils/
│   │   ├── __init__.py
│   │   └── utils.py
│   └── web/
│       ├── __init__.py
│       └── routes.py
└── legacy files (gradually deprecated)
```

## Implementation Notes

### Current Import Patterns Identified
Heavy use of patterns like:
- `from kospex_core import Kospex`
- `import kospex_utils as KospexUtils`
- `from kospex_query import KospexQuery`

### Backward Compatible Usage
```python
# New style (gradually adopt)
from kospex.datamodels import Commit, Repository

# Old style (maintain compatibility)
import kospex_schema as KospexSchema
```

## Benefits of SQLModel Migration

1. **Type Safety**: SQLModel provides Pydantic validation and typing
2. **Modern ORM**: Built on SQLAlchemy 2.0 with async support
3. **API Integration**: Native FastAPI integration for web endpoints
4. **Code Generation**: Automatic API schema generation
5. **Validation**: Built-in data validation and serialization

## Next Steps

1. Create the namespace directory structure
2. Implement the datamodels with SQLModel
3. Add backward compatibility bridge in existing schema file
4. Test integration with existing code
5. Gradually migrate other modules to use new structure

## Considerations

- **Maintain CLI compatibility**: All existing commands must continue working
- **Database compatibility**: SQLModel should work with existing SQLite database
- **Import compatibility**: Existing imports should continue working during transition
- **Testing**: Ensure all tests pass with new structure
- **Documentation**: Update documentation as migration progresses

---

*Created: 2025-01-09*
*Status: Planning Phase*
*Priority: Medium*