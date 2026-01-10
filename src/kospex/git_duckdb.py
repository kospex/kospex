"""
GitDuckDB - DuckDB database operations for git analytics.

This module provides a class for managing git commit and file data in a DuckDB database.
"""

import os
import duckdb
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
        """Create commits and commit_files tables.

        Args:
            verbose: Enable verbose output
        """
        if not self.conn:
            raise RuntimeError("Not connected to database. Call connect() first.")

        if verbose:
            print("Creating database schema...")

        self.conn.execute(SQL_CREATE_COMMITS_DUCKDB)
        self.conn.execute(SQL_CREATE_COMMIT_FILES_DUCKDB)

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

    def insert_commits_batch(self, commits: List[Dict], batch_size: int = BATCH_SIZE, verbose: bool = False, log_replacements: bool = True):
        """Batch insert commits with optional replacement logging.

        Args:
            commits: List of commit dictionaries
            batch_size: Number of rows per batch
            verbose: Enable verbose output
            log_replacements: If True, pre-check for existing records and log replacements (adds overhead)
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

            if verbose and (i // batch_size) % 10 == 0:
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
            except Exception as e:
                print(f"Error inserting batch at position {i}: {e}")
                raise

        if verbose:
            print(f"✓ Inserted {total} commits successfully")
            if log_replacements and total_replacements > 0:
                print(f"  ⚠ Replaced {total_replacements} existing commits")

    def insert_commit_files_batch(self, commit_files: List[Dict], batch_size: int = BATCH_SIZE, verbose: bool = False, log_replacements: bool = True):
        """Batch insert commit files with optional replacement logging.

        Args:
            commit_files: List of file change dictionaries
            batch_size: Number of rows per batch
            verbose: Enable verbose output
            log_replacements: If True, pre-check for existing records and log replacements (adds overhead)
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

            if verbose and (i // batch_size) % 10 == 0:
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
