#!/usr/bin/env python3
"""This is the kospex command line tool."""

import csv
import json
import logging
import os
import os.path
import socket
import ssl
import tempfile
import time
from datetime import datetime, timedelta, timezone
from shutil import which

import click
import requests
from click.core import ParameterSource
from rich.console import Console
from rich.table import Table
from rich.text import Text

import kospex.authorship as KospexAuthorship
import kospex.orphans as KospexOrphans
import kospex_rebuild as KospexRebuild
import kospex_schema as KospexSchema
import kospex_utils as KospexUtils
import krunner_utils as KrunnerUtils
from kospex.db import Migrator
from kospex.habitat_config import HabitatConfig
from kospex.db.migrator import warn_if_behind
from kospex.dependencies import version_diff as vdiff
from kospex.extractors.registry import package_types
from kospex.integrations.snyk.cli import snyk
from kospex_core import GitRepo, Kospex, RepoPathConflict
from kospex_dependencies import KospexDependencies
from kospex_git import KospexGit
from kospex_query import KospexQuery

# Initialize Kospex environment with enhanced logging
KospexUtils.init(create_directories=True, setup_logging=True, verbose=False)
kospex = Kospex()
# markup=False: values printed here come from git metadata (author emails,
# remote URLs, repo_ids). Rich would parse them as markup (CWE-150), so style
# with style= or rich.text.Text instead of [tags].
console = Console(markup=False)

# Get logger using the new centralized logging system
log = KospexUtils.get_kospex_logger("kospex")


def _configure_runtime_logging(debug=False, verbose=False, quiet=False, log_console=False):
    """Configure logging based on runtime CLI flags."""
    import logging
    import os

    # Set environment variables for the logging system
    if debug:
        os.environ["KOSPEX_LOG_LEVEL"] = "DEBUG"
        log.info("Debug logging enabled via CLI flag")
    elif verbose:
        os.environ["KOSPEX_LOG_LEVEL"] = "INFO"
        log.info("Verbose logging enabled via CLI flag")
    elif quiet:
        os.environ["KOSPEX_LOG_LEVEL"] = "ERROR"
        log.info("Quiet mode enabled via CLI flag")

    if log_console:
        os.environ["KOSPEX_CONSOLE_LOGGING"] = "true"
        log.info("Console logging enabled via CLI flag")

    # Get a fresh logger instance with the new configuration
    # Note: This is a simplified approach. For full runtime reconfiguration,
    # we would need to reset the logging handlers, but this provides
    # immediate feedback for the current session
    if debug:
        log.setLevel(logging.DEBUG)
    elif verbose:
        log.setLevel(logging.INFO)
    elif quiet:
        log.setLevel(logging.ERROR)


# logging.basicConfig(filename='example.log', encoding='utf-8', level=logging.DEBUG)
# VERSION = "0.0.16" # This value should align with the pyproject.toml version for pip


@click.group(invoke_without_command=True)
@click.version_option(version=Kospex.VERSION)
@click.option(
    "--debug", is_flag=True, default=False, help="Enable debug logging (overrides config)."
)
@click.option("--verbose", "-v", is_flag=True, default=False, help="Enable verbose logging.")
@click.option(
    "--quiet", "-q", is_flag=True, default=False, help="Suppress most output (errors only)."
)
@click.option(
    "--log-console",
    is_flag=True,
    default=False,
    help="Enable console logging in addition to file logging.",
)
@click.pass_context
def cli(ctx, debug, verbose, quiet, log_console):
    """Kospex is a tool for assessing code and git repositories.

    It is designed to help understand the structure of code, who are developers and
    changes over time.

    For documentation on how commands run `kospex COMMAND --help`.

    See also https://kospex.io/

    Global Options:
      --debug: Enable debug-level logging for detailed troubleshooting
      --verbose: Enable verbose output and INFO-level logging
      --quiet: Reduce output to errors only
      --log-console: Show log messages on console as well as in files

    """
    # Store logging preferences in context for subcommands
    ctx.ensure_object(dict)
    ctx.obj["debug"] = debug
    ctx.obj["verbose"] = verbose
    ctx.obj["quiet"] = quiet
    ctx.obj["log_console"] = log_console

    # Apply runtime logging configuration
    if debug or verbose or quiet or log_console:
        _configure_runtime_logging(debug, verbose, quiet, log_console)

    if ctx.invoked_subcommand is None:
        # Default behavior when no command is provided
        click.echo(ctx.get_help())
        ctx.exit(0)

    # On a behind DB the write paths fail hard (no such column: last_fetch,
    # no column named resolution). This banner is what connects those errors
    # back to their cause. Warns before every real subcommand; never blocks.
    warn_if_behind(kospex.kospex_db, quiet=quiet)


@cli.command("init")
@click.option(
    "--create",
    is_flag=True,
    default=False,
    help="Create the default ~/code or KOSPEX_CODE directory.",
)
@click.option("--verbose", is_flag=True, default=False, help="Show detailed initialization status.")
@click.option(
    "--validate", is_flag=True, default=False, help="Validate Kospex setup and configuration."
)
def kospex_init(create, verbose, validate):
    """
    Perform comprehensive Kospex environment initialization.

    This command sets up the Kospex directory structure, validates configuration,
    and initializes the centralized logging system.

    Use --create to create missing directories including ~/code or KOSPEX_CODE.
    Use --verbose for detailed status information during setup.
    Use --validate to check the current setup without making changes.
    """
    log.info("Starting Kospex initialization")

    if validate:
        # Run validation without making changes
        validation = KospexUtils.validate_kospex_setup()

        print("\n=== Kospex Setup Validation ===")
        print(f"Overall Status: {validation['overall_status'].upper()}")

        print("\nPaths (environment > kospex.env > default):")
        for var, info in validation["environment_vars"].items():
            print(f"  {var}: {info['value']} ({info['source']})")

        print("\nDirectories:")
        for name, info in validation["directories"].items():
            exists = "✓" if info["exists"] else "✗"
            writable = ""
            if name == "kospex_home" and info["exists"]:
                writable = " (writable)" if info["writable"] else " (not writable)"
            elif name == "kospex_code" and info["exists"]:
                writable = " (readable)" if info["readable"] else " (not readable)"
            print(f"  {exists} {info['path']}{writable}")

        if validation["logging"]:
            print("\nLogging System:")
            if validation["logging"].get("directories_exist"):
                print("  ✓ Logging directories configured")
            else:
                print("  ✗ Logging directories need setup")

        database = validation.get("database") or {}
        if database:
            print("\nDatabase:")
            if database.get("error"):
                print(f"  ✗ Could not read database status: {database['error']}")
            else:
                mark = "✓" if not database.get("pending_count") else "⚠"
                print(f"  {mark} {database['path']}")
                # Report what the bootstrap actually did rather than "it exists" —
                # the module-level Kospex() creates it at import, so "exists" is
                # always true by the time this runs.
                if database.get("created_this_run"):
                    applied = database.get("migrations_applied_this_run", 0)
                    print(f"      created during this invocation, {applied} migration(s) applied")
                elif database.get("pending_count"):
                    print(
                        f"      version {database['version']}, "
                        f"{database['pending_count']} migration(s) pending"
                    )
                else:
                    print(f"      version {database['version']}, up to date")
                if database.get("migration_error"):
                    print(f"  ✗ Migration error: {database['migration_error']}")

        if validation["recommendations"]:
            print("\nRecommendations:")
            for rec in validation["recommendations"]:
                print(f"  • {rec}")

        return

    # Perform initialization
    init_result = KospexUtils.init(create_directories=create, setup_logging=True, verbose=verbose)

    if verbose:
        print("\n=== Initialization Results ===")
        print(f"KOSPEX_HOME: {init_result['kospex_home']}")

        if init_result["directories_created"]:
            print("\nDirectories Created:")
            for dir_path in init_result["directories_created"]:
                print(f"  • {dir_path}")

        if init_result["environment_vars_set"]:
            print("\nLoaded from kospex.env:")
            for var in init_result["environment_vars_set"]:
                print(f"  • {var}")

        if init_result["warnings"]:
            print("\nWarnings:")
            for warning in init_result["warnings"]:
                print(f"  ⚠ {warning}")

        if init_result["errors"]:
            print("\nErrors:")
            for error in init_result["errors"]:
                print(f"  ✗ {error}")

    # Check for scc installation
    installed = which("scc")
    if not installed:
        log.warning("scc binary not found")
        print("\n⚠ Please install scc from https://github.com/boyter/scc")
        print("  This is used to count lines of code, complexity and determine the language.")
    elif verbose:
        log.info("scc binary found!")
        print("\n✓ scc binary found and ready")

    # Report DB state alongside the directory and scc checks.
    try:
        from kospex.db.health import db_status
        db_info = db_status()
        if db_info.get("created_this_run"):
            applied = db_info.get("migrations_applied_this_run", 0)
            print(f"\n✓ Created database: {db_info['path']}")
            print(f"  Applied {applied} migration(s), now at version {db_info['version']}")
        elif db_info.get("pending_count"):
            print(f"\n⚠ Database is {db_info['pending_count']} migration(s) behind")
            print("  Run 'kospex upgrade-db -apply' to update it")
        elif verbose:
            print(f"\n✓ Database up to date: {db_info['path']} (version {db_info['version']})")
    except Exception as e:
        log.error(f"Could not read database status: {e}")
        print(f"\n✗ Could not read database status: {e}")

    # Handle code directory creation/validation
    kospex_code = KospexUtils.get_kospex_code_path()

    if create:
        if not os.path.exists(kospex_code):
            try:
                os.makedirs(kospex_code, exist_ok=True)
                log.info(f"Created code directory: {kospex_code}")
                if verbose:
                    print(f"✓ Created code directory: {kospex_code}")
            except OSError as e:
                log.error(f"Failed to create code directory: {e}")
                print(f"✗ Failed to create code directory: {e}")
        else:
            if verbose:
                print(f"✓ Code directory already exists: {kospex_code}")
    else:
        if os.path.exists(kospex_code):
            if verbose:
                print(f"✓ Code directory exists: {kospex_code}")
                print("  To override, edit config file or set KOSPEX_CODE environment variable")
        else:
            log.warning(f"Code directory does not exist: {kospex_code}")
            print(f"\n⚠ Code directory does not exist: {kospex_code}")
            print("  Run 'kospex init --create' to create it, or")
            print("  Set the KOSPEX_CODE environment variable to a different location")

    if not init_result["errors"]:
        log.info("Kospex initialization completed successfully")
        if not verbose:
            print("✓ Kospex initialization complete!")
            print("  Run 'kospex init --validate' to check your setup")
    else:
        log.error("Kospex initialization completed with errors")


