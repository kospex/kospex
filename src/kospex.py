#!/usr/bin/env python3
"""This is the kospex command line tool."""
import os
import os.path
import json
import logging
from datetime import datetime, timezone, timedelta
from shutil import which
import click
import requests
from kospex_core import Kospex, GitRepo
import kospex_utils as KospexUtils
from kospex_git import KospexGit
from kospex_query import KospexQuery
import kospex_schema as KospexSchema
from kospex_dependencies import KospexDependencies
import krunner_utils as KrunnerUtils

# Initialize Kospex environment with enhanced logging
KospexUtils.init(create_directories=True, setup_logging=True, verbose=False)
kospex = Kospex()

# Get logger using the new centralized logging system
log = KospexUtils.get_kospex_logger('kospex')


def _configure_runtime_logging(debug=False, verbose=False, quiet=False, log_console=False):
    """Configure logging based on runtime CLI flags."""
    import os
    import logging

    # Set environment variables for the logging system
    if debug:
        os.environ['KOSPEX_LOG_LEVEL'] = 'DEBUG'
        log.info("Debug logging enabled via CLI flag")
    elif verbose:
        os.environ['KOSPEX_LOG_LEVEL'] = 'INFO'
        log.info("Verbose logging enabled via CLI flag")
    elif quiet:
        os.environ['KOSPEX_LOG_LEVEL'] = 'ERROR'
        log.info("Quiet mode enabled via CLI flag")

    if log_console:
        os.environ['KOSPEX_CONSOLE_LOGGING'] = 'true'
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
#logging.basicConfig(filename='example.log', encoding='utf-8', level=logging.DEBUG)
#VERSION = "0.0.16" # This value should align with the pyproject.toml version for pip

@click.group(invoke_without_command=True)
@click.version_option(version=Kospex.VERSION)
@click.option('--debug', is_flag=True, default=False, help="Enable debug logging (overrides config).")
@click.option('--verbose', '-v', is_flag=True, default=False, help="Enable verbose logging.")
@click.option('--quiet', '-q', is_flag=True, default=False, help="Suppress most output (errors only).")
@click.option('--log-console', is_flag=True, default=False, help="Enable console logging in addition to file logging.")
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
    ctx.obj['debug'] = debug
    ctx.obj['verbose'] = verbose
    ctx.obj['quiet'] = quiet
    ctx.obj['log_console'] = log_console

    # Apply runtime logging configuration
    if debug or verbose or quiet or log_console:
        _configure_runtime_logging(debug, verbose, quiet, log_console)

    if ctx.invoked_subcommand is None:
        # Default behavior when no command is provided
        click.echo(ctx.get_help())
        ctx.exit(0)

@cli.command("init")
@click.option('--create', is_flag=True, default=False, help="Create the default ~/code or KOSPEX_CODE directory.")
@click.option('--verbose', is_flag=True, default=False, help="Show detailed initialization status.")
@click.option('--validate', is_flag=True, default=False, help="Validate Kospex setup and configuration.")
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

        print("\nEnvironment Variables:")
        for var, info in validation['environment_vars'].items():
            status = "✓" if info['set'] else "✗"
            print(f"  {status} {var}: {info['value'] or 'Not set'}")

        print("\nDirectories:")
        for name, info in validation['directories'].items():
            exists = "✓" if info['exists'] else "✗"
            writable = ""
            if name == 'kospex_home' and info['exists']:
                writable = " (writable)" if info['writable'] else " (not writable)"
            elif name == 'kospex_code' and info['exists']:
                writable = " (readable)" if info['readable'] else " (not readable)"
            print(f"  {exists} {info['path']}{writable}")

        if validation['logging']:
            print("\nLogging System:")
            if validation['logging'].get('directories_exist'):
                print("  ✓ Logging directories configured")
            else:
                print("  ✗ Logging directories need setup")

        if validation['recommendations']:
            print("\nRecommendations:")
            for rec in validation['recommendations']:
                print(f"  • {rec}")

        return

    # Perform initialization
    init_result = KospexUtils.init(
        create_directories=create,
        setup_logging=True,
        verbose=verbose
    )

    if verbose:
        print("\n=== Initialization Results ===")
        print(f"KOSPEX_HOME: {init_result['kospex_home']}")

        if init_result['directories_created']:
            print("\nDirectories Created:")
            for dir_path in init_result['directories_created']:
                print(f"  • {dir_path}")

        if init_result['environment_vars_set']:
            print("\nEnvironment Variables Set:")
            for var in init_result['environment_vars_set']:
                print(f"  • {var}")

        if init_result['warnings']:
            print("\nWarnings:")
            for warning in init_result['warnings']:
                print(f"  ⚠ {warning}")

        if init_result['errors']:
            print("\nErrors:")
            for error in init_result['errors']:
                print(f"  ✗ {error}")

    # Check for scc installation
    installed = which('scc')
    if not installed:
        log.warning("scc binary not found")
        print("\n⚠ Please install scc from https://github.com/boyter/scc")
        print("  This is used to count lines of code, complexity and determine the language.")
    elif verbose:
        log.info("scc binary found!")
        print("\n✓ scc binary found and ready")

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

    if not init_result['errors']:
        log.info("Kospex initialization completed successfully")
        if not verbose:
            print("✓ Kospex initialization complete!")
            print("  Run 'kospex init --validate' to check your setup")
    else:
        log.error("Kospex initialization completed with errors")


