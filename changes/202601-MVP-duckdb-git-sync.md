# MVP: DuckDB Git Sync Command

## Overview

Add a new `sync-repo` command to `/src/kgit.py` that uses GitIngest and DuckDB for repository synchronization. This provides an "interim" sync workflow using the newer DuckDB-based storage while maintaining compatibility with the existing SQLite-based system.

## Requirements

1. Check if DuckDB database exists; error with initialization suggestion if not
2. Clone repository if not already cloned (reuse existing clone logic)
3. Perform full sync to DuckDB if repository hasn't been synced before
4. Perform incremental sync if repository has been synced before

## Implementation Details

### File to Modify

**`/src/kgit.py`** - Add new CLI command

### New Imports Required

```python
from kospex import GitIngest, GitDuckDB
```

### Command Signature

```python
@cli.command("sync-repo")
@click.option('-verbose', is_flag=True, default=False, help="Show detailed sync output")
@click.option('-force-full', is_flag=True, default=False, help="Force full sync even if already synced")
@click.argument('repo', type=click.STRING, required=True)
def sync_repo(verbose, force_full, repo):
    """
    Sync a git repository to the DuckDB database.

    REPO can be either:
    - A git URL (https://github.com/owner/repo) - will clone if needed
    - A local path to an existing git repository

    Examples:
        kgit sync-repo https://github.com/kospex/kospex
        kgit sync-repo /path/to/local/repo
        kgit sync-repo --verbose https://github.com/owner/repo
        kgit sync-repo --force-full /path/to/repo

    The command will:
    1. Verify DuckDB database exists (suggest 'kospex init-duckdb' if not)
    2. Clone the repository if a URL is provided and repo doesn't exist
    3. Perform full sync if never synced, incremental sync otherwise
    """
```

### Implementation Flow

```python
def sync_repo(verbose, force_full, repo):
    log.info(f"Starting sync-repo for: {repo}")

    # Step 1: Check DuckDB database exists
    db = GitDuckDB()
    try:
        db.connect(create_new=False, verbose=verbose)
    except FileNotFoundError:
        log.error("DuckDB database not found")
        console.print("[bold red]Error:[/bold red] DuckDB database does not exist.")
        console.print("\nTo initialize the DuckDB database, run:")
        console.print("  [cyan]kospex init-duckdb[/cyan]")
        console.print("\nOr create manually with:")
        console.print("  [cyan]from kospex import GitDuckDB")
        console.print("  db = GitDuckDB()")
        console.print("  db.connect(create_new=True)")
        console.print("  db.create_schema()[/cyan]")
        exit(1)

    # Step 2: Determine if repo is URL or local path
    repo_path = None
    if repo.startswith(('http://', 'https://', 'git@')):
        # It's a URL - clone if needed
        log.info(f"Repository URL detected: {repo}")
        repo_path = kgit.clone_repo(repo)
        if not repo_path:
            log.error(f"Failed to clone repository: {repo}")
            console.print(f"[bold red]Error:[/bold red] Failed to clone {repo}")
            db.close()
            exit(1)
        console.print(f"Repository cloned/updated: {repo_path}")
    else:
        # It's a local path
        repo_path = os.path.abspath(repo)
        if not os.path.isdir(repo_path):
            log.error(f"Directory does not exist: {repo_path}")
            console.print(f"[bold red]Error:[/bold red] Directory does not exist: {repo_path}")
            db.close()
            exit(1)
        if not KospexUtils.is_git(repo_path):
            log.error(f"Not a git repository: {repo_path}")
            console.print(f"[bold red]Error:[/bold red] Not a git repository: {repo_path}")
            db.close()
            exit(1)

    # Step 3: Check if repo has been synced before
    ingest = GitIngest(db)

    # We need to get repo_id first - temporarily sync to get it
    # Or use KospexGit to extract repo_id from the path
    kg = KospexGit()
    kg.set_repo(repo_path)
    repo_id = kg.repo_id

    last_sync_date = db.get_latest_commit_date(repo_id)

    # Step 4: Perform sync
    if last_sync_date and not force_full:
        # Incremental sync
        log.info(f"Performing incremental sync since {last_sync_date}")
        console.print(f"[blue]Incremental sync[/blue] (last sync: {last_sync_date})")
        stats = ingest.sync(
            repo_directory=repo_path,
            last_commit=last_sync_date,
            verbose=verbose
        )
    else:
        # Full sync
        sync_type = "Full sync (forced)" if force_full else "Full sync (first time)"
        log.info(f"Performing full sync for {repo_id}")
        console.print(f"[blue]{sync_type}[/blue]")
        stats = ingest.sync(
            repo_directory=repo_path,
            verbose=verbose
        )

    # Step 5: Report results
    log.info(f"Sync complete: {stats}")
    console.print(f"\n[bold green]Sync complete![/bold green]")
    console.print(f"  Repository: {stats.get('repo_id', repo_id)}")
    console.print(f"  Commits added: {stats.get('commits_added', 0)}")
    console.print(f"  Files processed: {stats.get('files_added', 0)}")
    console.print(f"  Sync type: {'Incremental' if stats.get('incremental') else 'Full'}")

    db.close()
```