@cli.command("summary")
@click.option("-out", type=click.STRING, help="filename to write CSV results to.")
@click.option("-server", type=click.STRING, help="Git server to query.")
@click.option("-email", type=click.STRING, help="Email of user to query.")
@click.option("-active", is_flag=True, default=False, help="Find only actively modified (90 days).")
@click.option("-docker", is_flag=True, default=False, help="Summary of Docker files.")
@click.option("-dependencies", is_flag=True, default=False, help="Summary of dependencies.")
@click.option("-email-contains", type=click.STRING, help="Email of user to query.")
@click.option("-group", type=click.STRING, help="Name of group (of repos) to query with.")
@click.option("-org", type=click.STRING, help="Name of Org/Team to query with.")
@click.option("-debug", is_flag=True, default=False, help="Debug mode.")
@click.option("--verbose", is_flag=True, default=False, help="Verbose output.")
@click.pass_context
# @click.option('-', type=click.STRING, help="Name of group (of repos) to query with.")
# pylint: disable=unused-argument
def summary(
    ctx,
    out,
    server,
    email,
    active,
    docker,
    dependencies,
    email_contains,
    group,
    org,
    debug,
    verbose,
):
    """Provide a summary of all the known repositories."""
    # Respect global logging settings from context
    global_verbose = ctx.obj.get("verbose", False) if ctx.obj else False
    is_verbose = verbose or global_verbose

    log.info("Starting repository summary")

    params = locals()
    if group and org:
        log.error("Cannot specify both group and org parameters")
        print("Please specify either a group or an org, not both.")
        exit(1)
    elif group and email:
        log.error("Cannot specify both group and email parameters")
        print("Please specify either a group or an email, not both.")
        exit(1)

    results = kospex.summary(results_file=out, **params)

    if results:
        print(f"\nSummary of {len(results)} repositories found.\n")

        # Print status stats of the repos found
        console.print("Repo status summary")
        status = KospexUtils.count_key_occurrences(results, "status")
        status_table = KospexUtils.get_status_table(status)
        console.print(status_table)
        console.print(KospexUtils.get_status_legend())
        console.print()

        unknown = 0
        repo_dirs = []
        for r in results:
            if repo_path := r.get("file_path"):
                if repo_path.lower() != "unknown":
                    repo_dirs.append(repo_path)
                else:
                    if debug:
                        print(f"Unknown repo:\n{r}\n")
                    unknown += 1

        if docker:
            print("Docker file summary")
            # docker = KospexUtils.count_key_occurrences(results, "docker")
            # docker_table = KospexUtils.get_status_table(docker)
            # print(docker_table)
            # docker_files = KrunnerUtils.find_dockerfiles_in_repos(dirs)

            docker_files = KrunnerUtils.find_dockerfiles_in_repos(repo_dirs)
            records = KospexUtils.get_git_metadata(docker_files)
            # print(records)
            docker_status = KospexUtils.count_key_occurrences(records, "status")
            docker_status_table = KospexUtils.get_status_table(docker_status)
            console.print(docker_status_table)

        if dependencies:
            kdeps = KospexDependencies(kospex_db=kospex.kospex_db, kospex_query=kospex.kospex_query)
            deps = []
            for d in repo_dirs:
                results = kdeps.find_dependency_files(d)
                deps.extend(results)
            # results = kdeps.find_dependency_files(directory)
            records = KospexUtils.get_all_last_commit_info(deps)
            dep_stats = KospexUtils.repo_stats(records, "author_date")
            deps_status_table = KospexUtils.get_status_table(dep_stats)
            console.print("\nDependencies summary")
            console.print(deps_status_table)

        print()

        if unknown > 0:
            print(f"WARNING: {unknown} repos found without an entry in 'repos' table.\n")

    else:
        print("No repositories found in the kospex DB.\n")


# @cli.command("sync")
# @click.option('--no-scc', is_flag=True, default=False, help="Skip scc analysis.")
# @click.argument('repo', type=GitRepo())
# def sync(repo,no_scc):
#     """
#     Sync a single repo to the kospex DB, using the native git commands.
#     """

#     installed = which('scc')
#     if not installed:
#         print("Please install scc from https://github.com/boyter/scc")
#         print("This is used to count lines of code, complexity and determine the language.")
#         print("or run sync with --no-scc to skip this step.")

#     kospex.sync_repo(repo, no_scc=no_scc)

# @cli.command("sync")
# @click.option('-previous', type=int, help='# Commits to sync from the oldest in kospex DB.')
# @click.argument('repo', type=GitRepo())
# def sync(repo, previous):
#    """Sync a single repo to the kospex DB.
#
#    Available switches:
#
#    -previous to get the previous N commits using the "-before" date
#
#    Future (not implemented yet) switches:
#
#    -before to specify the the commits before a date
#
#    -after to specify the the commits after a date
#
#    -hash to get all the commits after a hash
#
#    -before and -previous to get the previous N commits before a date
#
#    -next to get the next N commits after the "-after" date
#    """
#    params = {}
#   params['previous'] = previous
#    kospex.sync_repo(repo, **params)


@cli.command("sync-directory")
@click.option("-force", is_flag=True, default=False,
              help="Sync repos already synced from another directory, repointing them.")
@click.argument("directory", type=click.Path(exists=True))
def sync_directory(force, directory):
    """Sync all Git repos found in the data directory to the kospex DB."""
    # Find all the repos in the directory
    repos = KospexUtils.find_repos(directory)
    skipped = 0
    for repo in repos:
        print(f"\nSyncing {repo}")
        try:
            kospex.sync_repo(repo, force=force)
        except RepoPathConflict as exc:
            # A second copy of a tree would otherwise repoint many rows at once.
            log.error(str(exc))
            print(f"SKIPPED: {exc}")
            skipped += 1

    if skipped:
        print(f"\nSkipped {skipped} repo(s) already synced from another directory. "
              f"Re-run with -force to repoint them.")


@cli.command("developers")
@click.option("-repo", type=GitRepo(), help="Git repo directory to assess, confirm sync status")
@click.option("-days", type=int, default=90, help="Committed in the last X days.")
@click.option("-org_key", type=click.STRING, help="Format of SERVER~ORG")
@click.option(
    "-repo_id", type=click.STRING, help="Repo ID to query, in the format SERVER~ORG~REPO."
)
@click.option("-server", type=click.STRING, help="Domain of the git server. E.g. github.com")
@click.option("-all", is_flag=True, default=False, help="Find all developer history. ")
@click.option("-out", type=click.Path(), help="filename to write CSV results to.")
# pylint: disable=unused-argument
def devs(repo, days, org_key, repo_id, server, all, out):
    """
    Information about developers history.
    """
    params = locals()

    # A repo the database has never seen returns no rows, exactly like a repo
    # that is present but dormant. Say which it is before reporting zero. (#134)
    if repo:
        status = kospex.repo_scope_status(repo)
        if status["message"]:
            click.echo(status["message"], err=True)
            raise SystemExit(1)

    if all:
        click.echo("Finding all developer history")
    else:
        click.echo(f"Searching for active developers (last {days} days)")

    developers = kospex.active_developers(**params)

    if all:
        click.echo(f"Found {len(developers)} developers.")

    else:
        click.echo(f"Found {len(developers)} active developers in the last {days} days.")


@cli.command("list-repos")
@click.option("-db", is_flag=True, default=False, help="List repos sync'ed to db.")
@click.option("-repo_id", is_flag=True, default=False, help="Include repo_id in output.")
@click.option("-server", type=click.STRING, help="Domain of the git server. E.g. github.com")
@click.option("-email", type=click.STRING, help="Email of user to query.")
@click.argument("directory", required=False, type=click.Path(exists=True))
def list_repos(
    db,
    repo_id,
    server,
    email,
    directory,
):
    """
    List all git repositories found in either:
    - the given directory or
    - the kospex database from sync operations.
    """
    if directory:
        print("\nDirectory: " + os.path.abspath(directory))
    elif db:
        print("\nListing repos from the kospex database.")
    else:
        print("Please specify either a directory or use the -db flag.")
        exit(1)

    kospex.list_repos(directory, db=db, repo_id=repo_id, server=server, email=email)

    if directory and email:
        print("\nWARNING! email search ONLY works with the -db flag.\n")


@cli.command("tech-landscape")
@click.option("-repo", type=GitRepo())
@click.option("-repo_id", type=click.STRING)
@click.option("-days", type=int, default=90, help="Committed in the last X days.")
@click.option(
    "-metadata", is_flag=True, default=False, help="Use file metadata, NOT Git committed filenames."
)
# pylint: disable=unused-argument
def tech_landscape(repo, repo_id, days, metadata):
    """
    Show the tech landscape either by file extensions
    or scc metadata (use the -metadata switch)."""
    # A repo the database has never seen returns no rows, exactly like a repo
    # that is present but dormant. Say which it is before reporting zero. (#134)
    if repo:
        status = kospex.repo_scope_status(repo)
        if status["message"]:
            click.echo(status["message"], err=True)
            raise SystemExit(1)
    kwargs = locals()
    kospex.tech_landscape(**kwargs)