@cli.command("summary")
@click.option('-out', type=click.STRING,help="filename to write CSV results to.")
@click.option('-server', type=click.STRING, help="Git server to query.")
@click.option('-email', type=click.STRING, help="Email of user to query.")
@click.option('-active', is_flag=True, default=False, help="Find only actively modified (90 days).")
@click.option('-docker', is_flag=True, default=False, help="Summary of Docker files.")
@click.option('-dependencies', is_flag=True, default=False, help="Summary of dependencies.")
@click.option('-email-contains', type=click.STRING, help="Email of user to query.")
@click.option('-group', type=click.STRING, help="Name of group (of repos) to query with.")
@click.option('-org', type=click.STRING, help="Name of Org/Team to query with.")
@click.option('-debug', is_flag=True, default=False, help="Debug mode.")
@click.option('--verbose', is_flag=True, default=False, help="Verbose output.")
@click.pass_context
#@click.option('-', type=click.STRING, help="Name of group (of repos) to query with.")
# pylint: disable=unused-argument
def summary(ctx, out,server,email, active, docker, dependencies, email_contains,group,org,debug,verbose):
    """ Provide a summary of all the known repositories."""
    # Respect global logging settings from context
    global_verbose = ctx.obj.get('verbose', False) if ctx.obj else False
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
        print("Repo status summary")
        status = KospexUtils.count_key_occurrences(results, "status")
        status_table = KospexUtils.get_status_table(status)
        print(status_table)
        print()

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
            #docker = KospexUtils.count_key_occurrences(results, "docker")
            #docker_table = KospexUtils.get_status_table(docker)
            #print(docker_table)
            #docker_files = KrunnerUtils.find_dockerfiles_in_repos(dirs)

            docker_files = KrunnerUtils.find_dockerfiles_in_repos(repo_dirs)
            records = KospexUtils.get_git_metadata(docker_files)
            #print(records)
            docker_status = KospexUtils.count_key_occurrences(records, "status")
            docker_status_table = KospexUtils.get_status_table(docker_status)
            print(docker_status_table)

        if dependencies:
            kdeps = KospexDependencies(kospex_db=kospex.kospex_db, kospex_query=kospex.kospex_query)
            deps = []
            for d in repo_dirs:
                results = kdeps.find_dependency_files(d)
                deps.extend(results)
            #results = kdeps.find_dependency_files(directory)
            records = KospexUtils.get_all_last_commit_info(deps)
            dep_stats = KospexUtils.repo_stats(records,"author_date")
            deps_status_table = KospexUtils.get_status_table(dep_stats)
            print("\nDependencies summary")
            print(deps_status_table)


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

