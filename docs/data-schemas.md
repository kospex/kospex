# Kospex Data Schemas

This document describes the database schemas used by Kospex for storing git repository analytics data.

Kospex uses two database systems:
- **SQLite** (`kospex.db`) - Primary database for all kospex data
- **DuckDB** (`kospex-git.duckdb`) - High-performance analytics database for git commit data

## Database Locations

| Database | Default Path | Environment Variable |
|----------|--------------|----------------------|
| SQLite | `~/kospex/kospex.db` | `KOSPEX_HOME` |
| DuckDB | `~/kospex/kospex-git.duckdb` | `KOSPEX_HOME` |

---

## DuckDB Schema

The DuckDB database is optimized for high-performance analytics on git commit and file change data. It contains two tables focused on git history analysis.

Source: `/src/kospex/git_duckdb.py`

### commits

Stores git commit metadata. Schema inspired by [Mergestat git-commits sync](https://github.com/mergestat/syncs/blob/main/syncs/git-commits/schema.sql).

| Column | Type | Description |
|--------|------|-------------|
| `hash` | VARCHAR | Git commit hash |
| `author_email` | VARCHAR | Email of the commit author |
| `author_name` | VARCHAR | Name of the commit author |
| `author_when` | TIMESTAMP | Timestamp when authored |
| `committer_email` | VARCHAR | Email of the committer |
| `committer_name` | VARCHAR | Name of the committer |
| `committer_when` | TIMESTAMP | Timestamp when committed |
| `message` | VARCHAR | Commit message |
| `parents` | VARCHAR | Parent commit hashes |
| `parent_count` | INTEGER | Number of parent commits |
| `branches` | VARCHAR | Branch names containing this commit |
| `branch_count` | INTEGER | Number of branches |
| `_git_server` | VARCHAR | Git server (e.g., github.com) |
| `_git_owner` | VARCHAR | Repository owner/organization |
| `_git_repo` | VARCHAR | Repository name |
| `_repo_id` | VARCHAR | Unique repository identifier |
| `_files` | INTEGER | Number of files changed |
| `_cycle_time` | INTEGER | Time between author and commit (seconds) |

**Primary Key:** `(hash, _repo_id)`

**Indexes:**
- `idx_commits_when` on `committer_when`
- `idx_commits_repo` on `_repo_id`

### commit_files

Stores file-level changes per commit. Schema inspired by [Mergestat git-commit-stats sync](https://github.com/mergestat/syncs/blob/main/syncs/git-commit-stats/schema.sql).

| Column | Type | Description |
|--------|------|-------------|
| `hash` | VARCHAR | Git commit hash |
| `file_path` | VARCHAR | Path to the file |
| `_ext` | VARCHAR | File extension |
| `additions` | INTEGER | Lines added |
| `deletions` | INTEGER | Lines deleted |
| `committer_when` | TIMESTAMP | Timestamp when committed |
| `path_change` | VARCHAR | Raw git change path |
| `_git_server` | VARCHAR | Git server |
| `_git_owner` | VARCHAR | Repository owner/organization |
| `_git_repo` | VARCHAR | Repository name |
| `_repo_id` | VARCHAR | Unique repository identifier |

**Primary Key:** `(hash, file_path, _repo_id)`

**Indexes:**
- `idx_files_path` on `file_path`

---

## SQLite Schema

The SQLite database is the primary data store for all kospex data including commits, file metadata, dependencies, and configuration.

Source: `/src/kospex_schema.py`

**Current Schema Version:** 2

### Core Tables

#### commits

Git commit history data.

| Column | Type | Description |
|--------|------|-------------|
| `hash` | TEXT | Git commit hash |
| `author_email` | TEXT | Email of the commit author |
| `author_name` | TEXT | Name of the commit author |
| `author_when` | TEXT | Timestamp when authored |
| `committer_email` | TEXT | Email of the committer |
| `committer_name` | TEXT | Name of the committer |
| `committer_when` | TEXT | Timestamp when committed |
| `message` | TEXT | Commit message |
| `parents` | INTEGER | Number of parent commits |
| `_git_server` | TEXT | Git server |
| `_git_owner` | TEXT | Repository owner/organization |
| `_git_repo` | TEXT | Repository name |
| `_repo_id` | TEXT | Unique repository identifier |
| `_files` | INTEGER | Number of files changed |
| `_cycle_time` | INTEGER | Time between author and commit (seconds) |

**Primary Key:** `(_repo_id, hash)`

#### commit_files

File changes per commit.

| Column | Type | Description |
|--------|------|-------------|
| `hash` | TEXT | Git commit hash |
| `file_path` | TEXT | Path to the file |
| `_ext` | TEXT | File extension |
| `additions` | INTEGER | Lines added |
| `deletions` | INTEGER | Lines deleted |
| `committer_when` | TEXT | Timestamp when committed |
| `path_change` | TEXT | Raw git change path |
| `_git_server` | TEXT | Git server |
| `_git_owner` | TEXT | Repository owner/organization |
| `_git_repo` | TEXT | Repository name |
| `_repo_id` | TEXT | Unique repository identifier |

**Primary Key:** `(hash, file_path, _repo_id)`

#### repos

Repository metadata and sync status.

| Column | Type | Description |
|--------|------|-------------|
| `_repo_id` | TEXT | Unique repository identifier |
| `_git_server` | TEXT | Git server |
| `_git_owner` | TEXT | Repository owner/organization |
| `_git_repo` | TEXT | Repository name |
| `created_at` | TIMESTAMP | When record was created (default: CURRENT_TIMESTAMP) |
| `last_sync` | TEXT | Date of last kospex sync |
| `last_seen` | TEXT | Date of last commit |
| `first_seen` | TEXT | Date of first commit |
| `git_remote` | TEXT | URL of the git repository |
| `file_path` | TEXT | Path to repo on local filesystem |

**Primary Key:** `(_repo_id)`

### File Analysis Tables

#### file_metadata

File metadata from external tools (primarily `scc`).

| Column | Type | Description |
|--------|------|-------------|
| `Language` | TEXT | Detected programming language |
| `Provider` | TEXT | File location/path |
| `Filename` | TEXT | Filename |
| `Lines` | INTEGER | Total number of lines |
| `Code` | INTEGER | Lines of code |
| `Comments` | INTEGER | Lines of comments |
| `Blanks` | INTEGER | Blank lines |
| `Complexity` | INTEGER | Cyclomatic complexity |
| `Bytes` | INTEGER | File size in bytes |
| `hash` | TEXT | Commit hash when metadata captured |
| `tech_type` | TEXT | Technology type tags |
| `latest` | INTEGER | 1 if latest version, 0 otherwise |
| `authors` | INTEGER | Number of authors who modified this file |
| `commits` | INTEGER | Number of commits to this file |
| `_mtime` | INTEGER | Last modified time |
| `committer_when` | TEXT | Date of last commit |
| `_metadata_source` | TEXT | Tool used to get metadata |
| `_git_server` | TEXT | Git server |
| `_git_owner` | TEXT | Repository owner/organization |
| `_git_repo` | TEXT | Repository name |
| `_repo_id` | TEXT | Unique repository identifier |

**Primary Key:** `(Provider, hash, _repo_id)`

#### repo_hotspots

Hotspot indicators for repositories.

| Column | Type | Description |
|--------|------|-------------|
| `commits` | INTEGER | Number of commits |
| `authors` | INTEGER | Number of authors |
| `files` | INTEGER | Number of distinct files |
| `loc` | INTEGER | Total lines of code |
| `first_seen` | TEXT | Date of first commit |
| `last_seen` | TEXT | Date of last commit |
| `hash` | TEXT | Commit hash |
| `_git_server` | TEXT | Git server |
| `_git_owner` | TEXT | Repository owner/organization |
| `_git_repo` | TEXT | Repository name |
| `_repo_id` | TEXT | Unique repository identifier |

**Primary Key:** `(_repo_id, hash)`

### Dependency Analysis Tables

#### dependency_data

Software composition analysis (SCA) data.

| Column | Type | Description |
|--------|------|-------------|
| `hash` | TEXT | Commit hash |
| `file_path` | TEXT | Path to dependency file |
| `package_type` | TEXT | Package ecosystem (e.g., PyPi, maven) |
| `package_name` | TEXT | Name of the package |
| `package_version` | TEXT | Version of the package |
| `package_use` | TEXT | Usage type (direct, development, testing) |
| `published_at` | TEXT | Package publication date |
| `advisories` | INTEGER | Number of security advisories |
| `versions_behind` | INTEGER | How many versions behind latest |
| `source_repo` | TEXT | Source repository URL |
| `default` | TEXT | If this is the default version |
| `source` | TEXT | Tool used to get metadata |
| `latest` | INTEGER | 1 if latest entry, 0 otherwise |
| `created_at` | TIMESTAMP | When record was created |
| `_git_server` | TEXT | Git server |
| `_git_owner` | TEXT | Repository owner/organization |
| `_git_repo` | TEXT | Repository name |
| `_repo_id` | TEXT | Unique repository identifier |

**Primary Key:** `(_repo_id, hash, file_path, package_type, package_name, package_version)`

### Branch Tables

#### branches

Current branch data for repositories.

| Column | Type | Description |
|--------|------|-------------|
| `count` | INTEGER | Number of branches |
| `branch_data` | TEXT | JSON array of branch names |
| `hash` | TEXT | Current commit hash |
| `_repo_id` | TEXT | Unique repository identifier |
| `_git_server` | TEXT | Git server |
| `_git_owner` | TEXT | Repository owner/organization |
| `_git_repo` | TEXT | Repository name |
| `created_at` | TIMESTAMP | When record was created |
| `last_sync` | TEXT | Date of last sync |
| `last_seen` | TEXT | Date of last commit |

**Primary Key:** `(_repo_id)`

#### branch_history

Historical branch data for repositories.

| Column | Type | Description |
|--------|------|-------------|
| `count` | INTEGER | Number of branches |
| `branch_data` | TEXT | JSON array of branch names |
| `hash` | TEXT | Commit hash |
| `_repo_id` | TEXT | Unique repository identifier |
| `_git_server` | TEXT | Git server |
| `_git_owner` | TEXT | Repository owner/organization |
| `_git_repo` | TEXT | Repository name |
| `created_at` | TIMESTAMP | When record was created |
| `last_sync` | TEXT | Date of last sync |
| `last_seen` | TEXT | Date of last commit |

**Primary Key:** `(_repo_id, hash)`

### Metadata and Observations Tables

#### commit_metadata

Additional metadata about commits.

| Column | Type | Description |
|--------|------|-------------|
| `hash` | TEXT | Commit hash |
| `name` | TEXT | Metadata name (e.g., 'directory_size') |
| `value` | TEXT | Metadata value |
| `source` | TEXT | Tool used to get metadata |
| `_git_server` | TEXT | Git server |
| `_git_owner` | TEXT | Repository owner/organization |
| `_git_repo` | TEXT | Repository name |
| `_repo_id` | TEXT | Unique repository identifier |

**Primary Key:** None defined

#### observations

Tool observations and analysis results.

| Column | Type | Description |
|--------|------|-------------|
| `uuid` | TEXT | Unique identifier for the observation |
| `hash` | TEXT | Commit hash |
| `file_path` | TEXT | File path (optional for repo-level observations) |
| `format` | TEXT | Data format (JSON, JSONL, CSV, LINE) |
| `data` | TEXT | Cleaned data/output |
| `raw` | TEXT | Raw data/output |
| `source` | TEXT | Tool/function that generated the observation |
| `observation_key` | TEXT | Unique key (e.g., SEMGREP, GREP_TODO) |
| `observation_type` | TEXT | REPO or FILE |
| `line_number` | INTEGER | Line number in file (optional) |
| `command` | TEXT | Command ran (optional) |
| `latest` | INTEGER | 1 if latest version, 0 otherwise |
| `created_at` | TIMESTAMP | When record was created |
| `_git_server` | TEXT | Git server |
| `_git_owner` | TEXT | Repository owner/organization |
| `_git_repo` | TEXT | Repository name |
| `_repo_id` | TEXT | Unique repository identifier |

**Primary Key:** `(_repo_id, hash, file_path, observation_key, latest)`

#### krunner

Command output storage from batch processing.

| Column | Type | Description |
|--------|------|-------------|
| `hash` | TEXT | Commit hash |
| `file_path` | TEXT | File path in repo |
| `format` | TEXT | Data format (JSON, JSONL, CSV, LINE) |
| `data` | TEXT | Output data |
| `source` | TEXT | Tool/function that generated data |
| `command` | TEXT | Command that was run (optional) |
| `created_at` | TIMESTAMP | When record was created |
| `_git_server` | TEXT | Git server |
| `_git_owner` | TEXT | Repository owner/organization |
| `_git_repo` | TEXT | Repository name |
| `_repo_id` | TEXT | Unique repository identifier |

**Primary Key:** `(_repo_id, hash, file_path)`

### Identity Management Tables

#### email_map

Maps email aliases to canonical email addresses.

| Column | Type | Description |
|--------|------|-------------|
| `alias_email` | TEXT | Email alias |
| `main_email` | TEXT | Canonical email address |
| `created_at` | TIMESTAMP | When record was created |
| `notes` | TEXT | Additional notes |
| `source` | TEXT | Where mapping came from (CLI, filesystem, URL) |

**Primary Key:** `(alias_email)`

### Configuration Tables

#### kospex_config

Key-value configuration storage.

| Column | Type | Description |
|--------|------|-------------|
| `format` | TEXT | Value format (JSON, JSONL, CSV, TEXT, INTEGER, YAML) |
| `key` | TEXT | Configuration key |
| `value` | TEXT | Configuration value |
| `latest` | INTEGER | 1 if latest version, 0 otherwise |
| `created_at` | TIMESTAMP | When record was created |
| `updated_at` | TIMESTAMP | When record was updated |

**Primary Key:** `(key, latest)`

#### kospex_groups

Grouping configuration for repositories and users.

| Column | Type | Description |
|--------|------|-------------|
| `group_name` | TEXT | Name of the group |
| `git_url` | TEXT | Repository URL (optional) |
| `email` | TEXT | Email address (optional) |
| `data` | TEXT | Additional data |
| `data_type` | TEXT | Type of data (git_url, email, other) |
| `created_at` | TIMESTAMP | When record was created |
| `_repo_id` | TEXT | Normalized repository identifier |

**Primary Key:** `(group_name, _repo_id, email)`

### Utility Tables

#### url_cache

HTTP response caching.

| Column | Type | Description |
|--------|------|-------------|
| `url` | TEXT | Cached URL |
| `content` | TEXT | Cached content |
| `timestamp` | REAL | Cache timestamp |

**Primary Key:** `(url)`

### Views

#### commits_view

Joins commits with email_map to provide canonical email addresses.

```sql
SELECT
    c.*,
    COALESCE(m.main_email, c.author_email) as canonical_email
FROM commits c
LEFT JOIN email_map m ON c.author_email = m.alias_email;
```

---

## Repository Identifier Format

The `_repo_id` field uses the format: `GIT_SERVER~OWNER~REPO`

Examples:
- `github.com~kospex~kospex`
- `bitbucket.org~myorg~myrepo`

This format is used consistently across both databases and all tables containing repository data.

## Tables with Repository Context

The following tables include `_repo_id` for repository-scoped queries:

- `commits`
- `commit_files`
- `commit_metadata`
- `file_metadata`
- `repo_hotspots`
- `dependency_data`
- `krunner`
- `observations`
- `repos`
- `kospex_groups`
- `branches`
- `branch_history`