@cli.command("sync-metadata")
@click.option("-repo", type=GitRepo())
@click.option("-directory", type=click.Path(exists=True))
@click.option("-no_scc", is_flag=True, default=False, help="Don't use scc for stats.")
@click.option("-force", is_flag=True, default=False, help="Force a new metadata scan.")
def sync_metadata(repo, directory, no_scc, force):
    """
    Sync file metadata for either a 'repo' or 'directory' of repos.
    """
    if directory and repo:
        print("Please specify either a -repo or a -directory, not both.")
    elif directory:
        kospex.sync_metadata(directory)
    elif repo:
        data = kospex.file_metadata(repo, force=force)
        print(data)
    else:
        print("Please specify either a '-repo' or a '-directory'.")


@cli.command("hotspot")
@click.option("-repo", type=GitRepo())
@click.option("-repo_id", type=click.Path())
@click.option("-by_file", is_flag=True, default=False, help="Calculate hotspot per file.")
# pylint: disable=unused-argument
def hotspot(repo, repo_id, by_file):
    """Find hotspots in the given repo."""
    params = locals()
    print("WARNING: EXPERIMENTAL")

    # Same guard as developers / tech-landscape: an unsynced repo is a
    # different answer from a stale one, and scripts need the exit code. (#134)
    if repo:
        status = kospex.repo_scope_status(repo)
        if status["message"]:
            click.echo(status["message"], err=True)
            raise SystemExit(1)

    if repo or repo_id:
        kospex.hotspot(**params)
    else:
        print("Please specify either a '-repo' or a '-repo_id'.")


@cli.command("health-check")
@click.argument("directory", required=False, type=click.Path(exists=True))
def health_check(directory):
    """Run a health check on a git repository."""

    warning_message = "\nWARNING: This is an experimental WIP feature."
    print(warning_message)
    print()
    if not directory:
        # directory = os.getcwd()
        directory = "."
        print("No directory specified, using current directory.")

    if not KospexUtils.is_git(directory):
        print("Directory is not a git repository.")
        exit(1)

    details = KospexUtils.get_git_stats(directory)

    print("\nDirectory: " + os.path.abspath(directory))
    # Get the # of authors
    # Get the active authors
    # Get the number of depencencies files
    kquery = KospexQuery()
    kdeps = KospexDependencies(kospex_db=kospex.kospex_db, kospex_query=kquery)
    results = kdeps.find_dependency_files(directory)
    records = KospexUtils.get_all_last_commit_info(results)
    stats = KospexUtils.repo_stats(records, "author_date")
    print(stats)

    details["dependency_files"] = len(results)
    print(f"Found {len(results)} dependency files.")
    active_dep_files = stats.get("Active", 0)
    if active_dep_files > 0:
        active_dep_files_percent = (active_dep_files / len(results)) * 100
        details["active_dependency_files"] = f"{active_dep_files_percent:.2f}%"
    else:
        details["active_dependency_files"] = "0%"

    # Get the number of total dependencies
    # Get the status label
    print(f"{warning_message}\n")
    print(KospexUtils.get_keyvalue_table(details))


@cli.command("deps")
@click.option("-repo", type=GitRepo(), help="File path to git repo.")
@click.option("-directory", type=click.Path(), help="Directory to search for dependency files.")
@click.option("-out", type=click.STRING, help="filename to write CSV results to.")
def deps(repo, directory, out):
    """Find dependency files in a directory or repo.

    To assess a single manifest against deps.dev, use `kospex sca FILE`.
    """
    kquery = KospexQuery()
    kdeps = KospexDependencies(kospex_db=kospex.kospex_db, kospex_query=kquery)

    if directory:
        results = kdeps.find_dependency_files(directory)
        records = []

        if results:
            records = KospexUtils.get_all_last_commit_info(results)

            repos = [data.get("repo", "Unknown") for data in records]
            unique_repos = list(set(repos))
            stats_dict = {key: KospexUtils.init_repo_stats() for key in unique_repos}
            repo_activity_stats = {
                key: KospexUtils.init_repo_stats()
                for key in KospexUtils.get_development_status_options()
            }
            # This will hold a Git url : status mapping
            # E.g. repos_status['https://github.com/kospex/kospex'] = 'Active'
            repo_status = {}

            if records:
                table = KospexUtils.get_dependency_files_table(records)
                print(table)

                for r in records:
                    repo = r.get("repo", "Unknown")
                    file_path = r.get("file_path")
                    git_base = KospexUtils.find_git_base(file_path)
                    get_last_commit_info = KospexUtils.get_last_commit_info(git_base)
                    repo_status[repo] = get_last_commit_info.get("status")

                    if repo:
                        stats_dict[repo] = KospexUtils.add_status(stats_dict[repo], r.get("status"))
                        repo_activity_stats[repo_status[repo]] = KospexUtils.add_status(
                            repo_activity_stats[repo_status[repo]], r.get("status")
                        )

                    # Get repo status

            print(repo_status)

            activity_table = KospexUtils.get_repo_stats_table(
                stats=repo_activity_stats, fieldname="Repo Status"
            )

            if out:
                KospexUtils.list_dict_2_csv(records, out)

            stats = KospexUtils.repo_stats(records, "author_date")
            print("\nOverall Summary of dependency files found")
            print(stats)
            status_table = KospexUtils.get_status_table(stats)
            console.print(status_table)

            print("\nSummary of dependency files found in the directory by repo\n")
            status_table = KospexUtils.get_repo_stats_table(stats=stats_dict)
            print(status_table)
            print()

            print("\nSummary of dependency files found by Repo Status\n")
            print(activity_table)
            print()

        else:
            print("No package/dependency manager files found.")

    elif repo:
        results = kdeps.find_dependency_files(repo)

        if results:
            print("\nDependency files found in the repo:")
            for file_path in results:
                print(f"{file_path}")
            print(f"\nFound {len(results)} dependency files.\n")

        else:
            print("No package/dependency manager files found.")

    else:
        print("Please specify either a '-repo' or a '-directory'.")
        print("To assess a single dependency file, use `kospex sca FILE`.")


def _split_package_version(name):
    """NAME[@VERSION] -> (name, version or None).

    The version follows the LAST '@', and a leading '@' is part of the name,
    so npm's `@babel/core@7.0.0` is ('@babel/core', '7.0.0') and
    `@babel/core` has no version. A trailing '@' gives version "".
    """
    head, sep, tail = name.rpartition("@")
    if not sep or not head:
        return name, None
    return head, tail


def _explain_no_package_record(package_type, package_name):
    """Say why deps.dev gave no package record: only a 404 means it has none.

    A failed request also comes back as "no record", so without this a TLS
    verification failure -- typically a TLS-intercepting proxy whose CA Python
    does not trust -- told the user the package did not exist.
    """
    query = kospex.dependencies.kospex_query
    error = getattr(query, "last_url_error", None)
    status = getattr(query, "last_url_status", None)
    err = Console(stderr=True, soft_wrap=True)

    if isinstance(error, requests.exceptions.SSLError):
        err.print(f"SSL error connecting to deps.dev, so {package_type} {package_name} "
                  "was not looked up:", markup=False)
        err.print(f"  {error}", markup=False)
        err.print("\n" + KospexUtils.SSL_CA_BUNDLE_ADVICE, markup=False)
    elif error is not None:
        err.print(f"Could not reach deps.dev, so {package_type} {package_name} was not "
                  f"looked up: {type(error).__name__}: {error}", markup=False)
    elif status not in (None, 200, 404):
        err.print(f"deps.dev returned HTTP {status} for {package_type} {package_name}, "
                  "so it was not looked up.", markup=False)
    else:
        console.print(Text.assemble("deps.dev has no record of ", (package_name, "bold"),
                                    f" ({package_type})."))