#@cli.command("sync")
#@click.option('-previous', type=int, help='# Commits to sync from the oldest in kospex DB.')
#@click.argument('repo', type=GitRepo())
#def sync(repo, previous):
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
@click.argument('directory', type=click.Path(exists=True))
def sync_directory(directory):
    """Sync all Git repos found in the data directory to the kospex DB."""
    # Find all the repos in the directory
    repos = KospexUtils.find_repos(directory)
    for repo in repos:
        print(f"\nSyncing {repo}")
        kospex.sync_repo(repo)

@cli.command("developers")
@click.option('-repo', type=GitRepo(),
              help='Git repo directory to assess, confirm sync status')
@click.option('-days', type=int, default=90, help='Committed in the last X days.')
@click.option('-org_key', type=click.STRING, help='Format of SERVER~ORG')
@click.option('-repo_id', type=click.STRING, help="Repo ID to query, in the format SERVER~ORG~REPO.")
@click.option('-server', type=click.STRING, help="Domain of the git server. E.g. github.com")
@click.option('-all', is_flag=True, default=False, help="Find all developer history. ")
@click.option('-out', type=click.Path(), help='filename to write CSV results to.')
# pylint: disable=unused-argument
def devs(repo, days, org_key, repo_id, server, all, out):
    """
    Information about developers history.
    """
    params = locals()

    if all:
        click.echo('Finding all developer history')
    else:
        click.echo(f'Searching for active developers (last {days} days)')

    developers = kospex.active_developers(**params)

    if all:
        click.echo(f"Found {len(developers)} developers.")

    else:
        click.echo(f"Found {len(developers)} active developers in the last {days} days.")


@cli.command("list-repos")
@click.option('-db', is_flag=True, default=False, help="List repos sync'ed to db.")
@click.option('-repo_id', is_flag=True, default=False, help="Include repo_id in output.")
@click.option('-server', type=click.STRING, help="Domain of the git server. E.g. github.com")
@click.option('-email', type=click.STRING, help="Email of user to query.")
@click.argument('directory', required=False, type=click.Path(exists=True))
def list_repos(db,repo_id,server,email,directory,):
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

    kospex.list_repos(directory,db=db,repo_id=repo_id,server=server,email=email)

    if directory and email:
        print("\nWARNING! email search ONLY works with the -db flag.\n")

@cli.command("tech-landscape")
@click.option('-repo', type=GitRepo())
@click.option('-repo_id', type=click.STRING)
@click.option("-days", type=int, default=90, help="Committed in the last X days.")
@click.option("-metadata", is_flag=True, default=False,
              help="Use file metadata, NOT Git committed filenames.")
# pylint: disable=unused-argument
def tech_landscape(repo, repo_id, days, metadata):
    """
    Show the tech landscape either by file extensions
    or scc metadata (use the -metadata switch)."""
    kwargs = locals()
    kospex.tech_landscape(**kwargs)

@cli.command("sync-metadata")
@click.option('-repo', type=GitRepo())
@click.option('-directory', type=click.Path(exists=True))
@click.option('-no_scc', is_flag=True, default=False, help="Don't use scc for stats.")
@click.option('-force', is_flag=True, default=False, help="Force a new metadata scan.")
def sync_metadata(repo,directory,no_scc,force):
    """
    Sync file metadata for either a 'repo' or 'directory' of repos.
    """
    if directory and repo:
        print("Please specify either a -repo or a -directory, not both.")
    elif directory:
        kospex.sync_metadata(directory)
    elif repo:
        data = kospex.file_metadata(repo,force=force)
        print(data)
    else:
        print("Please specify either a '-repo' or a '-directory'.")

@cli.command("hotspot")
@click.option('-repo', type=GitRepo())
@click.option('-repo_id', type=click.Path())
@click.option("-by_file", is_flag=True, default=False, help="Calculate hotspot per file.")
# pylint: disable=unused-argument
def hotspot(repo, repo_id, by_file):
    """Find hotspots in the given repo."""
    params = locals()
    print("WARNING: EXPERIMENTAL")
    if repo or repo_id:
        kospex.hotspot(**params)
    else:
        print("Please specify either a '-repo' or a '-repo_id'.")