### Error Handling

| Scenario | Behavior |
|----------|----------|
| DuckDB database doesn't exist | Exit with error, suggest `kospex init-duckdb` |
| Clone fails (URL provided) | Exit with error, log failure |
| Local path doesn't exist | Exit with error |
| Local path isn't a git repo | Exit with error |
| Sync fails | Log error, report to user |

### Dependencies

The command relies on these existing components:
- `GitDuckDB` from `kospex` package (`/src/kospex/git_duckdb.py`)
- `GitIngest` from `kospex` package (`/src/kospex/git_ingest.py`)
- `KospexGit` from `kospex_git.py` (for `clone_repo()` and `repo_id`)
- `KospexUtils` for `is_git()` check

### Prerequisite: `kospex init-duckdb` Command

The `sync-repo` command will suggest `kospex init-duckdb` when the database doesn't exist. This command needs to be added to `/src/kospex.py`:

```python
@cli.command("init-duckdb")
@click.option('-verbose', is_flag=True, default=False, help="Show detailed output")
def init_duckdb(verbose):
    """Initialize the DuckDB database for git sync operations."""
    from kospex import GitDuckDB

    db = GitDuckDB()
    db.connect(create_new=True, verbose=verbose)
    db.create_schema(verbose=verbose)
    db.create_indexes(verbose=verbose)
    db.close()

    console.print(f"[bold green]DuckDB database initialized![/bold green]")
    console.print(f"  Location: {db.db_path}")
```

## Testing

### Manual Testing

```bash
# Test 1: Error when DuckDB doesn't exist
rm ~/kospex/kospex-git.duckdb
kgit sync-repo https://github.com/kospex/kospex
# Expected: Error with initialization suggestion

# Test 2: Initialize and full sync
# (initialize DuckDB manually first)
kgit sync-repo --verbose https://github.com/kospex/kospex
# Expected: Full sync, reports commits and files added

# Test 3: Incremental sync
kgit sync-repo https://github.com/kospex/kospex
# Expected: Incremental sync (if no new commits, 0 added)

# Test 4: Force full sync
kgit sync-repo --force-full /path/to/repo
# Expected: Full sync even if already synced

# Test 5: Local path sync
kgit sync-repo /Users/peterfreiberg/dev/github.com/kospex/kospex
# Expected: Sync local repo

# Test 6: Invalid path
kgit sync-repo /nonexistent/path
# Expected: Error - directory does not exist

# Test 7: Non-git directory
kgit sync-repo /tmp
# Expected: Error - not a git repository
```

### Unit Tests

Add to `/tests/test_kgit.py`:
- Test DuckDB connection error handling
- Test URL vs path detection
- Test incremental vs full sync logic

## Files Changed

| File | Change |
|------|--------|
| `/src/kgit.py` | Add `sync-repo` command (~80 lines) |
| `/src/kospex.py` | Add `init-duckdb` command (~15 lines) |

## Future Enhancements

1. Add `kospex init-duckdb` command for database initialization
2. Add `--dry-run` option to preview what would be synced
3. Add batch sync from file (like existing `clone -filename`)
4. Add progress indicators for large repositories
5. Consider integrating with existing `kgit clone -sync` flow