def _sca_package(package_spec, file_path, save, out, as_json):
    """`kospex sca -package TYPE:NAME[@VERSION]`: one package's releases (#390).

    The name is everything after the first colon, so a Maven
    group:artifact keeps its own. Options that only apply to a file are
    refused rather than ignored, before deps.dev is asked anything.
    """
    ctx = click.get_current_context()
    refused = [name for name, given in (
        ("FILE_PATH", file_path), ("-repo", ctx.params.get("repo")), ("-save", save),
        ("-out", out), ("-json", as_json),
        ("-scope", ctx.get_parameter_source("scope") == ParameterSource.COMMANDLINE))
        if given]
    if refused:
        raise click.UsageError(f"-package cannot be used with {', '.join(refused)}.")

    package_type, _, package_name = package_spec.partition(":")
    package_type = package_type.lower()
    types = package_types()
    if not package_name or package_type not in types:
        raise click.UsageError(
            f"-package expects TYPE:NAME[@VERSION], with TYPE one of {', '.join(types)} "
            f"(got {package_spec!r}).")
    package_name, version = _split_package_version(package_name)
    if version == "":
        raise click.UsageError(f"-package {package_spec!r} has an empty version after '@'.")

    permitted, reason = kospex.dependencies.lookup_permitted(package_type, package_name)
    if not permitted:
        Console(stderr=True, soft_wrap=True).print(
            f"{package_type} {package_name} is private ({reason}), so it is never "
            "looked up.", markup=False)
        exit(1)

    # Fresh: one request, and a cached "latest" can be a day old.
    summary = kospex.dependencies.release_summary(package_type, package_name, fresh=True,
                                                  version=version)
    if summary is None:
        _explain_no_package_record(package_type, package_name)
        exit(1)
    requested = summary.get("requested")
    if version is not None and requested is None:
        status = summary.get("requested_status")
        if getattr(kospex.dependencies.kospex_query, "last_url_error", None) is not None:
            _explain_no_package_record(package_type, f"{package_name}@{version}")
        elif status == 404:
            console.print(Text.assemble("deps.dev has no version ", (version, "bold"),
                                        f" of {package_name} ({package_type}). "
                                        "Give an exact version, not a range."))
        else:
            console.print(f"Could not look up version {version} of {package_name} "
                          f"({package_type}) on deps.dev"
                          + (f": HTTP {status}." if status else "."))
        exit(1)

    def version_of(entry):
        return entry["version"] if entry else None

    rows = [("First", summary["first"]), ("Latest", summary["latest"])]
    # The "(any)" rows include pre-releases, and only appear when that gives a
    # different answer -- react's 0.0.x builds, a release candidate newer than
    # the latest release. Otherwise they repeat the row above.
    if version_of(summary["first_any"]) != version_of(summary["first"]):
        rows.insert(1, ("First (any)", summary["first_any"]))
    if summary["latest_any"]:
        rows.append(("Latest (any)", summary["latest_any"]))
    requested_rows = set()
    if requested:
        # A requested version that is already a row (First, Latest...) is
        # flagged there. Otherwise it gets its own row in publish order: the
        # rows above are oldest first, and an undated version goes before Latest.
        matches = [i for i, (_, entry) in enumerate(rows)
                   if version_of(entry) == requested["version"]]
        if matches:
            for i in matches:
                rows[i] = (f"{rows[i][0]} (requested)", rows[i][1])
            requested_rows.update(matches)
        else:
            published = requested["published_at"]
            at = next((i for i, (label, entry) in enumerate(rows)
                       if i > 0 and (label == "Latest" and not published
                                     or entry and published
                                     and entry["published_at"] > published)),
                      len(rows))
            rows.insert(at, ("Requested", requested))
            requested_rows.add(at)

    table = Table(title=f"Releases: {package_type} {package_name}")
    table.add_column("Release")
    table.add_column("Version")
    table.add_column("Published")
    table.add_column("Age", justify="right")
    for i, (label, entry) in enumerate(rows):
        style = "bold" if i in requested_rows else None
        if entry is None:
            table.add_row(label, "none", "", "", style=style)
            continue
        # deps.dev text is printed through terminal_safe() (CWE-150).
        shown = (KospexUtils.terminal_safe(entry["version"])
                 + (" (deprecated)" if entry["is_deprecated"] else ""))
        table.add_row(label, shown, KospexUtils.terminal_safe(entry["published_at"][:10]),
                      KospexUtils.age_label(entry["age_days"]), style=style)
    console.print()
    KospexUtils.print_table(table, console)

    def plural(count, noun):
        return f"{count} {noun}{'' if count == 1 else 's'}"

    console.print(f"{plural(summary['versions'], 'version')}, "
                  f"{plural(summary['prereleases'], 'pre-release')}, "
                  f"{summary['deprecated']} deprecated. "
                  f"{plural(summary['releases_last_12_months'], 'release')} "
                  "in the last 12 months.")
    console.print("Releases exclude alphas, betas, release candidates and other "
                  "pre-releases" + ("; '(any)' rows include them."
                                    if any("(any)" in label for label, _ in rows) else "."))
    latest = summary["latest"]
    if latest and latest["is_deprecated"]:
        reason = KospexUtils.terminal_safe(latest["deprecated_reason"])
        console.print(f"The latest release is deprecated{': ' + reason if reason else '.'}")
    undated = summary["undated"]
    if undated == 1:
        console.print("There is 1 undated version (no publish date); it may be older "
                      "than the dates above.")
    elif undated:
        console.print(f"There are {undated} undated versions (no publish date); any of "
                      "them may be older than the dates above.")
    if requested:
        _print_requested_version(package_type, package_name, requested)
    console.print()


def _upgrade_path_lines(version, behind, system=None):
    """Lines describing the upgrade path from `version` (already
    terminal-safe) to the latest release, from behind_fields(); [] when it was
    not measured or there is no gap. deps.dev text goes through
    terminal_safe() (CWE-150).

    The total of releases in between (behind_releases) is left to the CSV:
    beside the published-between count above it, a third number read as a
    contradiction.
    """
    safe = KospexUtils.terminal_safe
    level = behind.get("behind_level")
    if level not in ("major", "minor", "patch"):
        return []

    steps = [f"{behind[f'behind_{name}']} {name}"
             for name in ("major", "minor", "patch", "other") if behind.get(f"behind_{name}")]
    listed = steps[0] if len(steps) == 1 else f"{', '.join(steps[:-1])} and {steps[-1]}"
    total = sum(behind.get(f"behind_{name}") or 0
                for name in ("major", "minor", "patch", "other"))
    lines = [f"Upgrade path to {safe(behind['latest_version'])}: "
             f"{listed} release{'' if total == 1 else 's'}."]
    if level != "major":
        # The latest is on this major, so the line below would repeat the one above.
        return lines

    numbers = vdiff.parse_release(behind.get("latest_on_major") or version, system)
    line = f"{numbers[0]}.x" if numbers else "its major"
    on_major = behind.get("behind_on_major")
    if on_major:
        newest = behind["latest_on_major"]
        step = vdiff.change_level(version, newest, system)
        size = f", a {step} upgrade" if step in vdiff.LEVELS else ""
        lines.append(f"Without a major upgrade: {version} → {safe(newest)}{size} "
                     f"({on_major} newer {line} release{'' if on_major == 1 else 's'}).")
    else:
        lines.append(f"{version} is the latest {line}; any upgrade from here is a major one.")
    return lines


def _print_requested_version(package_type, package_name, requested):
    """The lines under the Releases table for `-package TYPE:NAME@VERSION`.

    Every string from deps.dev goes through terminal_safe() (CWE-150): a
    deprecation message is whatever the package's maintainer wrote.
    """
    safe = KospexUtils.terminal_safe
    version = safe(requested["version"])
    console.print()
    if requested["requested_as"] != requested["version"]:
        console.print(f"deps.dev lists {safe(requested['requested_as'])} as {version}.")
    behind = requested["versions_behind"]
    latest = safe(requested["latest_version"])
    if requested["is_prerelease"]:
        console.print(f"{version} is a pre-release, so releases behind is not counted.")
    elif behind is None:
        console.print(f"{version} is not in deps.dev's version list, so releases "
                      "behind is unknown.")
    elif behind == 0:
        console.print(f"{version} is the latest release.")
    else:
        # Publish order, so it counts fixes to older lines released in that
        # window (react's 15.7.0) and not ones released after the latest.
        console.print(f"{behind} release{' was' if behind == 1 else 's were'} published "
                      f"between {version} and the latest ({latest}), including any "
                      "fixes to older lines.")
    for line in _upgrade_path_lines(version, requested.get("behind") or {}, package_type):
        console.print(line)
    if requested["is_deprecated"]:
        reason = safe(requested["deprecated_reason"])
        console.print(f"{version} is deprecated{': ' + reason if reason else '.'}")

    advisories = requested["advisories"]
    if not advisories:
        console.print(f"deps.dev lists no known advisories for {version}.")
        return
    count = len(advisories)
    note = ""
    if requested["advisory_ids"] != count:
        note = (f" (deps.dev lists {requested['advisory_ids']} IDs; aliases of the "
                "same vulnerability are shown once)")
    console.print(f"{count} known advisor{'y' if count == 1 else 'ies'} "
                  f"for {version}{note}:")
    table = Table(title=f"Advisories: {package_type} {package_name} {version}")
    table.add_column("Advisory")
    table.add_column("CVE")
    table.add_column("CVSS 3", justify="right")
    table.add_column("Title")
    # deps.dev's order, not sorted by score: an advisory with no CVSS 3 score
    # may be scored only under CVSS 4, and sorting would bury it.
    for advisory in advisories:
        ids = "\n".join(safe(i) for i in [advisory["id"]] + advisory["also"])
        score = advisory["cvss3_score"]
        table.add_row(ids, "\n".join(safe(c) for c in advisory["cves"]),
                      "-" if score is None else f"{score:.1f}", safe(advisory["title"]))
    KospexUtils.print_table(table, console)
    if any(a["cvss3_score"] is None for a in advisories):
        console.print("CVSS 3 '-' means deps.dev has no CVSS 3 score; the advisory may "
                      "be scored under CVSS 4. Check its severity on osv.dev.")
    console.print("Look up an advisory at "
                  "https://osv.dev/vulnerability/<ID from the Advisory column>")


@cli.command("sca")
@click.option("-repo", type=GitRepo(), help="NOT IMPLEMENTED - File path to git repo.")
@click.option(
    "-scope",
    default=KospexSchema.PACKAGE_USE_DIRECT,
    show_default=True,
    help="Comma-separated dependency scopes to show: "
    + ", ".join(KospexDependencies.scope_names() + [KospexDependencies.SCOPE_ALL])
    + ". Filters the output only; everything is still extracted and saved.",
)
@click.option(
    "-save",
    is_flag=True,
    default=False,
    help="Save results to the kospex DB. Refused unless the repo and its HEAD commit "
    "are synced and the worktree is clean.",
)
@click.option("-malware", is_flag=True, default=False, help="DEPRECATED, ignored.")
@click.option("-json", "as_json", is_flag=True, default=False,
              help="Print the results as JSON on stdout, and nothing else.")
@click.option("-out", type=click.STRING, help="filename to write CSV results to.")
@click.option("-package", "package_spec", metavar="TYPE:NAME[@VERSION]",
              help="Look up one package instead of a file, e.g. pypi:requests or "
              "maven:io.netty:netty-handler. Shows its first and latest releases, "
              "their age, and release counts. Add @VERSION (pypi:requests@2.25.0) "
              "to place that version among them, with its releases behind and "
              "known advisories.")
@click.option("-enrich/-no-enrich", "enrich", default=None,
              help="Look packages up on deps.dev, or only parse the file. "
                   "Overrides KOSPEX_DEPS_ENRICH (default: enrich).")
