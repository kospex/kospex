# Repo Sync Refactor - July 2025

## Overview

This document outlines the refactoring approach for extracting the `sync_repo` method from `/src/kospex_core.py` into a separate `/src/repo_sync.py` file. The goal is to create a more modular, testable, and maintainable repository synchronization system while using a separate database to avoid breaking existing functionality.

## Current Implementation Analysis

### sync_repo Method (kospex_core.py:149-284)

**Current Functionality:**
- Syncs git commit data and file changes to `commits` and `commit_files` tables
- Supports incremental synchronization using last commit datetime
- Handles git log parsing with numstat for file change details
- Manages git rename events and file path changes
- Updates repository status and metadata
- Optional scc integration for file metadata

**Key Dependencies:**
- `KospexGit` - Git repository operations
- `KospexSchema` - Database schema and table operations
- `sqlite_utils.Database` - Database connectivity
- `subprocess` - Git command execution

**Git Command Used:**
```bash
git log --pretty=format:%H#%aI#%cI#%aN#%aE#%cN#%cE --numstat [--since=DATE] [--until=DATE] [-n LIMIT]
```

**Data Flow:**
1. Set repository directory and change working directory
2. Get latest commit datetime from database (for incremental sync)
3. Execute git log command with appropriate filters
4. Parse output line by line to extract commit and file information
5. Upsert commits to `commits` table
6. Upsert file changes to `commit_files` table
7. Update repository status with sync timestamp

### Database Schema Analysis

**commits Table (SQL_CREATE_COMMITS):**
```sql
PRIMARY KEY(_repo_id, hash)
Fields: hash, author_email, author_name, author_when, committer_email, 
        committer_name, committer_when, message, parents, _git_server,
        _git_owner, _git_repo, _repo_id, _files, _cycle_time
```

**commit_files Table (SQL_CREATE_COMMIT_FILES):**
```sql
PRIMARY KEY(hash, file_path, _repo_id)
Fields: hash, file_path, _ext, additions, deletions, committer_when,
        path_change, _git_server, _git_owner, _git_repo, _repo_id
```

**Incremental Sync Logic:**
- Uses `get_latest_commit_datetime(repo_id)` to find most recent commit in database
- Applies `--since` parameter to git log command for incremental updates
- Handles upsert operations to prevent duplicates

## Proposed Refactored Architecture

### 1. File Structure
```
/src/repo_sync.py
  ├── RepoSyncConfig     # Configuration management
  ├── GitLogParser       # Git log output parsing
  ├── RepoSyncDatabase   # Database operations
  └── RepoSync          # Main synchronization orchestrator
```

### 2. Class Definitions

#### RepoSyncConfig
```python
class RepoSyncConfig:
    """Configuration management for repository synchronization"""
    
    def __init__(self, db_path=None, use_scc=True):
        self.db_path = db_path or "~/kospex/repo_sync_spike.db"
        self.use_scc = use_scc
        self.batch_size = 500  # For progress reporting
        self.timeout = 300     # Git command timeout
    
    @classmethod
    def from_dict(cls, config_dict):
        """Create config from dictionary"""
    
    def to_dict(self):
        """Export config to dictionary"""
```

#### GitLogParser
```python
class GitLogParser:
    """Parse git log output into structured commit and file data"""
    
    @staticmethod
    def parse_log_output(git_output):
        """Parse git log --numstat output into commits list"""
        
    @staticmethod
    def parse_commit_line(line):
        """Parse commit header line with format: hash#author_date#..."""
        
    @staticmethod
    def parse_file_line(line):
        """Parse file change line with format: additions\tdeletions\tfilename"""
        
    @staticmethod
    def handle_rename_event(filename):
        """Handle git rename events (old => new format)"""
```