@cli.command("health-check")
@click.argument('directory', required=False, type=click.Path(exists=True))
def health_check(directory):
    """ Run a health check on a git repository."""

    warning_message = "\nWARNING: This is an experimental WIP feature."
    print(warning_message)
    print()
    if not directory:
        #directory = os.getcwd()
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
    stats = KospexUtils.repo_stats(records,"author_date")
    print(stats)

    details['dependency_files'] = len(results)
    print(f"Found {len(results)} dependency files.")
    active_dep_files = stats.get("Active",0)
    if active_dep_files > 0:
        active_dep_files_percent = (active_dep_files / len(results)) * 100
        details['active_dependency_files'] = f"{active_dep_files_percent:.2f}%"
    else:
        details['active_dependency_files'] = "0%"

    # Get the number of total dependencies
    # Get the status label
    print(f"{warning_message}\n")
    print(KospexUtils.get_keyvalue_table(details))

@cli.command("deps")
@click.option('-repo', type=GitRepo(), help="File path to git repo.")
@click.option('-file', type=click.Path(exists=True), help="Package file to assess.")
@click.option('-directory', type=click.Path(), help="Directory to search for dependency files.")
@click.option('-dev', is_flag=True, default=False, help="Include dev/test dependencies. EXPERIMENTAL.")
@click.option('-out', type=click.STRING, help="filename to write CSV results to.")
def deps(repo, file, directory, out, dev):
    """Find dependency files or assess a specific file."""
    kquery = KospexQuery()
    kdeps = KospexDependencies(kospex_db=kospex.kospex_db, kospex_query=kquery)

    if directory:

        results = kdeps.find_dependency_files(directory)
        records = []

        if results:

            records = KospexUtils.get_all_last_commit_info(results)

            repos = [data.get('repo','Unknown') for data in records]
            unique_repos = list(set(repos))
            stats_dict = {key: KospexUtils.init_repo_stats() for key in unique_repos}
            repo_activity_stats = {key: KospexUtils.init_repo_stats() for key in KospexUtils.get_development_status_options()}
            # This will hold a Git url : status mapping
            # E.g. repos_status['https://github.com/kospex/kospex'] = 'Active'
            repo_status = {}

            if records:
                table = KospexUtils.get_dependency_files_table(records)
                print(table)

                for r in records:
                    repo = r.get('repo','Unknown')
                    file_path = r.get('file_path')
                    git_base = KospexUtils.find_git_base(file_path)
                    get_last_commit_info = KospexUtils.get_last_commit_info(git_base)
                    repo_status[repo] = get_last_commit_info.get('status')

                    if repo:
                        stats_dict[repo] = KospexUtils.add_status(stats_dict[repo], r.get('status'))
                        repo_activity_stats[repo_status[repo]] = KospexUtils.add_status(repo_activity_stats[repo_status[repo]], r.get('status'))

                    # Get repo status

            print(repo_status)

            activity_table = KospexUtils.get_repo_stats_table(stats=repo_activity_stats,fieldname="Repo Status")

            if out:
                KospexUtils.list_dict_2_csv(records,out)

            stats = KospexUtils.repo_stats(records,"author_date")
            print("\nOverall Summary of dependency files found")
            print(stats)
            status_table = KospexUtils.get_status_table(stats)
            print(status_table)

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

    elif file:

        file_path = os.path.abspath(file)
        repo_info = kospex.file_repo_details(file)
        repo_authors = 0
        if repo_info:
            results = kquery.authors_by_repo(repo_info['_repo_id'])
            if results:
                repo_authors = len(results)

        params = {}
        params['dev_deps'] = dev
        params['repo_info'] = repo_info
        params['print_table'] = True
        params['results_file'] = out

        #records = kdeps.assess(file_path, results_file=out, repo_info=repo_info,
        #                       print_table=True)
        records = kdeps.assess(file_path, **params)
        # TODO - refactor this into a function
        total = len(records)
        count = 0
        author_count = 0
        repos_observerd = []
        for record in records:
            if 'versions_behind' in record and record['versions_behind'] > 2:
                count += 1
            if 'authors' in record and record['authors']:
                if not record.get('source_repo') in repos_observerd:
                    repos_observerd.append(record.get('source_repo'))
                    author_count += record['authors']

        if total:
            print(f"Total dependencies: {total} | Outdated(>2): {count}")
            print(f"Non Compliant: {count/total*100:.2f}%")
            print(f"Compliant: {(total-count)/total*100:.2f}%")
            print()
            print(f"# Dependency Authors: {author_count}")
            if repo_authors:
                print(f"# Repo Authors: {repo_authors}")
            if author_count and repo_authors:
                print(f"Dependency:Repo Author Ratio: {(repo_authors/author_count)*100:.5f}%")
        else:
            print("No dependencies found.")

        print()

    else:
        print("Please specify either a '-repo', '-directory' or a '-file'.")

