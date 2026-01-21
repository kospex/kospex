"""
GitDuckDB - DuckDB database operations for git analytics.

This module provides a class for managing git commit and file data in a DuckDB database.
"""

import os
import uuid
import time
import json
import duckdb
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# ============================================================================
# CONSTANTS
# ============================================================================

DUCKDB_FILENAME = "kospex-git.duckdb"
BATCH_SIZE = 1000

SQL_CREATE_COMMITS_DUCKDB = """
CREATE TABLE IF NOT EXISTS commits (
    hash VARCHAR,
    author_email VARCHAR,
    author_name VARCHAR,
    author_when TIMESTAMP,
    committer_email VARCHAR,
    committer_name VARCHAR,
    committer_when TIMESTAMP,
    message VARCHAR,
    parents VARCHAR,
    parent_count INTEGER,
    branches VARCHAR,
    branch_count INTEGER,
    _git_server VARCHAR,
    _git_owner VARCHAR,
    _git_repo VARCHAR,
    _repo_id VARCHAR,
    _files INTEGER,
    _cycle_time INTEGER,
    PRIMARY KEY (hash, _repo_id)
)
"""

SQL_CREATE_COMMIT_FILES_DUCKDB = """
CREATE TABLE IF NOT EXISTS commit_files (
    hash VARCHAR,
    file_path VARCHAR,
    _ext VARCHAR,
    additions INTEGER,
    deletions INTEGER,
    committer_when TIMESTAMP,
    path_change VARCHAR,
    _git_server VARCHAR,
    _git_owner VARCHAR,
    _git_repo VARCHAR,
    _repo_id VARCHAR,
    PRIMARY KEY (hash, file_path, _repo_id)
)
"""

# Sync progress tracking tables
SQL_CREATE_SYNC_OPERATIONS = """
CREATE TABLE IF NOT EXISTS sync_operations (
    sync_id VARCHAR PRIMARY KEY,
    repo_id VARCHAR NOT NULL,
    started_at TIMESTAMP NOT NULL,
    completed_at TIMESTAMP,
    status VARCHAR NOT NULL,
    sync_type VARCHAR NOT NULL,
    since_date TIMESTAMP,
    commits_extracted INTEGER DEFAULT 0,
    commits_inserted INTEGER DEFAULT 0,
    files_inserted INTEGER DEFAULT 0,
    commits_replaced INTEGER DEFAULT 0,
    error_message VARCHAR,
    encoding_errors INTEGER DEFAULT 0,
    last_successful_batch INTEGER DEFAULT 0,
    resume_from_hash VARCHAR,
    git_head_hash VARCHAR,
    branch_count INTEGER
)
"""

SQL_CREATE_SYNC_PROGRESS = """
CREATE TABLE IF NOT EXISTS sync_progress (
    repo_id VARCHAR PRIMARY KEY,
    sync_id VARCHAR NOT NULL,
    phase VARCHAR NOT NULL,
    phase_started_at TIMESTAMP,
    current_batch INTEGER DEFAULT 0,
    total_batches INTEGER DEFAULT 0,
    items_processed INTEGER DEFAULT 0,
    total_items INTEGER DEFAULT 0,
    last_updated_at TIMESTAMP,
    last_hash_processed VARCHAR,
    avg_batch_duration_ms REAL DEFAULT 0
)
"""

SQL_CREATE_SYNC_METRICS = """
CREATE TABLE IF NOT EXISTS sync_metrics (
    sync_id VARCHAR NOT NULL,
    metric_type VARCHAR NOT NULL,
    metric_name VARCHAR NOT NULL,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    duration_ms INTEGER,
    items_count INTEGER,
    details VARCHAR
)
"""

# Progress tracking constants
PROGRESS_UPDATE_EVERY_N_BATCHES = 5  # Update progress DB every N batches


# ============================================================================
# SyncProgressTracker CLASS
# ============================================================================