#### RepoSyncDatabase
```python
class RepoSyncDatabase:
    """Handle database operations for repository synchronization"""
    
    def __init__(self, db_path):
        self.db_path = db_path
        self.db = None
    
    def connect(self):
        """Connect to database and create tables if needed"""
        
    def get_latest_commit_datetime(self, repo_id):
        """Get latest commit datetime for incremental sync"""
        
    def upsert_commits(self, commits_data):
        """Upsert commit data to commits table"""
        
    def upsert_commit_files(self, commit_files_data):
        """Upsert commit file data to commit_files table"""
        
    def update_repo_status(self, repo_data):
        """Update repository status and metadata"""
        
    def close(self):
        """Close database connection"""
```

#### RepoSync
```python
class RepoSync:
    """Main repository synchronization orchestrator"""
    
    def __init__(self, config=None):
        self.config = config or RepoSyncConfig()
        self.db = RepoSyncDatabase(self.config.db_path)
        self.git = KospexGit()
        self.parser = GitLogParser()
    
    def sync_repository(self, repo_path, **kwargs):
        """Main synchronization method"""
        
    def _build_git_command(self, from_date, to_date, limit):
        """Build git log command with appropriate filters"""
        
    def _execute_git_command(self, cmd):
        """Execute git command and return output"""
        
    def _process_commits(self, commits_data, repo_id):
        """Process and insert commit data to database"""
        
    def _display_progress(self, count, total=None):
        """Display sync progress to user"""
```

### 3. Key Improvements

**Separation of Concerns:**
- Configuration isolated in `RepoSyncConfig`
- Git parsing logic in `GitLogParser`
- Database operations in `RepoSyncDatabase`
- Orchestration in `RepoSync`

**Error Handling:**
- Explicit error handling for git command failures
- Database connection error handling
- Validation of repository paths and git status

**Testing Support:**
- Mockable database operations
- Isolated git parsing logic
- Configurable components

**Extensibility:**
- Plugin architecture for different git log formats
- Configurable database backends
- Customizable progress reporting

## Implementation Plan

### Phase 1: Core Infrastructure
1. Create `RepoSyncConfig` class with basic configuration management
2. Create `GitLogParser` with current parsing logic extracted
3. Create `RepoSyncDatabase` with table creation and basic operations
4. Set up separate SQLite database for spike testing

### Phase 2: Main Synchronization Logic
1. Create `RepoSync` class with extracted sync logic
2. Implement incremental sync functionality
3. Add progress reporting and error handling
4. Maintain compatibility with existing KospexGit integration

### Phase 3: Testing and Validation
1. Create CLI interface for testing the refactored sync
2. Test incremental sync with various repository states
3. Validate data integrity compared to original implementation
4. Performance testing and optimization

### Phase 4: Integration
1. Optional integration back into main kospex CLI
2. Migration path for existing databases
3. Documentation and usage examples

## Database Isolation Strategy

**Separate Database File:**
- Location: `~/kospex/repo_sync_spike.db`
- Same table schemas as original (SQL_CREATE_COMMITS, SQL_CREATE_COMMIT_FILES)
- Independent of main kospex database
- Allows safe experimentation without affecting existing data

**Schema Reuse:**
- Import table creation statements from `kospex_schema.py`
- Maintain same primary key constraints for upsert operations
- Ensure compatibility with existing query patterns

## Benefits of Refactoring

**Modularity:**
- Clear separation between git operations, parsing, and database work
- Easier to unit test individual components
- Better code organization and maintainability

**Reliability:**
- Isolated database prevents breaking existing functionality
- Better error handling and recovery
- More predictable behavior

**Extensibility:**
- Easy to add new sync strategies
- Support for different database backends
- Plugin architecture for custom parsing

**Performance:**
- Opportunity for optimization without affecting main codebase
- Better memory management for large repositories
- Configurable batch processing

## Risk Mitigation

**Data Safety:**
- Separate database eliminates risk to production data
- Comprehensive testing before integration
- Validation against original implementation

**Functionality Preservation:**
- Maintain all existing sync features
- Preserve incremental sync behavior
- Keep same CLI interface patterns

**Development Velocity:**
- Incremental implementation approach
- Early testing and validation
- Minimal disruption to existing development

## Success Criteria

