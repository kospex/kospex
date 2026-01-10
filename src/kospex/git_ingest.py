"""
GitIngest - Extract git commit data and sync to DuckDB.

This module provides a class for extracting commit history from git repositories
and syncing it to a DuckDB database.
"""

import os
import sys
import subprocess
from pathlib import Path
from typing import Dict, List, Tuple, Optional

# Add parent directory to path for legacy imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from kospex_git import KospexGit
from kospex.git_duckdb import GitDuckDB


# ============================================================================
# GitIngest CLASS
# ============================================================================

class GitIngest:
    """Extract commit data from git repositories and sync to DuckDB."""

    def __init__(self, db: GitDuckDB):
        """Initialize GitIngest.

        Args:
            db: GitDuckDB instance for database operations
        """
        self.db = db
        self.repo_path = None
        self.repo_id = None
        self.kgit = None

    def _set_repo(self, repo_directory: str):
        """Set repository and initialize KospexGit.

        Args:
            repo_directory: Path to git repository

        Raises:
            ValueError: If not a valid git repository
        """
        if not os.path.isdir(repo_directory):
            raise ValueError(f"Repository path does not exist: {repo_directory}")

        git_dir = os.path.join(repo_directory, ".git")
        if not os.path.isdir(git_dir):
            raise ValueError(f"Not a git repository: {repo_directory}")

        self.repo_path = os.path.abspath(repo_directory)

        # Initialize KospexGit and get repo_id
        self.kgit = KospexGit()
        self.kgit.set_repo(self.repo_path)
        self.repo_id = self.kgit.get_repo_id()

    def _get_branch_info(self, verbose: bool = False) -> Dict[str, List[str]]:
        """Get branch information for all commits.

        Args:
            verbose: Enable verbose output

        Returns:
            Dict mapping commit hash to list of branch names
        """
        if not self.repo_path:
            raise RuntimeError("Repository not set. Call _set_repo() first.")

        if verbose:
            print("Gathering branch information for all commits...")

        # Get all branches (local and remote)
        branches_cmd = ["git", "branch", "-a", "--format=%(refname:short)"]
        result = subprocess.run(
            branches_cmd,
            capture_output=True,
            text=True,
            cwd=self.repo_path,
            check=True
        )
        branches = [b.strip() for b in result.stdout.split("\n") if b.strip()]

        commit_branches = {}
        total_branches = len(branches)

        for idx, branch in enumerate(branches, 1):
            if verbose and idx % 10 == 0:
                print(f"  Processing branch {idx}/{total_branches}...")

            # Get all commits in this branch
            commits_cmd = ["git", "log", branch, "--format=%H"]
            result = subprocess.run(
                commits_cmd,
                capture_output=True,
                text=True,
                cwd=self.repo_path,
                check=True
            )

            for commit_hash in result.stdout.split("\n"):
                commit_hash = commit_hash.strip()
                if commit_hash:
                    if commit_hash not in commit_branches:
                        commit_branches[commit_hash] = []
                    commit_branches[commit_hash].append(branch)

        if verbose:
            print(f"✓ Gathered branch info for {len(commit_branches)} commits")

        return commit_branches

    def _extract_commits(
        self,
        commit_branches: Dict,
        since_date: Optional[str] = None,
        verbose: bool = False
    ) -> Tuple[List[Dict], List[Dict]]:
        """Extract commit history with parent information.

        Args:
            commit_branches: Dict mapping commit hash to list of branches
            since_date: Optional ISO datetime string - extract commits AFTER this date
            verbose: Enable verbose output

        Returns:
            Tuple of (commits list, commit_files list)
        """
        if not self.repo_path:
            raise RuntimeError("Repository not set. Call _set_repo() first.")

        if verbose:
            if since_date:
                print(f"Extracting commits from git repository (since {since_date})...")
            else:
                print("Extracting commits from git repository...")

        # Build git log command
        cmd = [
            "git", "log", "--all",
            "--pretty=format:%H#%P#%aI#%cI#%aN#%aE#%cN#%cE#%s",
            "--numstat"
        ]

        # Add --since filter for incremental sync
        if since_date:
            cmd.extend(["--since", since_date])

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=self.repo_path,
            check=True
        )

        commits = []
        commit_files = []
        commit = {}

        for line_num, line in enumerate(result.stdout.split("\n")):
            if verbose and line_num % 1000 == 0 and line_num > 0:
                print(f"  Processed {line_num} lines...")

            if line:
                if "\t" in line and len(line.split("\t")) == 3:
                    # File stats line: additions, deletions, filename
                    additions, deletions, filename = line.split("\t")

                    if "filenames" in commit:
                        # Handle git rename events (old_filename => new_filename)
                        if "=>" in filename:
                            path_change = filename
                            # Extract the new filename from rename event
                            if "{" in filename and "}" in filename:
                                parts = filename.split("{")
                                prefix = parts[0]
                                rest = parts[1]
                                rename_parts = rest.split("}")
                                old_new = rename_parts[0]
                                suffix = rename_parts[1] if len(rename_parts) > 1 else ""
                                new_name = old_new.split("=>")[1].strip()
                                filename = prefix + new_name + suffix
                            else:
                                filename = filename.split("=>")[1].strip()
                        else:
                            path_change = None

                        # Extract file extension
                        ext = Path(filename).suffix.lstrip(".") if "." in filename else ""

                        commit["filenames"].append({
                            "file_path": filename,
                            "path_change": path_change,
                            "additions": int(additions) if additions != "-" else 0,
                            "deletions": int(deletions) if deletions != "-" else 0,
                            "_ext": ext
                        })

                elif "#" in line:
                    # This is a new commit line
                    if commit:  # Save the previous commit
                        commits.append(commit)

                    parts = line.split("#", 8)
                    if len(parts) >= 8:
                        (hash_value, parents, author_datetime, committer_datetime,
                         author_name, author_email, committer_name, committer_email) = parts[0:8]

                        message = parts[8] if len(parts) > 8 else ""

                        # Parse parent hashes (space-separated, can be 0, 1, or more)
                        parent_list = parents.split() if parents else []
                        parent_count = len(parent_list)

                        # Get branches for this commit
                        branches = commit_branches.get(hash_value, [])

                        commit = {
                            "hash": hash_value,
                            "parents": ",".join(parent_list),
                            "parent_count": parent_count,
                            "branches": "|".join(branches),
                            "branch_count": len(branches),
                            "author_when": author_datetime,
                            "committer_when": committer_datetime,
                            "author_name": author_name,
                            "author_email": author_email.lower(),
                            "committer_name": committer_name,
                            "committer_email": committer_email.lower(),
                            "message": message,
                            "filenames": [],
                            "_git_server": "",
                            "_git_owner": "",
                            "_git_repo": "",
                            "_repo_id": "",
                            "_cycle_time": 0
                        }
            else:
                # Empty line - end of a commit block
                if commit:
                    commits.append(commit)
                commit = {}

        # Don't forget the last commit
        if commit:
            commits.append(commit)

        if verbose:
            print(f"✓ Extracted {len(commits)} commits")

        # Process commits into commit_files
        if verbose:
            print("Processing file changes...")

        for commit in commits:
            commit["_files"] = len(commit["filenames"])

            # Process file changes for this commit
            for file_info in commit["filenames"]:
                commit_files.append({
                    "hash": commit["hash"],
                    "file_path": file_info["file_path"],
                    "_ext": file_info["_ext"],
                    "additions": file_info["additions"],
                    "deletions": file_info["deletions"],
                    "committer_when": commit["committer_when"],
                    "path_change": file_info.get("path_change") or "",
                    "_git_server": commit["_git_server"],
                    "_git_owner": commit["_git_owner"],
                    "_git_repo": commit["_git_repo"],
                    "_repo_id": commit["_repo_id"]
                })

        if verbose:
            print(f"✓ Extracted {len(commit_files)} file changes")

        return commits, commit_files

    def sync(
        self,
        repo_directory: str,
        last_commit: Optional[str] = None,
        verbose: bool = False,
        log_replacements: bool = True,
        auto_optimize: bool = True
    ) -> Dict:
        """Sync git repository to DuckDB.

        Args:
            repo_directory: Path to git repository
            last_commit: Optional ISO datetime string - sync commits AFTER this date (exclusive)
            verbose: Enable detailed output
            log_replacements: If True, log when existing records are replaced (adds overhead)
            auto_optimize: If True, automatically disable logging for initial syncs (recommended)

        Returns:
            Dict with stats: {
                'commits_added': int,
                'files_added': int,
                'repo_id': str,
                'incremental': bool
            }
        """
        # 1. Set repository and get repo_id
        self._set_repo(repo_directory)

        if verbose:
            print(f"Repository ID: {self.repo_id}")

        # 2. Auto-optimize: Disable replacement logging for initial syncs
        effective_log_replacements = log_replacements
        if auto_optimize and log_replacements:
            # Check if this repo has any existing commits
            existing_commit_date = self.db.get_latest_commit_date(self.repo_id)

            if existing_commit_date is None:
                # No previous commits - this is an initial sync, disable logging
                effective_log_replacements = False
                if verbose:
                    print("⚡ Auto-optimization: Initial sync detected, disabling replacement logging for better performance")
            elif verbose and not last_commit:
                # Re-syncing all commits - likely many duplicates
                print("⚠ Re-syncing existing repository - replacement logging enabled")

        # 3. Determine sync mode
        incremental = last_commit is not None
        since_date = last_commit  # Will be used in git log --since filter

        # 4. Extract data
        commit_branches = self._get_branch_info(verbose)
        commits, commit_files = self._extract_commits(commit_branches, since_date, verbose)

        # 5. Add repo metadata
        for commit in commits:
            commit['_repo_id'] = self.repo_id

        for file_change in commit_files:
            file_change['_repo_id'] = self.repo_id

        # 6. Insert to database (using optimized log_replacements setting)
        self.db.insert_commits_batch(commits, batch_size=1000, verbose=verbose, log_replacements=effective_log_replacements)
        self.db.insert_commit_files_batch(commit_files, batch_size=1000, verbose=verbose, log_replacements=effective_log_replacements)

        # 7. Return stats
        return {
            'commits_added': len(commits),
            'files_added': len(commit_files),
            'repo_id': self.repo_id,
            'incremental': incremental
        }

    def clone_repo(self, repo_url: str):
        """Clone a repository using KospexGit.

        Args:
            repo_url: URL of repository to clone

        Returns:
            Path to cloned repository, or None if clone failed
        """
        if not self.kgit:
            self.kgit = KospexGit()

        return self.kgit.clone_repo(repo_url)