@click.argument("file_path", required=False, type=click.Path(exists=True))
def sca(repo, scope, save, malware, as_json, out, package_spec, enrich, file_path):
    """
    Run a lightweight software composition analysis (SCA) of a dependency file
    (e.g. requirements.txt, package.json), using deps.dev.

    Shows direct dependencies by default; use -scope to choose others.

    With -package TYPE:NAME, looks up a single package instead. TYPE is an
    ecosystem kospex reads dependency files for, such as npm, pypi or maven;
    an unknown TYPE lists the accepted ones. It shows the first and latest
    releases (alphas, betas, release candidates and other pre-releases
    excluded), with "(any)" rows when counting pre-releases changes the
    answer. Versions deps.dev lists without a publish date are counted, since
    any of them may be older. TYPE:NAME@VERSION adds that exact version to the
    table, with how many releases it is behind and its known advisories.

    With -no-enrich, or KOSPEX_DEPS_ENRICH=false, nothing is looked up: the
    file is parsed and its packages listed (and saved with -save) as not
    checked. -enrich overrides the setting.

    Package names matching KOSPEX_DEPS_PRIVATE (or GOPRIVATE, for Go) are
    never looked up, even with -enrich, and are listed as private_skipped.
    """
    err = Console(stderr=True, soft_wrap=True)

    try:
        enrich, why_not = KospexDependencies.enrich_decision(enrich)
    except ValueError as e:
        err.print(f"Error: {e}", style="red", markup=False)
        exit(1)

    # A CA bundle path that doesn't exist fails every lookup with an OSError
    # requests does not wrap, so say so before looking anything up (#453).
    if enrich and (problem := KospexUtils.ca_bundle_problem()):
        err.print(problem, style="red", markup=False)
        exit(1)

    if package_spec:
        if not enrich:
            # -package is nothing but a lookup, so there is no parse-only form.
            err.print(f"-package looks a package up on deps.dev, and lookups are off "
                      f"({why_not}). Add -enrich to look it up anyway.", markup=False)
            exit(1)
        _sca_package(package_spec, file_path=file_path, save=save, out=out,
                     as_json=as_json)
        return

    if repo:
        err.print("-repo is NOT implemented")
        exit(1)

    if not file_path:
        err.print("Either -package TYPE:NAME, -repo REPO or file_path is required.\n")
        exit(1)

    try:
        scopes = KospexDependencies.parse_scope(scope)
    except ValueError as e:
        err.print(str(e), style="red")
        exit(1)

    if malware:
        err.print("-malware is deprecated: the lookup service it used is no longer "
                  "available. The flag is ignored.", style="yellow")

    kdeps = kospex.dependencies

    # Checked before assess(), which calls deps.dev once per package: a run
    # that is going to be refused should not spend those lookups first.
    if save:
        problems = kdeps.save_preconditions(file_path)
        if problems:
            err.print("Not saving -- sca -save records committed, synced state only:",
                      style="red")
            for problem in problems:
                err.print(f"  - {problem}", markup=False)
            exit(1)

    if not enrich:
        err.print(f"Not looking packages up ({why_not}): the file is parsed only, "
                  "and lookup columns are empty. Use -enrich to look them up.",
                  markup=False)

    # Failures from this run only: the query object outlives a command.
    if getattr(kdeps.kospex_query, "url_failures", None) is not None:
        kdeps.kospex_query.url_failures.clear()
    results = kdeps.assess(file_path, save=save, source="kospex sca", quiet=True,
                           enrich=enrich)

    if results is None:
        err.print(f"\n{file_path} is not a supported package manager\n", style="red")
        exit(1)

    private = sum(r.get("resolution") == KospexSchema.RESOLUTION_PRIVATE_SKIPPED
                  for r in results)
    if private:
        one = private == 1
        err.print(f"{private} package{' is' if one else 's are'} private "
                  "(KOSPEX_DEPS_PRIVATE / GOPRIVATE) and "
                  f"{'was' if one else 'were'} not looked up.", markup=False)

    # Why rows are lookup_error, when requests got no response at all (#453).
    failures = getattr(kdeps.kospex_query, "url_failures", None)
    for line in KospexUtils.url_failure_summary(failures):
        err.print(line, style="yellow", markup=False)
    if line := KospexUtils.url_failure_log_line(failures):
        log.warning("sca %s: %s", file_path, line)

    shown = KospexDependencies.filter_by_scope(results, scopes)

    if out:
        with open(out, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=kdeps.get_table_field_names())
            writer.writeheader()
            writer.writerows(kdeps.table_rows(shown))

    footer = KospexDependencies.scope_footer(results, scopes)
    if save:
        save_status = f"Saved {len(results)} packages to the kospex DB."
    else:
        save_status = ("Dependency data not saved to db, use the -save option "
                       "to store these results")

    if as_json:
        # stdout carries the JSON alone; the status lines go to stderr.
        click.echo(json.dumps(kdeps.table_rows(shown), indent=2))
        err.print(footer, markup=False)
        err.print(save_status)
        return

    print(file_path)
    for record in shown:
        print(KospexDependencies.summary_line(record))
    kdeps.print_table_rows(shown, console=console)
    console.print(footer, markup=False, soft_wrap=True)
    console.print(save_status)


@cli.command("author-tech")
@click.option("-author_email", type=click.STRING)
@click.option("-repo_id", type=click.STRING)
# pylint: disable=unused-argument
def author_tech(author_email, repo_id):
    """Show the tech landscape for a given author."""
    kwargs = locals()
    print(kwargs)
    results = kospex.kospex_query.author_tech(**kwargs)
    table = kospex.author_tech_pretty_table(results)
    print(table)


@cli.command("key-person")
@click.option("-top", type=int, default=4, help="The number of top authors to assess.")
@click.option(
    "-repo_id", type=click.STRING, help="Repo ID to query, in the format SERVER~ORG~REPO."
)
@click.argument("directory", required=False, type=GitRepo())
@click.pass_context
def key_person(ctx, directory, top, repo_id):
    """
    Identify the key people for a repo based on number of commits.

    Give either a DIRECTORY (e.g. '.'), whose git remote gives the repo_id,
    or -repo_id, which needs no clone on disk. Either way the figures come
    from the kospex DB, as of the repo's last sync.

    Shows the top X committers of all time, plus any of the top X active
    committers (last 90 days) not already listed. X defaults to 4; set it
    with -top X.
    """
    if not directory and not repo_id:
        # No silent default to the current directory: make the choice explicit.
        raise click.exceptions.NoArgsIsHelpError(ctx)

    if directory and repo_id:
        click.echo("Give either a DIRECTORY or -repo_id, not both.", err=True)
        raise SystemExit(1)

    if directory:
        kgit = KospexGit()
        kgit.set_repo(directory)
        repo_id = kgit.get_repo_id()

    # An unknown repo_id returns no rows, exactly like a dormant repo. Say
    # which it is rather than print an empty table. (#134)
    status = kospex.repo_db_status(repo_id, repo_directory=directory)
    if status["message"]:
        click.echo(status["message"], err=True)
        raise SystemExit(1)

    kquery = KospexQuery()

    # "merges" sits beside "commits" so a maintainer's lower commit count is
    # explained in place rather than needing to be taken on trust. It is only
    # comparable within a repo — a squash-and-merge project has none. See #170.
    headers = ["author", "commits", "merges", "% commits", "last_commit",
               "first_commit", "active_commits", "% active", "tenure"]
    # Text, not str: rich parses a str as markup, and authors come from commit
    # metadata (CWE-150). "a[/]@b" would raise; "[link=...]" would render a link.
    table = Table(title=Text(f"Key Person: {repo_id}"))
    # author is an email: an identifier, so never wrapped mid-value
    table.add_column("author", style="cyan", no_wrap=True)
    for header in headers[1:]:
        table.add_column(header, justify="right")

    authors = kquery.key_person(repo_id=repo_id, top=top)

    for a in authors:
        table.add_row(*[Text(str(a.get(h) if a.get(h) is not None else "")) for h in headers])

    console.print()
    KospexUtils.print_table(table, console)
    console.print()
    print("Merge commits are counted separately. A merge combines work already")
    print("credited to the branch commits, so it is not authorship - but it does")
    print("show who integrates changes. Comparable within a repo only: a project")
    print("using squash-and-merge produces no merge commits at all.")
    print()
    if directory:
        # The directory only gives the repo_id; the table is the DB's last sync,
        # which may be behind what is on disk.
        repo = kquery.get_repo_by_id(repo_id) or {}
        last_sync = repo.get("last_sync")
        last_sync = (KospexUtils.extract_db_date(last_sync) if last_sync else None) or "unknown"
        print("Note: the repo_id is calculated from this repo directory, but the data")
        print(f"comes from the last sync in the database ({last_sync}).")
        print()


@cli.command("stats")
@click.argument("repo_id", required=True, type=click.STRING)
def developer_stats(repo_id):
    """Show developer stats for a repository (key person analysis).

    REPO_ID is the normalised repo identifier (e.g. github.com~owner~repo).
    """
    kquery = KospexQuery()

    stats = kquery.get_developer_stats(repo_id=repo_id)
    if not stats:
        console.print(Text.assemble("No developer stats found for ", (repo_id, "bold"), "."))
        console.print("Run 'kgit sync' on the repo first to populate stats.")
        return

    table = Table(title=f"Developer Stats: {repo_id}")
    table.add_column("Author", style="cyan")
    table.add_column("Commits", justify="right")
    table.add_column("Additions", justify="right")
    table.add_column("Deletions", justify="right")
    table.add_column("Files", justify="right")
    table.add_column("90d Commits", justify="right")
    table.add_column("90d Adds", justify="right")
    table.add_column("90d Dels", justify="right")
    table.add_column("90d Files", justify="right")
    table.add_column("% All Time", justify="right")
    table.add_column("% 90 Days", justify="right")
    table.add_column("First Commit")
    table.add_column("Last Commit")

    for row in stats:
        table.add_row(
            str(row.get("author_email", "")),
            str(row.get("total_commits", 0)),
            str(row.get("additions") or ""),
            str(row.get("deletions") or ""),
            str(row.get("unique_files") or ""),
            str(row.get("commits_last_90_days", 0)),
            str(row.get("additions_90_days") or ""),
            str(row.get("deletions_90_days") or ""),
            str(row.get("unique_files_90_days") or ""),
            f"{row.get('pct_all_time', 0):.1f}%" if row.get("pct_all_time") else "",
            f"{row.get('pct_90_days', 0):.1f}%" if row.get("pct_90_days") else "",
            KospexUtils.extract_db_date(row.get("first_commit", "")),
            KospexUtils.extract_db_date(row.get("last_commit", "")),
        )

    console.print()
    console.print(table)
    console.print()