1. **Functional Parity:** New implementation produces identical results to original
2. **Performance:** No significant performance degradation
3. **Reliability:** Improved error handling and recovery
4. **Maintainability:** Cleaner, more testable code structure
5. **Isolation:** Zero impact on existing kospex functionality

## Testing Interface

A comprehensive CLI testing interface is provided in `test_repo_sync.py`:

### Basic Usage
```bash
# Sync entire repository
python test_repo_sync.py /path/to/repo

# Sync with limit
python test_repo_sync.py /path/to/repo --limit 10

# Incremental sync from specific date
python test_repo_sync.py /path/to/repo --from-date 2024-01-01

# Date range sync
python test_repo_sync.py /path/to/repo --from-date 2024-01-01 --to-date 2024-12-31

# Custom database location
python test_repo_sync.py /path/to/repo --db-path /custom/path/test.db

# Verbose output with database info
python test_repo_sync.py /path/to/repo --verbose
```

### Configuration Options
- `--batch-size`: Control commit processing batch size (default: 500)
- `--progress-interval`: Adjust progress indicator frequency (default: 80)
- `--no-scc`: Disable scc file metadata analysis
- `--verbose`: Enable detailed output and database statistics

## Usage Examples

### Programmatic Usage
```python
from src.repo_sync import RepoSync, RepoSyncConfig, sync_repo

# Simple convenience function
commits_synced = sync_repo("/path/to/repo")

# With custom database
commits_synced = sync_repo("/path/to/repo", db_path="/custom/spike.db")

# Advanced configuration
config = RepoSyncConfig(
    db_path="/custom/test.db",
    batch_size=1000,
    progress_interval=50
)
syncer = RepoSync(config)
commits_synced = syncer.sync_repository("/path/to/repo", limit=100)
```

### Class-based Usage
```python
# Custom configuration
config = RepoSyncConfig(
    db_path="~/kospex/custom_sync.db",
    use_scc=False,
    batch_size=250
)

# Initialize syncer
syncer = RepoSync(config)

# Sync with incremental update
commits_synced = syncer.sync_repository(
    repo_path="/path/to/repo",
    from_date="2024-01-01"
)
```

## Key Features Implemented

### ✅ Functional Parity
- **Incremental Sync**: Uses `get_latest_commit_datetime()` to sync only newer commits
- **Git Log Parsing**: Complete parsing of git log output with numstat
- **Git Rename Handling**: Properly handles rename events (`old => new` format)
- **File Extensions**: Automatic file extension detection and storage
- **Repository Metadata**: Updates repository status and sync timestamps

### ✅ Enhanced Architecture
- **Modular Design**: Four focused classes with clear responsibilities
- **Database Isolation**: Separate SQLite database prevents production impact
- **Error Handling**: Improved error handling with timeouts and validation
- **Progress Reporting**: Configurable progress indicators and batch processing
- **Type Hints**: Full type annotations for better development experience

### ✅ Testing & Development
- **CLI Interface**: Complete testing script with multiple options
- **Verbose Mode**: Detailed output and database statistics
- **Configuration**: Flexible configuration with sensible defaults
- **Batch Processing**: Configurable batch sizes for performance tuning

### ✅ Compatibility
- **Schema Reuse**: Uses existing table schemas from `kospex_schema.py`
- **KospexGit Integration**: Maintains compatibility with existing git operations
- **Upsert Logic**: Same primary key constraints and upsert behavior
- **Data Format**: Identical data structures and field mappings

## Implementation Status

**✅ COMPLETED:**
1. ✅ Planning document created
2. ✅ RepoSyncConfig class for configuration management
3. ✅ GitLogParser class for parsing git log output
4. ✅ RepoSyncDatabase class for database operations
5. ✅ RepoSync main orchestrator class
6. ✅ Separate SQLite database setup
7. ✅ Incremental sync functionality
8. ✅ CLI interface for testing (`test_repo_sync.py`)

**Ready for Testing:**
- Full end-to-end repository synchronization
- Incremental sync validation
- Performance comparison with original implementation
- Data integrity verification