@cli.command("sca")
@click.option('-repo', type=GitRepo(), help="NOT IMPLEMENTED - File path to git repo.")
@click.option('-dev', is_flag=True, default=True, help="Include dev/test dependencies. EXPERIMENTAL.")
@click.option('-save', is_flag=False, default=True, help="Save results to kospex DB.")
@click.option('-malware', is_flag=True, default=False, help="Check for malware in dependencies.")
@click.option('-out', type=click.STRING, help="filename to write CSV results to.")
@click.argument('file_path', required=False, type=click.Path(exists=True))
def sca(repo, dev, save, malware, out, file_path):
    """
    Run a lightweight software composition analysis (SCA) task
    optionally run the maliciouspackages.com analysis of the dependencies
    -malware to enable malware analysis using the maliciouspackages.com API
    Your API_MAT needs to be set in the environment variable API_MAT
    """
    params = locals()
    api_mat = os.environ.get('API_MAT')

    if repo:
        print("NOT implemented")
        exit(1)

    if file_path:
        # Handle a dependency file (e.g. package.json, requirements.txt) and check dependencies
        results = kospex.dependencies.assess(file_path,**params)

        import pprint
        pprint.PrettyPrinter(indent=4).pprint(results)

        if malware:
            print("Checking packages for malware on maliciouspackages.com")
            if not api_mat:
                 print("API_MAT environment variable not set. Please set it to your maliciouspackages.com API key.")
                 exit(1)

            for result in results:
                package_type = result.get('package_type')
                package_name = result.get('package_name')

                if package_type and package_name:
                    print(f"Checking {package_type} package: {package_name}")
                    is_malware = kospex.dependencies.check_malware(package_type, package_name,api_mat)
                    result['malware'] = is_malware
                else:
                    print(f"Skipping package with missing type '{package_type}' or name '{package_name}'")

        kospex.dependencies.print_dependencies_table(results,malware=True)


    else:
        print("Either -repo REPO or file_path is required.\n")


@cli.command("author-tech")
@click.option('-author_email', type=click.STRING)
@click.option('-repo_id', type=click.STRING)
# pylint: disable=unused-argument
def author_tech(author_email,repo_id):
    """Show the tech landscape for a given author."""
    kwargs = locals()
    print(kwargs)
    results = kospex.kospex_query.author_tech(**kwargs)
    table = kospex.author_tech_pretty_table(results)
    print(table)

@cli.command("key-person")
@click.option('-top', type=int, default=4, help='The number of top authors to assess.')
@click.argument('directory', required=False, type=GitRepo())
def key_person(directory,top):
    """
    Identify the key people for a repo based on number of commits.

    Firstly, the top X committers of all time

    Secondly, the topic X active committers (i.e. last 90 days)

    The top X defaults to 4, but can be provided as a switch with -top Y
    """
    if not directory:
        directory = os.getcwd()
        if not KospexUtils.is_git(directory):
            print(f"Directory '{directory}' is not a git repository.")
            print("Please either cd in a git repository directory, or specify one.")
            exit(1)

    kgit = KospexGit()
    kgit.set_repo(directory)
    kquery = KospexQuery()

    table = KospexUtils.key_person_prettytable()
    headers = table.field_names

    authors = kquery.key_person(repo_id=kgit.get_repo_id(),top=top)

    for a in authors:
        table.add_row(KospexUtils.get_values_by_keys(a, headers))

    print()
    print(table)
    print()