@cli.group("rebuild")
def rebuild():
    """Rebuild derived data for repos already in the DB, without re-syncing.

    \b
    Each subcommand rebuilds one table for a scope of repos:
        kospex rebuild developer-stats -all
        kospex rebuild developer-stats -org github.com~kospex
        kospex rebuild developer-stats github.com~kospex~kospex
    'kospex rebuild list' shows the tasks and what each writes.
    """


@rebuild.command("list")
def rebuild_list():
    """List the rebuild tasks, the tables they write and whether they need a clone."""
    table = Table(title="Rebuild tasks")
    table.add_column("Task", style="cyan")
    table.add_column("Writes")
    table.add_column("Needs clone")
    table.add_column("Description")
    for task in KospexRebuild.TASKS.values():
        table.add_row(task.name, ", ".join(task.writes), "yes" if task.needs_clone else "no",
                      task.description)
    console.print(table)


def _rebuild_command(task):
    """A `kospex rebuild <task>` command with the shared scope options."""

    @click.option("-dry-run", "dry_run", is_flag=True, default=False,
                  help="List the repos in scope without changing anything.")
    @click.option("-server", default=None, help="Every repo on a git server, e.g. github.com")
    @click.option("-org", "org_key", default=None, help="Every repo in an ORG_KEY, e.g. github.com~kospex")
    @click.option("-all", "all_repos", is_flag=True, default=False, help="Every repo in the DB.")
    @click.argument("repo_id", required=False, type=click.STRING)
    def command(repo_id, all_repos, org_key, server, dry_run):
        kquery = KospexQuery()
        try:
            repos = KospexRebuild.resolve_scope(kquery, repo_id=repo_id, all_repos=all_repos,
                                                org_key=org_key, server=server)
        except ValueError as e:
            raise click.UsageError(str(e))
        if not repos:
            console.print("No repos in scope.")
            raise SystemExit(1)

        # How the repos were chosen, as typed, for the log.
        scope = repo_id or ("-all" if all_repos else f"-org {org_key}" if org_key else f"-server {server}")

        if dry_run:
            log.debug("rebuild %s dry run: scope=%s, %d repos", task.name, scope, len(repos))
            for repo in repos:
                console.print(repo["_repo_id"])
            console.print(f"\n{len(repos)} repo(s) would be rebuilt by '{task.name}'.")
            return

        def progress(i, total, result):
            line = f"[{i}/{total}] {result.repo_id}  {result.status}  {result.seconds:.2f}s"
            console.print(f"{line}  {result.detail}" if result.detail else line)

        started = time.perf_counter()
        results = KospexRebuild.run_task(task, repos, kquery, on_repo=progress, scope=scope)
        counts = {s: sum(1 for r in results if r.status == s) for s in ("ok", "skipped", "failed")}
        console.print(f"\n{task.name}: {counts['ok']} rebuilt, {counts['skipped']} skipped, "
                      f"{counts['failed']} failed in {time.perf_counter() - started:.1f}s")
        if counts["failed"]:
            raise SystemExit(1)

    command.__doc__ = f"Rebuild {task.name} for a REPO_ID, or -all / -org / -server.\n\n{task.description}"
    return command


for _task in KospexRebuild.TASKS.values():
    rebuild.command(_task.name)(_rebuild_command(_task))


@cli.command("orgs")
def orgs():
    """List all the organizations in the database."""
    kquery = KospexQuery()
    orgs_list = kquery.orgs()

    table = Table(title="Organizations")
    table.add_column("org_key", style="cyan")
    table.add_column("org")
    table.add_column("commits", justify="right")
    table.add_column("repos", justify="right")
    table.add_column("authors", justify="right")
    table.add_column("committers", justify="right")
    table.add_column("days_ago", justify="right")
    table.add_column("last_commit", justify="right")

    for o in orgs_list:
        table.add_row(
            str(o.get("org_key", "")),
            str(o.get("org", "")),
            str(o.get("commits", "")),
            str(o.get("repos", "")),
            str(o.get("authors", "")),
            str(o.get("committers", "")),
            str(o.get("days_ago", "")),
            str(o.get("last_commit", "")),
        )

    console.print()
    console.print(table)
    console.print()


# @cli.command("know")
# @click.argument('email',  type=click.STRING)
# def knol(email):
#     """Show the tech landscape for a given author."""

#     print("WARNING: This is a work in progress.")

#     nowish = datetime.now().isoformat()
#     print(nowish)
#     last30 = KospexUtils.date_days_ago(nowish, 60)

#     kquery = KospexQuery()

#     kd = kquery.tech_commits(author_email=email)
#     kd.where("commits.committer_when", ">=", last30)

#     print(kd.generate_sql(line=True))
#     print(kd.get_bind_parameters())

#     ar1 = {}
#     ar2 = {}

#     results = kospex.kospex_db.execute(kd.generate_sql(), kd.get_bind_parameters())
#     for result in results:
#         ar1[result[0]] = result[1]
#         #print(result[0])
#         print(result)

#     print(ar1)

#     kd2 = kquery.tech_commits(author_email=email)
#     kd2.where("commits.committer_when", "<", last30)
#     kd2_from = KospexUtils.date_days_ago(last30, 90)
#     kd2.where("commits.committer_when", ">=", kd2_from)

#     print("###")
#     print(kd2.generate_sql(line=True))
#     print(kd2.get_bind_parameters())

#     results = kospex.kospex_db.execute(kd2.generate_sql(), kd2.get_bind_parameters())
#     for result in results:
#         ar2[result[0]] = result[1]
#         print(result)

#     ext_dif = KospexUtils.merge_dicts(ar1, ar2)
#     print(ext_dif)


@cli.command("groups")
@click.option("-name", type=click.STRING, help="Name of the group")
@click.option("-add", is_flag=True, default=False, help="Add items to a group.")
@click.option("-remove", is_flag=True, default=False, help="Remove items from a group.")
@click.option("-delete", is_flag=True, default=False, help="Delete ALL items in a group.")
@click.option("-show", is_flag=True, default=False, help="List the items in a group.")
@click.option("-file", type=click.Path(), help="The group file to sync.")
@click.option("-value", type=click.STRING, help="Value to add or remove from the group.")
@click.option("-email", is_flag=True, default=False, help="Specify the values are emails.")
@click.option("-repo", is_flag=True, default=False, help="Specify the values are repo URLs.")
def groups(name, add, remove, delete, show, file, value, email, repo):
    """
    List all the groups in the database.
    """
    params = locals()
    kquery = KospexQuery()

    actions = [add, remove, delete, show]
    types = [email, repo]
    input_type = [file, value]

    # By default, if there are no action parameters and we'll display the groups and exit
    if sum(actions) == 0:
        group_list = kquery.groups()
        if group_list:
            for g in group_list:
                print(g)
        else:
            print("No groups found.")
        exit(0)

    # Start validating the parameters

    if not name:
        print("Please specify a group name.")
        exit(1)

    KospexUtils.validate_only_one(actions, "Please specify either -add, -list, -remove or -delete.")

    if remove:
        print("Not implemented yet.")
        exit(1)

    if add:
        KospexUtils.validate_only_one(types, "Please specify either -email or -repo.")

    lines = []

    if file:
        with open(file, "r") as content:
            lines = content.readlines()
        # Remove any trailing newlines
        lines = [line.strip() for line in lines]
    elif value:
        lines.append(value)

    if add:
        # create a list of hashsed with lines
        # Then use sqliteutils to upsert them
        print(f"Should add the values to the group '{name}'")
        records = []
        data_keyname = "email" if email else "git_url"
        kgit = KospexGit()

        for l in lines:
            record = {}
            record["group_name"] = name
            record[data_keyname] = l
            record["_repo_id"] = ""
            record["email"] = ""
            if repo:
                kgit.set_remote_url(l)
                record["_repo_id"] = kgit.get_repo_id()
                print(f"Repo ID: {record['_repo_id']}")

            record["data_type"] = data_keyname
            records.append(record)

        kospex.kospex_db.table(KospexSchema.TBL_GROUPS).upsert_all(
            records, pk=["group_name", "_repo_id", "email"]
        )

    elif show:
        print(f"Listing the values in the group '{name}'")
        results = kquery.groups(params)
        if results:
            for r in results:
                print(r)
        else:
            print("No items found in the group.")

    elif delete:
        print(f"Deleting all the values in the group '{name}'")
        kquery.groups(params)

    else:
        print("Please specify either -add, -list, -remove or -delete.")

    print(lines)