class SyncProgressTracker:
    """Track and persist sync progress for monitoring and resume capability."""

    def __init__(self, conn, repo_id: str, sync_type: str = 'full'):
        """Initialize progress tracker.

        Args:
            conn: DuckDB connection
            repo_id: Repository identifier
            sync_type: 'full' or 'incremental'
        """
        self.conn = conn
        self.repo_id = repo_id
        self.sync_id = str(uuid.uuid4())
        self.sync_type = sync_type
        self.phase_start_time = None
        self._current_phase = None
        self.batch_times = []
        self._batch_counter = 0
        self._batch_start_time = None

    def start_sync(self, since_date: Optional[str] = None, head_hash: Optional[str] = None):
        """Initialize sync operation record.

        Args:
            since_date: ISO datetime for incremental sync start
            head_hash: Current HEAD commit hash
        """
        now = datetime.utcnow().isoformat()

        self.conn.execute("""
            INSERT INTO sync_operations
            (sync_id, repo_id, started_at, status, sync_type, since_date, git_head_hash)
            VALUES (?, ?, ?, 'running', ?, ?, ?)
        """, [self.sync_id, self.repo_id, now, self.sync_type, since_date, head_hash])

        # Initialize progress record (upsert in case previous sync left orphan)
        self.conn.execute("""
            INSERT OR REPLACE INTO sync_progress
            (repo_id, sync_id, phase, phase_started_at, last_updated_at)
            VALUES (?, ?, 'init', ?, ?)
        """, [self.repo_id, self.sync_id, now, now])

    def update_phase(self, phase: str, total_items: int = 0):
        """Update current sync phase.

        Args:
            phase: Phase name ('branches', 'extraction', 'commit_insert', 'file_insert')
            total_items: Total items to process in this phase
        """
        now = datetime.utcnow()
        now_iso = now.isoformat()

        # Record phase timing metric for previous phase
        if self.phase_start_time and self._current_phase:
            duration_ms = int((now - self.phase_start_time).total_seconds() * 1000)
            self._record_metric('phase_timing', f'phase_{self._current_phase}',
                                duration_ms=duration_ms)

        self.phase_start_time = now
        self._current_phase = phase
        self._batch_counter = 0
        self.batch_times = []

        total_batches = (total_items // BATCH_SIZE) + (1 if total_items % BATCH_SIZE else 0) if total_items else 0

        self.conn.execute("""
            UPDATE sync_progress
            SET phase = ?, phase_started_at = ?, total_items = ?,
                total_batches = ?, items_processed = 0, current_batch = 0,
                last_updated_at = ?
            WHERE repo_id = ?
        """, [phase, now_iso, total_items, total_batches, now_iso, self.repo_id])

    def update_batch_progress(self, batch_num: int, items_processed: int,
                              last_hash: Optional[str] = None):
        """Update progress after batch completion.

        Only persists to DB every N batches to reduce I/O overhead.

        Args:
            batch_num: Current batch number
            items_processed: Total items processed so far
            last_hash: Last commit hash processed (for resume)
        """
        self._batch_counter += 1
        batch_end = time.time()

        # Track batch timing for averaging
        if self._batch_start_time is not None:
            batch_duration = (batch_end - self._batch_start_time) * 1000
            self.batch_times.append(batch_duration)
            # Keep rolling window of last 20 batches for average
            if len(self.batch_times) > 20:
                self.batch_times.pop(0)

        self._batch_start_time = batch_end

        # Only update DB every N batches to reduce I/O overhead
        if self._batch_counter % PROGRESS_UPDATE_EVERY_N_BATCHES == 0:
            avg_duration = sum(self.batch_times) / len(self.batch_times) if self.batch_times else 0
            now_iso = datetime.utcnow().isoformat()

            self.conn.execute("""
                UPDATE sync_progress
                SET current_batch = ?, items_processed = ?, last_hash_processed = ?,
                    avg_batch_duration_ms = ?, last_updated_at = ?
                WHERE repo_id = ?
            """, [batch_num, items_processed, last_hash, avg_duration, now_iso, self.repo_id])

            # Also update sync_operations for resume capability
            self.conn.execute("""
                UPDATE sync_operations
                SET last_successful_batch = ?, resume_from_hash = ?
                WHERE sync_id = ?
            """, [batch_num, last_hash, self.sync_id])

    def record_encoding_error(self, details: str):
        """Record an encoding error encountered during sync.

        Args:
            details: Error details string
        """
        self._record_metric('error', 'encoding_error', details=json.dumps({'error': details}))
        self.conn.execute("""
            UPDATE sync_operations
            SET encoding_errors = encoding_errors + 1
            WHERE sync_id = ?
        """, [self.sync_id])

    def complete_sync(self, commits_inserted: int, files_inserted: int,
                      commits_replaced: int = 0, commits_extracted: int = 0,
                      branch_count: int = 0):
        """Mark sync as completed with final statistics.

        Args:
            commits_inserted: Number of commits inserted
            files_inserted: Number of file changes inserted
            commits_replaced: Number of existing commits replaced
            commits_extracted: Total commits extracted from git
            branch_count: Number of branches processed
        """
        now = datetime.utcnow().isoformat()

        # Record final phase timing
        if self.phase_start_time and self._current_phase:
            duration_ms = int((datetime.utcnow() - self.phase_start_time).total_seconds() * 1000)
            self._record_metric('phase_timing', f'phase_{self._current_phase}',
                                duration_ms=duration_ms)

        self.conn.execute("""
            UPDATE sync_operations
            SET status = 'completed', completed_at = ?,
                commits_extracted = ?, commits_inserted = ?,
                files_inserted = ?, commits_replaced = ?,
                branch_count = ?
            WHERE sync_id = ?
        """, [now, commits_extracted, commits_inserted, files_inserted,
              commits_replaced, branch_count, self.sync_id])

        # Clean up progress record
        self.conn.execute("DELETE FROM sync_progress WHERE repo_id = ?", [self.repo_id])

    def fail_sync(self, error_message: str):
        """Mark sync as failed with error details.

        Args:
            error_message: Error message describing the failure
        """
        now = datetime.utcnow().isoformat()

        self.conn.execute("""
            UPDATE sync_operations
            SET status = 'failed', completed_at = ?, error_message = ?
            WHERE sync_id = ?
        """, [now, error_message, self.sync_id])

    def _record_metric(self, metric_type: str, metric_name: str,
                       duration_ms: int = None, items_count: int = None,
                       details: str = None):
        """Record a metric to sync_metrics table.

        Args:
            metric_type: Type of metric ('phase_timing', 'batch_performance', 'error')
            metric_name: Name of the metric
            duration_ms: Duration in milliseconds
            items_count: Item count
            details: JSON details string
        """
        now = datetime.utcnow().isoformat()
        self.conn.execute("""
            INSERT INTO sync_metrics
            (sync_id, metric_type, metric_name, completed_at, duration_ms, items_count, details)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, [self.sync_id, metric_type, metric_name, now, duration_ms, items_count, details])


# ============================================================================
# GitDuckDB CLASS
# ============================================================================

class GitDuckDB:
    """Manage DuckDB database for git commit and file data."""

    def __init__(self, db_path: Optional[str] = None):
        """Initialize GitDuckDB.

        Args:
            db_path: Path to DuckDB file. If None, uses default path in KOSPEX_HOME.
        """
        self.db_path = db_path or self._get_default_path()
        self.conn = None

    def _get_default_path(self) -> str:
        """Get default DuckDB path in KOSPEX_HOME directory."""
        kospex_home = os.getenv("KOSPEX_HOME", os.path.expanduser("~/kospex"))
        return os.path.join(kospex_home, DUCKDB_FILENAME)

    def connect(self, force: bool = False, create_new: bool = False, verbose: bool = False):
        """Establish database connection.

        Args:
            force: If True and DB exists, drop existing tables
            create_new: If True, raise error if database already exists (for init command)
            verbose: Enable verbose output

        Raises:
            RuntimeError: If database exists and create_new=True and force=False
        """
        db_exists = os.path.exists(self.db_path)

        if db_exists and create_new and not force:
            raise RuntimeError(
                f"Database already exists at {self.db_path}. Use force=True to recreate."
            )

        if verbose:
            if db_exists and force:
                print(f"Forcing recreate of existing database at: {self.db_path}")
            elif db_exists:
                print(f"Connecting to existing database at: {self.db_path}")
            else:
                print(f"Creating DuckDB database at: {self.db_path}")

        self.conn = duckdb.connect(self.db_path)

        if force and db_exists:
            self.drop_tables(verbose=verbose)

    def close(self):
        """Close database connection."""
        if self.conn:
            self.conn.close()
            self.conn = None

    def drop_tables(self, verbose: bool = False):
        """Drop commits and commit_files tables.

        Args:
            verbose: Enable verbose output
        """
        if not self.conn:
            raise RuntimeError("Not connected to database. Call connect() first.")

        self.conn.execute("DROP TABLE IF EXISTS commits")
        self.conn.execute("DROP TABLE IF EXISTS commit_files")

        if verbose:
            print("Dropped existing tables")

    def create_schema(self, verbose: bool = False):
        """Create commits, commit_files, and progress tracking tables.

        Args:
            verbose: Enable verbose output
        """
        if not self.conn:
            raise RuntimeError("Not connected to database. Call connect() first.")

        if verbose:
            print("Creating database schema...")

        self.conn.execute(SQL_CREATE_COMMITS_DUCKDB)
        self.conn.execute(SQL_CREATE_COMMIT_FILES_DUCKDB)

        # Create progress tracking tables
        self.conn.execute(SQL_CREATE_SYNC_OPERATIONS)
        self.conn.execute(SQL_CREATE_SYNC_PROGRESS)
        self.conn.execute(SQL_CREATE_SYNC_METRICS)

        if verbose:
            print("✓ Schema created successfully")

    def create_indexes(self, verbose: bool = False):
        """Create performance indexes.

        Args:
            verbose: Enable verbose output
        """
        if not self.conn:
            raise RuntimeError("Not connected to database. Call connect() first.")

        if verbose:
            print("Creating indexes...")

        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_commits_when ON commits(committer_when)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_commits_repo ON commits(_repo_id)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_files_path ON commit_files(file_path)")

        if verbose:
            print("✓ Indexes created successfully")

    def get_commit_count(self) -> int:
        """Get total number of commits in database."""
        if not self.conn:
            raise RuntimeError("Not connected to database. Call connect() first.")

        result = self.conn.execute("SELECT COUNT(*) FROM commits").fetchone()
        return result[0] if result else 0

    def get_file_count(self) -> int:
        """Get total number of file changes in database."""
        if not self.conn:
            raise RuntimeError("Not connected to database. Call connect() first.")

        result = self.conn.execute("SELECT COUNT(*) FROM commit_files").fetchone()
        return result[0] if result else 0

    def get_latest_commit_date(self, repo_id: str) -> Optional[str]:
        """Get the most recent commit date for a repository.

        Args:
            repo_id: Repository identifier

        Returns:
            ISO datetime string of most recent commit, or None if no commits found
        """
        if not self.conn:
            raise RuntimeError("Not connected to database. Call connect() first.")

        result = self.conn.execute(
            "SELECT MAX(committer_when) FROM commits WHERE _repo_id = ?",
            [repo_id]
        ).fetchone()

        return str(result[0]) if result and result[0] else None

    def get_active_sync(self, repo_id: str) -> Optional[Dict]:
        """Get active sync progress for a repository.

        Args:
            repo_id: Repository identifier

        Returns:
            Dict with progress info, or None if no active sync
        """
        if not self.conn:
            raise RuntimeError("Not connected to database. Call connect() first.")

        result = self.conn.execute("""
            SELECT p.repo_id, p.sync_id, p.phase, p.phase_started_at,
                   p.current_batch, p.total_batches, p.items_processed,
                   p.total_items, p.last_updated_at, p.last_hash_processed,
                   p.avg_batch_duration_ms,
                   o.started_at, o.sync_type, o.commits_extracted
            FROM sync_progress p
            JOIN sync_operations o ON p.sync_id = o.sync_id
            WHERE p.repo_id = ?
        """, [repo_id]).fetchone()

        if result:
            return {
                'repo_id': result[0],
                'sync_id': result[1],
                'phase': result[2],
                'phase_started_at': result[3],
                'current_batch': result[4],
                'total_batches': result[5],
                'items_processed': result[6],
                'total_items': result[7],
                'last_updated_at': result[8],
                'last_hash_processed': result[9],
                'avg_batch_duration_ms': result[10],
                'started_at': result[11],
                'sync_type': result[12],
                'commits_extracted': result[13]
            }
        return None

    def get_sync_history(self, repo_id: str = None, limit: int = 20) -> List[Dict]:
        """Get sync history, optionally filtered by repo.

        Args:
            repo_id: Optional repository ID to filter
            limit: Maximum number of records to return

        Returns:
            List of sync operation dicts
        """
        if not self.conn:
            raise RuntimeError("Not connected to database. Call connect() first.")

        if repo_id:
            results = self.conn.execute("""
                SELECT sync_id, repo_id, started_at, completed_at, status,
                       sync_type, since_date, commits_extracted, commits_inserted,
                       files_inserted, commits_replaced, error_message,
                       encoding_errors, last_successful_batch, resume_from_hash,
                       git_head_hash, branch_count
                FROM sync_operations
                WHERE repo_id = ?
                ORDER BY started_at DESC
                LIMIT ?
            """, [repo_id, limit]).fetchall()
        else:
            results = self.conn.execute("""
                SELECT sync_id, repo_id, started_at, completed_at, status,
                       sync_type, since_date, commits_extracted, commits_inserted,
                       files_inserted, commits_replaced, error_message,
                       encoding_errors, last_successful_batch, resume_from_hash,
                       git_head_hash, branch_count
                FROM sync_operations
                ORDER BY started_at DESC
                LIMIT ?
            """, [limit]).fetchall()

        columns = ['sync_id', 'repo_id', 'started_at', 'completed_at', 'status',
                   'sync_type', 'since_date', 'commits_extracted', 'commits_inserted',
                   'files_inserted', 'commits_replaced', 'error_message',
                   'encoding_errors', 'last_successful_batch', 'resume_from_hash',
                   'git_head_hash', 'branch_count']
        return [dict(zip(columns, row)) for row in results]

    def get_interrupted_sync(self, repo_id: str) -> Optional[Dict]:
        """Get last interrupted/failed sync for potential resume.

        Args:
            repo_id: Repository identifier

        Returns:
            Dict with sync info if found, None otherwise
        """
        if not self.conn:
            raise RuntimeError("Not connected to database. Call connect() first.")

        result = self.conn.execute("""
            SELECT sync_id, repo_id, started_at, completed_at, status,
                   sync_type, since_date, commits_extracted, commits_inserted,
                   files_inserted, commits_replaced, error_message,
                   encoding_errors, last_successful_batch, resume_from_hash,
                   git_head_hash, branch_count
            FROM sync_operations
            WHERE repo_id = ? AND status IN ('interrupted', 'failed', 'running')
            ORDER BY started_at DESC
            LIMIT 1
        """, [repo_id]).fetchone()

        if result:
            columns = ['sync_id', 'repo_id', 'started_at', 'completed_at', 'status',
                       'sync_type', 'since_date', 'commits_extracted', 'commits_inserted',
                       'files_inserted', 'commits_replaced', 'error_message',
                       'encoding_errors', 'last_successful_batch', 'resume_from_hash',
                       'git_head_hash', 'branch_count']
            return dict(zip(columns, result))
        return None

    def insert_commits_batch(self, commits: List[Dict], batch_size: int = BATCH_SIZE, verbose: bool = False, log_replacements: bool = True, progress_tracker: Optional['SyncProgressTracker'] = None):
        """Batch insert commits with optional replacement logging and progress tracking.

        Args:
            commits: List of commit dictionaries
            batch_size: Number of rows per batch
            verbose: Enable verbose output
            log_replacements: If True, pre-check for existing records and log replacements (adds overhead)
            progress_tracker: Optional SyncProgressTracker for progress monitoring
        """
        if not self.conn:
            raise RuntimeError("Not connected to database. Call connect() first.")

        total = len(commits)
        total_replacements = 0

        # Create temporary table for batch key checking (used if log_replacements=True)
        if log_replacements:
            self.conn.execute("""
                CREATE TEMP TABLE IF NOT EXISTS batch_commit_keys (
                    hash VARCHAR,
                    repo_id VARCHAR
                )
            """)

        if verbose:
            print(f"Inserting {total} commits (batch size: {batch_size})...")

        for i in range(0, total, batch_size):
            batch = commits[i:i + batch_size]

            if verbose:
                print(f"  Progress: {i}/{total} commits...")

            # Check for existing commits in this batch (only if log_replacements enabled)
            if log_replacements:
                hash_repo_pairs = [(c.get('hash'), c.get('_repo_id')) for c in batch if c.get('hash') and c.get('_repo_id')]

                if hash_repo_pairs:
                    # Clear and populate temporary table with batch keys
                    self.conn.execute("DELETE FROM batch_commit_keys")
                    self.conn.executemany(
                        "INSERT INTO batch_commit_keys VALUES (?, ?)",
                        hash_repo_pairs
                    )

                    # Use parameterized JOIN to find existing records
                    existing = self.conn.execute("""
                        SELECT c.hash, c._repo_id
                        FROM commits c
                        INNER JOIN batch_commit_keys b ON c.hash = b.hash AND c._repo_id = b.repo_id
                    """).fetchall()

                    existing_set = {(row[0], row[1]) for row in existing}
                    batch_replacements = 0

                    for commit in batch:
                        commit_key = (commit.get('hash'), commit.get('_repo_id'))
                        if commit_key in existing_set:
                            batch_replacements += 1
                            if verbose:
                                print(f"  ⚠ Replacing: {commit.get('hash')[:8]} in {commit.get('_repo_id')}")

                    total_replacements += batch_replacements

            # Remove the 'filenames' field before inserting
            clean_batch = []
            for commit in batch:
                clean_commit = commit.copy()
                clean_commit.pop('filenames', None)
                clean_batch.append(clean_commit)

            try:
                self.conn.executemany(
                    """INSERT OR REPLACE INTO commits VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )""",
                    [
                        (
                            c.get("hash"),
                            c.get("author_email"),
                            c.get("author_name"),
                            c.get("author_when"),
                            c.get("committer_email"),
                            c.get("committer_name"),
                            c.get("committer_when"),
                            c.get("message"),
                            c.get("parents"),
                            c.get("parent_count"),
                            c.get("branches"),
                            c.get("branch_count"),
                            c.get("_git_server"),
                            c.get("_git_owner"),
                            c.get("_git_repo"),
                            c.get("_repo_id"),
                            c.get("_files"),
                            c.get("_cycle_time")
                        )
                        for c in clean_batch
                    ]
                )

                # Update progress tracker after successful batch insert
                if progress_tracker:
                    batch_num = (i // batch_size) + 1
                    items_processed = min(i + batch_size, total)
                    last_hash = batch[-1].get('hash') if batch else None
                    progress_tracker.update_batch_progress(batch_num, items_processed, last_hash)

            except Exception as e:
                print(f"Error inserting batch at position {i}: {e}")
                raise

        if verbose:
            print(f"✓ Inserted {total} commits successfully")
            if log_replacements and total_replacements > 0:
                print(f"  ⚠ Replaced {total_replacements} existing commits")

    def insert_commit_files_batch(self, commit_files: List[Dict], batch_size: int = BATCH_SIZE, verbose: bool = False, log_replacements: bool = True, progress_tracker: Optional['SyncProgressTracker'] = None):
        """Batch insert commit files with optional replacement logging and progress tracking.

        Args:
            commit_files: List of file change dictionaries
            batch_size: Number of rows per batch
            verbose: Enable verbose output
            log_replacements: If True, pre-check for existing records and log replacements (adds overhead)
            progress_tracker: Optional SyncProgressTracker for progress monitoring
        """
        if not self.conn:
            raise RuntimeError("Not connected to database. Call connect() first.")

        total = len(commit_files)
        total_replacements = 0

        # Create temporary table for batch key checking (used if log_replacements=True)
        if log_replacements:
            self.conn.execute("""
                CREATE TEMP TABLE IF NOT EXISTS batch_file_keys (
                    hash VARCHAR,
                    file_path VARCHAR,
                    repo_id VARCHAR
                )
            """)

        if verbose:
            print(f"Inserting {total} file changes (batch size: {batch_size})...")

        for i in range(0, total, batch_size):
            batch = commit_files[i:i + batch_size]

            if verbose:
                print(f"  Progress: {i}/{total} file changes...")

            # Check for existing file changes in this batch (only if log_replacements enabled)
            if log_replacements:
                file_keys = [(cf.get('hash'), cf.get('file_path'), cf.get('_repo_id')) for cf in batch if cf.get('hash') and cf.get('file_path') and cf.get('_repo_id')]

                if file_keys:
                    # Clear and populate temporary table with batch keys
                    self.conn.execute("DELETE FROM batch_file_keys")
                    self.conn.executemany(
                        "INSERT INTO batch_file_keys VALUES (?, ?, ?)",
                        file_keys
                    )

                    # Use parameterized JOIN to find existing records
                    existing = self.conn.execute("""
                        SELECT cf.hash, cf.file_path, cf._repo_id
                        FROM commit_files cf
                        INNER JOIN batch_file_keys b
                            ON cf.hash = b.hash
                            AND cf.file_path = b.file_path
                            AND cf._repo_id = b.repo_id
                    """).fetchall()

                    existing_set = {(row[0], row[1], row[2]) for row in existing}
                    batch_replacements = 0

                    for cf in batch:
                        file_key = (cf.get('hash'), cf.get('file_path'), cf.get('_repo_id'))
                        if file_key in existing_set:
                            batch_replacements += 1

                    total_replacements += batch_replacements

            try:
                self.conn.executemany(
                    """INSERT OR REPLACE INTO commit_files VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )""",
                    [
                        (
                            cf.get("hash"),
                            cf.get("file_path"),
                            cf.get("_ext"),
                            cf.get("additions"),
                            cf.get("deletions"),
                            cf.get("committer_when"),
                            cf.get("path_change"),
                            cf.get("_git_server"),
                            cf.get("_git_owner"),
                            cf.get("_git_repo"),
                            cf.get("_repo_id")
                        )
                        for cf in batch
                    ]
                )

                # Update progress tracker after successful batch insert
                if progress_tracker:
                    batch_num = (i // batch_size) + 1
                    items_processed = min(i + batch_size, total)
                    # Use hash from first file in batch for resume reference
                    last_hash = batch[0].get('hash') if batch else None
                    progress_tracker.update_batch_progress(batch_num, items_processed, last_hash)

            except Exception as e:
                print(f"Error inserting file batch at position {i}: {e}")
                raise

        if verbose:
            print(f"✓ Inserted {total} file changes successfully")
            if log_replacements and total_replacements > 0:
                print(f"  ⚠ Replaced {total_replacements} existing file changes")

    def find_duplicates(self, repo_id: Optional[str] = None) -> List[Dict]:
        """Find commits with duplicate hashes.

        Args:
            repo_id: Optional repository ID to filter duplicates

        Returns:
            List of dicts with keys: hash, repo_id, committer_when
        """
        if not self.conn:
            raise RuntimeError("Not connected to database. Call connect() first.")

        if repo_id:
            # Find duplicates within specific repo
            query = """
                SELECT hash, _repo_id as repo_id, committer_when
                FROM commits
                WHERE _repo_id = ?
                AND hash IN (
                    SELECT hash
                    FROM commits
                    WHERE _repo_id = ?
                    GROUP BY hash
                    HAVING COUNT(*) > 1
                )
                ORDER BY hash, committer_when
            """
            params = [repo_id, repo_id]
        else:
            # Find duplicates across all repos
            query = """
                SELECT hash, _repo_id as repo_id, committer_when
                FROM commits
                WHERE hash IN (
                    SELECT hash
                    FROM commits
                    GROUP BY hash
                    HAVING COUNT(*) > 1
                )
                ORDER BY hash, _repo_id, committer_when
            """
            params = []

        results = self.conn.execute(query, params).fetchall()

        # Convert to list of dicts
        duplicates = []
        for row in results:
            duplicates.append({
                'hash': row[0],
                'repo_id': row[1],
                'committer_when': str(row[2]) if row[2] else None
            })

        return duplicates