@cli.command("orgs")
def orgs():
    """List all the organizations in the database."""
    kquery = KospexQuery()
    orgs_list = kquery.orgs()
    table = KospexUtils.orgs_prettytable()
    for o in orgs_list:
        table.add_row(KospexUtils.get_values_by_keys(o, table.field_names))
    print(table)

@cli.command("sync-dependencies")
@click.option('-file', type=click.Path(exists=True), help="The dependency file to sync.")
@click.option('-repo', type=GitRepo())
@click.option('--dev', is_flag=True, default=False, help="Include dev/test dependencies. EXPERIMENTAL.")
def sync_dependencies(repo,file,dev):
    """ Clone and sync the dependencies for a repo/dependency file."""
    kquery = KospexQuery()
    kdeps = KospexDependencies(kospex_db=kospex.kospex_db, kospex_query=kquery)

    if file or repo:
        # We need one of these
        if file:
            file_path = os.path.abspath(file)
            print(f"Syncing dependencies for file: {file}")
            repo_info = kospex.file_repo_details(file)
            records = kdeps.assess(file_path, repo_info=repo_info,
                                   print_table=True,dev_deps=dev)

            for rec in records:

                if rec.get("source_repo"):
                    print(f'About to clone and sync {rec["source_repo"]}')
                    repo_path = kospex.git.clone_repo(rec["source_repo"])
                    if repo_path:
                        kospex.sync_repo(repo_path)
                    else:
                        print("No source repo URL for this dependency.")
                else:
                    print(f"No source repo URL for this dependency {rec.get('package_name')}")

        if repo:
            print(f"Syncing dependencies for repo: {repo}")
            print("NOT IMPLEMENTED!")

    else:
        print("Please specify either a '-repo' or a '-file'.")


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
@click.option('-name', type=click.STRING, help="Name of the group")
@click.option('-add', is_flag=True, default=False, help="Add items to a group.")
@click.option('-remove', is_flag=True, default=False, help="Remove items from a group.")
@click.option('-delete', is_flag=True, default=False, help="Delete ALL items in a group.")
@click.option('-show', is_flag=True, default=False, help="List the items in a group.")
@click.option('-file', type=click.Path(), help="The group file to sync.")
@click.option('-value', type=click.STRING, help="Value to add or remove from the group.")
@click.option('-email', is_flag=True, default=False, help="Specify the values are emails.")
@click.option('-repo', is_flag=True, default=False, help="Specify the values are repo URLs.")
def groups(name,add,remove,delete,show,file,value,email,repo):
    """
    List all the groups in the database.
    """
    params = locals()
    kquery = KospexQuery()

    actions = [add, remove, delete, show]
    types = [email, repo]
    input_type = [file,value]

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

    KospexUtils.validate_only_one(actions,
                                  "Please specify either -add, -list, -remove or -delete.")

    if remove:
        print("Not implemented yet.")
        exit(1)

    if add:
        KospexUtils.validate_only_one(types,
                                      "Please specify either -email or -repo.")

    lines = []

    if file:
        with open(file, 'r') as content:
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
            record['group_name'] = name
            record[data_keyname] = l
            record['_repo_id'] = ""
            record['email'] = ""
            if repo:
                kgit.set_remote_url(l)
                #record['_repo_id'] = KospexUtils.git_url_to_repo_id(l)
                record['_repo_id'] = kgit.get_repo_id()
                print(f"Repo ID: {record['_repo_id']}")

            record['data_type'] = data_keyname
            records.append(record)

        kospex.kospex_db.table(KospexSchema.TBL_GROUPS).upsert_all(records,pk=['group_name', '_repo_id', 'email'])

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
@click.option('-days', type=int, default=90, help='Committed in X days is considered active.(default 90)')
@click.option('-window', type=int, default=365, help='Days to consider for orphaned repos. (default 365)')
@click.option('-server', type=click.STRING, help="Git server to query.")
@click.option('-target-list', type=click.Path(exists=True),
    help="A file containing repos to check.")