@cli.command("orphans")
@click.option(
    "-days", type=int, default=90,
    help="A person who committed anywhere in the last X days is still here. (default 90)"
)
@click.option(
    "-window", type=int, default=365,
    help="Whose knowledge counts: people who committed to the repo in the last X days. "
         "(default 365)"
)
@click.option(
    "-stale-days", type=int, default=30,
    help="Flag repos whose last sync is older than X days. (default 30)"
)
@click.option(
    "-active-in", "active_in", type=click.Choice(["estate", "server", "org"]),
    default="estate", show_default=True,
    help="Where a person counts as still here: any synced repo, the repo's git "
         "server, or its kospex org (server~owner)."
)
@click.option("-server", type=click.STRING, help="Git server to query.")
@click.option(
    "-target-list", type=click.Path(exists=True),
    help="A file of repos to check, one git URL or repo_id per line."
)
@click.option(
    "-csv",
    "csv_path",
    type=click.Path(dir_okay=False, writable=True),
    help="Write results to this CSV file (spreadsheet-safe).",
)
def orphans(days, window, stale_days, active_in, server, target_list, csv_path):
    """
    Find orphaned repos.

    A repo is Orphaned when everyone who worked on it is no longer working
    for you: people committed to it in the window (default 365 days), and
    none of them has committed to any synced repo in the last X days
    (default 90). Someone who moved to another synced project is still
    here. "Present" counts those people still active.

    "Still here" is any synced repo by default. -active-in org looks only in
    the repo's own kospex org (server~owner, e.g. github.com~apache or
    gitlab.com~group~~subgroup), -active-in server only on its git server.

    \b
    Statuses:
      Orphaned    people worked on it, none of them is still active
      Knowledge   at least one of them is still active
      Dormant     no person committed in the window (bots may have)
      Not synced  in -target-list but not in the kospex DB

    Author email and author date are used, so a GitHub web merge
    (committer noreply@github.com) or a rebase does not distort the result.
    Bots are never counted as people; "bots active" is bots that committed
    to the repo in the last X days. "Last sync" is days since kospex last
    fetched or synced the repo; in red, it is older than -stale-days, so
    that repo's data may be out of date, and so may the activity of its
    people. It needs a sync: kgit pull --all --stale DAYS (or --org /
    --server).
    """
    kq = kospex.kospex_query
    for name, value in (("-days", days), ("-window", window), ("-stale-days", stale_days)):
        if value < 1:
            raise click.UsageError(f"{name} must be 1 or more.")

    if target_list:
        repo_ids = []
        with open(target_list, "r") as file:
            for line in file:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "~" in line and "/" not in line and ":" not in line:
                    repo_ids.append(line)
                    continue
                parts = KospexGit.parse_git_remote(line)
                if not parts:
                    click.echo(f"Skipping '{KospexUtils.terminal_safe(line)}': "
                               "not a git URL or repo_id.", err=True)
                    continue
                repo_ids.append(KospexGit.generate_repo_id(
                    parts["remote"], parts["org"], parts["repo"]))
        repo_ids = list(dict.fromkeys(repo_ids))
        repos = kq.repos_table(repo_ids=repo_ids)
    else:
        repos = kq.repos_table(server=server)
        repo_ids = list(repos)

    if not repo_ids:
        click.echo("No repos to check.")
        return

    rows = KospexOrphans.report(
        kq, repo_ids, repos, KospexAuthorship.bot_checker(), days=days, window=window,
        stale_days=stale_days, active_in=active_in)

    table = Table()
    table.add_column("repo_id")
    table.add_column("Status")
    table.add_column("People", justify="right")
    table.add_column("Present", justify="right")
    table.add_column("% Here", justify="right")
    table.add_column("Bots active", justify="right")
    table.add_column("Last sync", justify="right")

    for row in rows:
        repo_id = row["repo_id"]
        shown_id = KospexUtils.terminal_safe(repo_id)
        if row["status"] == KospexOrphans.NOT_SYNCED:
            console.print(f"repo_id: {shown_id}, {row['status']}", soft_wrap=True)
        else:
            console.print(
                f"repo_id: {shown_id}, {row['status']}, Present: {row['people_active']}, "
                f"Total: {row['people']} in {window} days.",
                soft_wrap=True,
            )
        age = row["refresh_age_days"]
        refreshed = Text("-" if age == "" and row["status"] == KospexOrphans.NOT_SYNCED
                         else "never" if age == "" else f"{age}d",
                         style="red" if row["refresh_stale"] is True else "")
        pct = row["pct_here"]
        table.add_row(
            Text(shown_id), row["status"],
            str(row["people"]), str(row["people_active"]),
            "" if pct == "" else f"{pct:.2f}%",
            str(row["bots_active"]), refreshed,
        )

    console.print()
    KospexUtils.print_table(table, console)

    counts = KospexOrphans.counts(rows)
    console.print(f"\n{KospexOrphans.summary(rows)}")
    with_people = counts[KospexOrphans.ORPHANED] + counts[KospexOrphans.KNOWLEDGE]
    if with_people:
        console.print(
            f"Of {with_people} repos with people in the window: "
            f"{counts[KospexOrphans.ORPHANED] / with_people * 100:.2f}% orphaned.")
    stale = sum(1 for r in rows if r["refresh_stale"] is True)
    if stale:
        console.print(
            f"{stale} of {len(rows)} repos not synced in {stale_days} days: their status "
            f"may be out of date, and so may who is still active. They need a sync: "
            f"{KospexOrphans.sync_command(stale_days, server=None if target_list else server)}",
            soft_wrap=True)
    console.print()

    if csv_path:
        with open(csv_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(KospexOrphans.FIELDS)
            for row in rows:
                writer.writerow([KrunnerUtils.spreadsheet_safe(row[k])
                                 for k in KospexOrphans.FIELDS])
        console.print(f"Wrote {len(rows)} rows to {csv_path}\n")

    print()


@cli.command("metadata")
@click.option("-file_type", type=click.STRING, help="Only returns files of this file type.")
@click.option("-repo_id", type=click.STRING, help="repo_id to query kospex DB for metadata.")
@click.option("-sync", type=click.BOOL, help="Sync metadata to kospex DB.")
def metadata(file_type, repo_id, sync):
    """
    Find file metadata and tags.
    """
    dir = os.getcwd()

    if repo_id:
        dir = None
    else:
        # Check we're in a repo
        if not kospex.git.is_git_repo(dir):
            print(f"\n{dir} is NOT a git repo")
            dir_base = KospexUtils.find_git_base(dir)
            if dir_base:
                print(f"Did you mean {dir_base}??")
            else:
                print("Can't find a Git repo in any parent directories either.")
            exit(1)

    if file_type and sync:
        print("Can only sync on a directory, with no file_type specified.")
        exit(1)

    table = kospex.cli_file_metadata(repo_dir=dir, file_type=file_type, repo_id=repo_id, sync=sync)

    print(table)

    if table:
        print(f"Files: {len(table.rows)}")


@cli.command("upgrade-db")
@click.option(
    "-apply", is_flag=True, default=False,
    help="Apply pending migrations. Without this flag, runs in status/preview mode."
)
def upgrade_db(apply):
    """
    Show the kospex DB migration status, or apply pending migrations.

    Without options: prints current version, applied migrations, pending migrations.
    With -apply: runs each pending migration in sequence (one transaction each).
    """
    click.echo(f"Kospex CLI version {Kospex.VERSION}")
    click.echo("\nWARNING: backup your database before performing ANY upgrade.\n")

    migrator = Migrator(kospex.kospex_db)

    if apply:
        ran = migrator.apply_pending()
        if ran:
            click.echo(f"\nApplied {len(ran)} migration(s).")
        else:
            click.echo("\nNothing to apply.")
    else:
        migrator.print_status()


ADVISORY_HISTORY_NOTICE = (
    "advisory-history is being redesigned; a fixed implementation is planned "
    "for Q4 2026. See https://github.com/kospex/kospex/issues/282"
)


@cli.command("advisory-history")
@click.option("-ecosystem", type=click.STRING, help="E.g. npm, pypi")
@click.option("-package", type=click.STRING, help="Name of package")
def advisory_history(ecosystem, package):
    """
    (Being redesigned) Advisory history for a package's recent versions.
    """
    # Disabled rather than left running: it crashed on scoped npm and Maven
    # packages and fetched every version in deps.dev's lexical order. The
    # options stay so existing invocations reach this notice instead of a
    # usage error. Redesign: #282.
    click.echo(ADVISORY_HISTORY_NOTICE, err=True)
    raise SystemExit(1)


@cli.command("system-status")
def status():
    """
    Show the system status.
    """
    print("\nKospex System Status")
    print("====================\n")

    table = kospex.get_kospex_table_summary(display_progress=True)
    print()
    print(table)
    print()

    print("Environment configuration\n")
    config = kospex.get_kospex_config_table()
    print(config)
    print()

    print("Database table version status")
    print("-----------------------------")
    try:
        from kospex.db.health import db_status
        db_info = db_status()
        print(f"Path:\t\t{db_info['path']}")
        print(f"Version:\t{db_info['version']}")
        print(f"Applied:\t{db_info['applied_count']} migration(s)")
        if db_info["pending_count"]:
            print(f"Pending:\t{db_info['pending_count']} migration(s)")
            for migration_id in db_info["pending_ids"]:
                print(f"\t\t  {migration_id}")
            print("\nRun 'kospex upgrade-db -apply' to apply them.")
        else:
            print("Pending:\tnone")
    except Exception as e:
        print(f"Could not read database status: {e}")
    print()

    print("Summary stats coverage")
    print("----------------------")
    try:
        coverage = KospexQuery().developer_stats_coverage()
        print(f"Repos with stats not rebuilt yet:\t{coverage['repos_not_rebuilt']}")
        print(f"Repos with commits but no stats:\t{coverage['repos_without_stats']}")
        if coverage["repos_not_rebuilt"] or coverage["repos_without_stats"]:
            print("\nThe web summary page counts are wrong for these repos until their")
            print("stats are rebuilt. Run: kospex rebuild developer-stats -all")
    except Exception as e:
        print(f"Could not read summary stats coverage: {e}")
    print()

    print("Installed tool status")
    print("---------------------")
    installed = which("scc")
    if installed:
        print(f"scc:\t{installed}")
    else:
        print("scc:\tNot installed")

    installed = which("git")
    if installed:
        print(f"git:\t{installed}")
    else:
        print("git:\tNot installed")

    print("\n")


@cli.command("commit-stats")
@click.option("-commits", type=int, default=11, help="Time to X commits (default: 11).")
@click.option(
    "-request_id",
    type=click.STRING,
    default=None,
    help="Scope by repo_id, org_key (server~owner), or server (e.g. github.com).",
)
@click.argument("dev", required=True, type=click.STRING)
def commit_stats(commits, request_id, dev):
    """
    Show commit stats for a given developer email.

    Reports all-time commits, commits in the last 90 days, tenure (years),
    and the number of days taken to reach the Xth commit (default 11) as an
    onboarding indicator. Optionally scope to a repo, org, or git server via
    -request_id.
    """
    scope = request_id if request_id else "all"

    all_commits = kospex.kospex_query.commits(author_email=dev, request_id=request_id)
    active_commits = kospex.kospex_query.commits(
        author_email=dev,
        request_id=request_id,
        after=KospexUtils.days_ago_iso_date(90),
    )

    tenure_years = "-"
    if all_commits:
        tenure_days = KospexUtils.days_between_datetimes(
            all_commits[-1].get("committer_when"),
            all_commits[0].get("committer_when"),
        )
        tenure_years = round(tenure_days / 365.25, 1)

    if len(all_commits) >= commits:
        days_to_x = round(
            KospexUtils.days_between_datetimes(
                all_commits[-1].get("committer_when"),
                all_commits[-commits].get("committer_when"),
            )
        )
    else:
        days_to_x = f"N/A (< {commits} commits)"

    table = Table(title=f"Commit Stats: {dev}  (scope: {scope})")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right")
    table.add_row("Total commits", str(len(all_commits)))
    table.add_row("Commits (last 90d)", str(len(active_commits)))
    table.add_row("Tenure (years)", str(tenure_years))
    table.add_row(f"Days to {commits}th commit", str(days_to_x))

    console.print()
    console.print(table)
    console.print()


# Parked: DuckDB proof of concept, being researched further for feasibility and
# usefulness. Commented out rather than deleted so it can be reviewed and restored.
# The duckdb dependency is commented out in pyproject.toml alongside it.
# @cli.command("init-duckdb")
# @click.option("-verbose", is_flag=True, default=False, help="Show detailed output")
# @click.option("-force", is_flag=True, default=False, help="Force recreate if database exists")
# def init_duckdb(verbose, force):
#     """
#     Initialize the DuckDB database for git sync operations.
#
#     This creates the DuckDB database file at ~/kospex/kospex-git.duckdb
#     with the required schema for storing git commit and file data.
#
#     Example:
#         kospex init-duckdb
#         kospex init-duckdb -verbose
#         kospex init-duckdb -force  (recreate existing database)
#     """
#     from kospex import GitDuckDB
#
#     log.info("Initializing DuckDB database")
#
#     db = GitDuckDB()
#
#     # Check if database already exists
#     if os.path.exists(db.db_path) and not force:
#         console.print(f"[bold yellow]DuckDB database already exists![/bold yellow]")
#         console.print(f"  Location: {db.db_path}")
#         console.print("\nUse [cyan]-force[/cyan] to recreate the database (will drop existing tables).")
#         return
#
#     db.connect(create_new=True, force=force, verbose=verbose)
#     db.create_schema(verbose=verbose)
#     db.create_indexes(verbose=verbose)
#
#     console.print(f"[bold green]DuckDB database initialized![/bold green]")
#     console.print(f"  Location: {db.db_path}")
#
#     db.close()
#     log.info("DuckDB database initialization complete")


@cli.command("connectivity")
@click.option(
    "-save",
    is_flag=True,
    default=False,
    help="Write a system CA bundle that verifies api.deps.dev to ~/kospex/kospex.env "
    "as REQUESTS_CA_BUNDLE.",
)
def connectivity(save):
    """
    Test the HTTPS connection to api.deps.dev, and fix certificate errors.

    kospex makes its lookups with Python's requests library, which verifies
    against its own CA bundle (certifi), or REQUESTS_CA_BUNDLE when set. It
    does not use the operating system's certificate store, so behind a
    TLS-intercepting proxy the organisation's CA can be trusted by apt, curl
    and git and still be unknown to kospex.

    When verification fails, this checks the system CA bundles and says
    which one verifies api.deps.dev. -save writes that one to
    ~/kospex/kospex.env as REQUESTS_CA_BUNDLE.
    """
    from kospex import ca_bundles

    hostname = "api.deps.dev"
    test_url = f"https://{hostname}/v3alpha/systems"
    log.info("Testing connectivity to %s", hostname)

    var, bundle = ca_bundles.bundle_in_use()
    print(f"\nTesting connectivity to {hostname}...")
    print(f"URL: {test_url}")
    print(f"CA bundle: {bundle}" + (f"  (from {var})" if var else "  (certifi, requests' default)"))
    print()

    # A bundle path that doesn't exist raises a plain OSError inside requests,
    # before any connection. Treated like a verification failure: the fix is
    # the same, a bundle that works.
    needs_bundle = False
    if problem := KospexUtils.ca_bundle_problem():
        print(f"✗ {problem}")
        log.error(problem)
        needs_bundle = True
    else:
        try:
            response = requests.get(test_url, timeout=10)
            print(f"✓ Successfully connected to {hostname}")
            print(f"  Status Code: {response.status_code}")
            print("  SSL Verification: PASSED")
            log.info("Successfully connected to %s", hostname)
        except requests.exceptions.SSLError as e:
            needs_bundle = True
            print(f"✗ SSL Error detected when connecting to {hostname}")
            print(f"  Error: {e}")
            log.error("SSL Error connecting to %s: %s", hostname, e)
        except requests.exceptions.RequestException as e:
            # DNS, refused, timeout, proxy: no CA bundle fixes these.
            print(f"✗ Connection error: {e}")
            log.error("Connection error: %s", e)
            return

    if not needs_bundle:
        if save:
            print("\nNothing saved: certificate verification already works.")
        print("\n" + "=" * 60)
        print("✓ Connectivity test completed successfully")
        print("=" * 60 + "\n")
        return

    if not problem:
        _show_presented_certificate(hostname)

    print("\nChecking the system CA bundles:")
    checks = ca_bundles.find_working_bundles(test_url, exclude=[bundle])
    for check in checks:
        if check.verified:
            print(f"  ✓ {check.path}  verifies {hostname}")
        else:
            print(f"  ✗ {check.path}  {'does not verify' if check.ssl_error else check.error}")
    if not checks:
        print("  none found")
    working = next((c.path for c in checks if c.verified), None)

    print("\n" + "=" * 60)
    if working is None:
        print(f"⚠ No CA bundle on this system verifies {hostname}.")
        print("\nIf you are behind a TLS-intercepting proxy, add your organisation's CA")
        print("to the system store (update-ca-certificates on Debian/Ubuntu,")
        print("update-ca-trust on RHEL/Fedora), then run this again. The CA, not the")
        print(f"certificate {hostname} presented. See https://docs.kospex.io/troubleshooting")
        print("=" * 60 + "\n")
        return

    if not save:
        print(f"⚠ The CA bundle in use doesn't work for {hostname}, but {working} does.")
        print("\nUse it for kospex:")
        print(f"  export REQUESTS_CA_BUNDLE={working}")
        print("or run `kospex connectivity -save` to write it to ~/kospex/kospex.env.")
        print("=" * 60 + "\n")
        return

    env_file = str(HabitatConfig.get_instance().config_file)
    try:
        previous = ca_bundles.save_to_kospex_env(env_file, working)
    except OSError as e:
        print(f"✗ Could not write {env_file}: {e}")
        log.error("Could not write %s: %s", env_file, e)
        print("=" * 60 + "\n")
        return
    print(f"✓ Saved REQUESTS_CA_BUNDLE={working} to {env_file}"
          + (f" (replacing {previous})" if previous and previous != working else ""))
    log.info("Saved REQUESTS_CA_BUNDLE=%s to %s", working, env_file)
    # kospex.env never overrides the environment, so a value set in the shell
    # would keep winning. It came from the shell if it isn't the file's value.
    shell_value = os.environ.get("REQUESTS_CA_BUNDLE")
    if shell_value and shell_value != previous and \
            os.path.expanduser(previous or "") != shell_value:
        print(f"⚠ REQUESTS_CA_BUNDLE is also set in your environment ({shell_value}),")
        print("  which takes precedence over kospex.env. Unset it: unset REQUESTS_CA_BUNDLE")
    elif not shell_value and os.environ.get("CURL_CA_BUNDLE"):
        print("  (It takes precedence over the CURL_CA_BUNDLE set in your environment.)")
    print("Run `kospex connectivity` again to check.")
    print("=" * 60 + "\n")


def _show_presented_certificate(hostname, port=443):
    """Print the subject and issuer of the certificate `hostname` presents.

    Behind a TLS-intercepting proxy the issuer is the proxy's CA, which names
    the CA to install. Diagnostic only: nothing is saved. Verification is
    disabled on purpose, to read a certificate that fails it.
    """
    try:
        # Verification is intentionally disabled because this is a diagnostic
        # that must retrieve the certificate even when it can't be validated
        # (e.g. behind a TLS-intercepting proxy). Pin a modern minimum TLS
        # version so the handshake itself stays safe.
        context = ssl.create_default_context()
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        with socket.create_connection((hostname, port), timeout=10) as sock:
            with context.wrap_socket(sock, server_hostname=hostname) as ssock:
                der_cert = ssock.getpeercert(binary_form=True)
        # getpeercert() returns {} without verification, so decode the DER
        # through ssl's own test helper, which needs a file.
        with tempfile.NamedTemporaryFile("w", suffix=".pem", delete=False) as fh:
            fh.write(ssl.DER_cert_to_PEM_cert(der_cert))
        try:
            cert = ssl._ssl._test_decode_cert(fh.name)  # pylint: disable=protected-access
        finally:
            os.unlink(fh.name)
    except (OSError, ssl.SSLError, ValueError, AttributeError) as e:
        # AttributeError: _test_decode_cert is private; if a Python release
        # drops it, the details are skipped, not the command.
        print(f"\n✗ Could not read the certificate {hostname} presents: {e}")
        log.error("Could not read the certificate %s presents: %s", hostname, e)
        return
    subject = dict(x[0] for x in cert.get("subject", ()))
    issuer = dict(x[0] for x in cert.get("issuer", ()))
    print(f"\nCertificate {hostname} presented:")
    print(f"  Subject: {KospexUtils.terminal_safe(subject.get('commonName', 'N/A'))}")
    print(f"  Issuer:  {KospexUtils.terminal_safe(issuer.get('commonName', 'N/A'))}"
          "   <- behind a proxy, this is the CA to trust")
    print(f"  Valid:   {cert.get('notBefore', 'N/A')} to {cert.get('notAfter', 'N/A')}")


# Vendor integrations keep their commands in their own subpackage.
cli.add_command(snyk)


#
# Start of the main program
#

if __name__ == "__main__":
    cli()
