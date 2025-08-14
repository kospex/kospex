#!/usr/bin/env python3
"""
Test script for the refactored repo_sync module.

This script provides a simple CLI interface to test the new RepoSync functionality
without modifying the main kospex codebase.

Usage:
    python test_repo_sync.py <repo_path> [options]

Examples:
    python test_repo_sync.py /path/to/repo
    python test_repo_sync.py /path/to/repo --limit 10
    python test_repo_sync.py /path/to/repo --from-date 2024-01-01
    python test_repo_sync.py /path/to/repo --db-path /custom/path/test.db
"""

import argparse
import sys
import os
from pathlib import Path

# Add src directory to path so we can import our modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

try:
    from repo_sync import RepoSync, RepoSyncConfig, sync_repo
    import kospex_utils as KospexUtils
except ImportError as e:
    print(f"Error importing modules: {e}")
    print("Make sure you're running this from the kospex project root directory.")
    sys.exit(1)


def main():
    """Main CLI interface for testing repo sync."""
    parser = argparse.ArgumentParser(
        description="Test the refactored repository synchronization functionality",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python test_repo_sync.py /path/to/repo
  python test_repo_sync.py /path/to/repo --limit 10
  python test_repo_sync.py /path/to/repo --from-date 2024-01-01
  python test_repo_sync.py /path/to/repo --db-path /custom/path/test.db
        """
    )
    
    parser.add_argument(
        'repo_path',
        help='Path to the git repository to sync'
    )
    
    parser.add_argument(
        '--db-path',
        help='Path to SQLite database file (default: ~/kospex/repo_sync_spike.db)'
    )
    
    parser.add_argument(
        '--limit',
        type=int,
        help='Maximum number of commits to sync'
    )
    
    parser.add_argument(
        '--from-date',
        help='Start date for sync (ISO format, e.g., 2024-01-01)'
    )
    
    parser.add_argument(
        '--to-date',
        help='End date for sync (ISO format, e.g., 2024-12-31)'
    )
    
    parser.add_argument(
        '--no-scc',
        action='store_true',
        help='Disable scc file metadata analysis'
    )
    
    parser.add_argument(
        '--batch-size',
        type=int,
        default=500,
        help='Batch size for processing commits (default: 500)'
    )
    
    parser.add_argument(
        '--progress-interval',
        type=int,
        default=80,
        help='Progress indicator interval (default: 80)'
    )
    
    parser.add_argument(
        '--verbose',
        '-v',
        action='store_true',
        help='Enable verbose output'
    )
    
    args = parser.parse_args()
    
    # Validate repository path
    if not os.path.exists(args.repo_path):
        print(f"Error: Repository path '{args.repo_path}' does not exist.")
        sys.exit(1)
    
    if not KospexUtils.is_git(args.repo_path):
        print(f"Error: '{args.repo_path}' is not a git repository.")
        sys.exit(1)
    
    # Create configuration
    config = RepoSyncConfig(
        db_path=args.db_path,
        use_scc=not args.no_scc,
        batch_size=args.batch_size,
        progress_interval=args.progress_interval
    )
    
    if args.verbose:
        print("Configuration:")
        print(f"  Database path: {config.db_path}")
        print(f"  Use scc: {config.use_scc}")
        print(f"  Batch size: {config.batch_size}")
        print(f"  Progress interval: {config.progress_interval}")
        print(f"  Repository: {args.repo_path}")
        print()
    
    # Prepare sync parameters
    sync_params = {}
    if args.limit:
        sync_params['limit'] = args.limit
    if args.from_date:
        sync_params['from_date'] = args.from_date
    if args.to_date:
        sync_params['to_date'] = args.to_date
    
    try:
        # Initialize synchronizer
        syncer = RepoSync(config)
        
        print(f"Starting synchronization of repository: {args.repo_path}")
        if args.verbose and sync_params:
            print(f"Sync parameters: {sync_params}")
        print()
        
        # Perform synchronization
        commits_synced = syncer.sync_repository(args.repo_path, **sync_params)
        
        print(f"\nSynchronization completed successfully!")
        print(f"Total commits synced: {commits_synced}")
        print(f"Database location: {config.db_path}")
        
        # Show database info if verbose
        if args.verbose:
            show_database_info(config.db_path)
    
    except Exception as e:
        print(f"\nError during synchronization: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)


def show_database_info(db_path: str):
    """Show basic information about the database."""
    try:
        from sqlite_utils import Database
        
        db = Database(db_path)
        
        print("\nDatabase Information:")
        print(f"  Location: {db_path}")
        
        # Check if tables exist and show row counts
        tables = ['commits', 'commit_files', 'repos']
        for table in tables:
            if db[table].exists():
                count = db[table].count
                print(f"  {table}: {count} rows")
            else:
                print(f"  {table}: table not found")
                
    except Exception as e:
        print(f"  Error reading database info: {e}")


if __name__ == '__main__':
    main()