def orphans(days,window,server,target_list):
    """
    Find orphaned repos.

    An orphaned repo is one where:

     - all the developers who've committed in the window (default 365 days) \
     - have not been active in the last X days (default 90 days).

    """
    print("Experimental - Work in Progress.")
    params = locals()

    active_devs = kospex.active_developers(**params)
    active_set = set(map(lambda item: item['author'], active_devs))

    # repos will contain an array of dicts, we need the _repo_id
    repos = []

    if target_list:
        # Process the file
        with open(target_list, 'r') as file:
                    for line in file:
                        # Skip empty lines
                        kospex.git.set_remote_url(line)
                        repo_id = kospex.git.repo_id
                        if repo_id:
                            # Create dict with _repo_id key and add to repos array
                            repo_dict = {"_repo_id": repo_id}
                            repos.append(repo_dict)
    else:
        # Find all the repos in the database
        repos = kospex.kospex_query.repos(server=server)

    now_utc = datetime.now(timezone.utc)
    from_date = now_utc - timedelta(days=window)

    table = KospexUtils.orphan_prettytable()
    orphaned = 0
    working_knowledge = 0

    # Loop through every repo
    for r in repos:
        # find all the authors in the last 'window' days
        row = []
        row.append(r["_repo_id"])
        print(f"repo_id: {r['_repo_id']}")
        commits = kospex.kospex_query.commits(
            repo_id=r["_repo_id"],
            after=from_date.strftime("%Y-%m-%dT%H:%M:%S%z"))

        committers = set([c['committer_email'] for c in commits])
        row.append(len(committers))

        intersection_count = len(committers.intersection(active_set))

        row.append(intersection_count)

        print(f"Present: {intersection_count}, Total: {len(committers)} in 12 months.")

        # if all the authors are not in the active_devs_emails list
        # then print the repo status of orphaned

        if intersection_count == 0:
            print("Orphaned")
            row.append(True)
            row.append("0%")
            orphaned += 1
        else:
            print("Working knowledge exists")
            row.append(False)
            row.append(f"{intersection_count/len(committers)*100:.2f}%")
            working_knowledge += 1

        table.add_row(row)
        print()

    print()
    print(table)
    print(f"\nOrphaned: {orphaned} | Working Knowledge: {working_knowledge} | Total: {len(repos)}")
    print(f"Orphaned: {orphaned/len(repos)*100:.2f}% | Working Knowledge: {working_knowledge/len(repos)*100:.2f}%\n")

    print()

@cli.command("metadata")
@click.option('-file_type', type=click.STRING, help="Only returns files of this file type.")
@click.option('-repo_id', type=click.STRING, help="repo_id to query kospex DB for metadata.")
@click.option('-sync', type=click.BOOL, help="Sync metadata to kospex DB.")
def metadata(file_type,repo_id,sync):
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

    table = kospex.cli_file_metadata(repo_dir=dir, file_type=file_type,
       repo_id=repo_id, sync=sync)

    print(table)

    if table:
        print(f"Files: {len(table.rows)}")

@cli.command("upgrade-db")
@click.option('-apply', is_flag=True, default=False, help="Confirm and apply upgrades kospex DB schema.")
def upgrade_db(apply):
    """
    Perform an upgrade and apply DB changes for new versions.
    Run without options, it inspects the database and current kospex schema and detects changes
    -apply will execute the alter table commands and update the db.
    """
    click.echo(f"Kospex CLI version {Kospex.VERSION}")
    # WARN about backups first
    print("\nWARNING: backup your database before performing ANY upgrade.\nJust in case ...\n")
    # Check versions
    # run the db diff
    # apply db diff
    # output status

    data = kospex.kospex_query.get_kospex_db_version()
    version = 0
    if data is not None:
        # try:
        #     version = int(data)
        # except (ValueError, TypeError):
        #     # Set to 0, meaning we're in an unknown state, but we'll try an upgrade
        #     invalid_version = True
        version = data
        #print(f"INVALID kospex db version '{data}'.\nSetting to 0 for need of an upgrade")
        print(f"kospex db version: {version}")
    else:
        print("We don't have a kospex db version, either deleted, or an older database version")
        print("Setting to 0 for need of an upgrade")

    if version < KospexSchema.KOSPEX_DB_VERSION:
        versions_behind = KospexSchema.KOSPEX_DB_VERSION - version
        print(f"database is {versions_behind} versions behind.")

    print()

    alter_db_changes = []

    rows = kospex.kospex_db.query("SELECT sql, tbl_name FROM sqlite_master where type = 'table'", [])

    for r in rows:
        tbl_name = r.get("tbl_name")
        sql = r.get("sql")
        print(f"tbl_name: {tbl_name}")
        #print(KospexUtils.parse_sql_create_columns(sql))
        if current_create := KospexSchema.DB_CREATE_STATEMENTS.get(tbl_name):
            alter_commands = KospexSchema.generate_alter_table(sql,current_create,tbl_name)
            if alter_commands:
                print(f"Alter commands for table {tbl_name}")
                print(alter_commands)
                if alter_commands:
                    alter_db_changes.extend(alter_commands)
                    print("Adding to list of changes")
            else:
                print(f"No changes for table {tbl_name}")

        else:
            print("WARNING: table '{tbl_name}' is NOT a kospex databse table")

        print()

    print(f"Changes required: {len(alter_db_changes)}")
    for change in alter_db_changes:
        print(change)

    if apply:
        # Make the changes
        print("Starting alter tables")
        if len(alter_db_changes) > 0:
            results = kospex.apply_alter_table_commands(alter_db_changes)
            errors = 0
            for item in results:
                if item.get("error"):
                    print(f"error: {item.get('error')} - message: {item.get('error')}")
                    errors += 1

            if errors > 0:
               print("WARNING: Errors in applying the alter table")
            else:
                # update db version
                rec = {
                    "key": KospexSchema.KOSPEX_DB_VERSION_KEY,
                    "value": str(KospexSchema.KOSPEX_DB_VERSION),
                    "format": "INTEGER",
                    "latest": 1
                }
                kospex.kospex_db.table(KospexSchema.TBL_KOSPEX_CONFIG).upsert(rec, pk=["key", "latest"])

                print("Changes applied.")

        else:
                print("Nothing to apply.")


    elif len(alter_db_changes) > 0:
        print("\nThe above is the dry run of the changes, to apply them:")
        print("> kospex upgrade-db -apply\n")
        print("\nWARNING: BACKUP YOUR DATABASE FIRST!\n")
    else:
        print("No changes required, up to date.")

@cli.command("advisory-history")
@click.option('-ecosystem', type=click.STRING, help="E.g. npm, pypi")
@click.option('-package', type=click.STRING, help="Name of package")
def advisory_history(ecosystem, package):
    """
    Show the advisories for the previous X (default 10) versions of the package
    """
    if ecosystem and package:
        print(f"Fetching advisories for {package} in {ecosystem}...")
        # Fetch advisories logic here
        data = kospex.dependencies.deps_dev_package(ecosystem, package)
        kd = kospex.dependencies

        import pprint
        #pprint.PrettyPrinter(indent=4).pprint(data)
        for p in data.get("versions"):
            print(p.get("purl"))
            parts = kd.extract_purl(p.get("purl"))
            package_info = kd.deps_dev(parts["ecosystem"],
                parts["package_name"],
                parts["package_version"]
            )
            pprint.PrettyPrinter(indent=4).pprint(package_info)
            print("\n")
            #pprint.PrettyPrinter(indent=4).pprint(kd.extract_purl(p.get("purl")))
    else:
        print("Please provide both ecosystem and package names.")


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

    print("Database table version status\n")


    print("Installed tool status")
    print("---------------------")
    installed = which('scc')
    if installed:
        print(f"scc:\t{installed}")
    else:
        print("scc:\tNot installed")

    installed = which('git')
    if installed:
        print(f"git:\t{installed}")
    else:
        print("git:\tNot installed")

    print("\n")

#
# Start of the main program
#

if __name__ == '__main__':
    cli()
