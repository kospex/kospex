#!/usr/bin/env python3
"""
The kospex git CLI helper tool.
"""
import time
import os
import json
import subprocess
import click
from prettytable import PrettyTable
from kospex_core import GitRepo, Kospex
from rich.console import Console
import kospex_utils as KospexUtils
from kospex_git import KospexGit
from kospex_github import KospexGithub
from kospex_bitbucket import (
    KospexBitbucket,
    MODE_TOKEN,
    MODE_LEGACY,
    MODE_CONFIG_ERROR,
    MODE_NONE,
    REQUIRED_SCOPES,
)
from rich.table import Table

# Initialize Kospex environment with logging
KospexUtils.init(create_directories=True, setup_logging=True, verbose=False)
kgit = KospexGit()
kospex = Kospex()
console = Console()

# Get logger using the new centralized logging system
log = KospexUtils.get_kospex_logger('kgit')


def _resolve_pull_repos(kospex_query, repo_id=None, all_flag=False, org=None, server=None):
    """Resolve the in-scope repo rows for `kgit pull`.

    Exactly one of repo_id / all_flag / org / server must be given.
    Returns the list of repo rows from the DB.
    """
    scopes = [bool(repo_id), bool(all_flag), bool(org), bool(server)]
    if sum(scopes) != 1:
        raise ValueError(
            "Specify exactly one scope: a REPO_ID, or one of --all / --org / --server."
        )
    if all_flag:
        return kospex_query.get_repos()
    if repo_id:
        return kospex_query.get_repos(repo_id=repo_id)
    if org:
        return kospex_query.get_repos(org_key=org)
    return kospex_query.get_repos(server=server)


def _staleness_rows(repos, now=None):
    """Build offline staleness display rows, sorted stalest-first.

    now: ISO string reference time (defaults to now); used so the function is
    deterministic under test.
    """
    if now is None:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).astimezone().isoformat()

    def _age_days(iso):
        if not iso:
            return None
        return KospexUtils.days_between_datetimes(iso, now)

    rows = []
    for r in repos:
        days = _age_days(r.get("last_fetch"))
        rows.append({
            "repo_id": r.get("_repo_id"),
            "last_fetch": r.get("last_fetch") or "never",
            "last_sync": r.get("last_sync") or "-",
            "last_commit": r.get("last_seen") or "-",
            "age": "never" if days is None else f"{int(days)}d",
            "_sort": float("inf") if days is None else days,
        })
    # stalest first: never-fetched (inf) first, then largest age
    rows.sort(key=lambda x: x["_sort"], reverse=True)
    for x in rows:
        del x["_sort"]
    return rows


def _pull_command(path, no_prompt=False):
    """Build (argv, env_overrides) for a fast-forward-only pull of `path`.

    no_prompt: make git non-interactive (fail fast) instead of invoking the
    credential helper — for unattended runs.
    """
    argv = ["git", "-C", path]
    env = {}
    if no_prompt:
        argv += ["-c", "credential.interactive=false"]
        env["GIT_TERMINAL_PROMPT"] = "0"
    argv += ["pull", "--ff-only"]
    return argv, env


@click.group()
@click.version_option(version=Kospex.VERSION)
def cli():
    """kgit (Kospex Git) is a utility for doing git things with kospex use cases.

    For documentation on how commands run `kgit COMMAND --help`.

    """

@cli.command("status")
@click.argument('repo', required=False, type=GitRepo())
# pylint: disable=unused-argument
def status(repo):
    """
    Show date and commit metadata for the given repo directory.
    """
    print()
    if repo is None:
        current_dir = os.getcwd()
        git_base = KospexUtils.find_git_base(current_dir)
        if git_base is None:
            console.log("No git repository found")
            return
        elif current_dir != git_base:
            console.print("You are not in the root directory of the git repository")
            console.print("Using the base repo directory.")
        repo = git_base

    console.print(f"\nRepo status for path: {repo}\n")

    st = time.time()
    stats = KospexUtils.get_git_stats(repo, 90)
    table = PrettyTable()
    table.field_names = ["Subject", "Value"]
    table.align["Subject"] = "l"
    table.align["Value"] = "r"
    for subject, details in stats.items():
        table.add_row([subject, details])
    print(table)
    print("Notes:")
    print("\tdirectory sizes are in KB.")
    print("\tunique authors are the number of unique authors in the last 90 days.")
    et = time.time()
    elapsed_time = et - st
    print('\nExecution time:', elapsed_time, 'seconds', "\n")

#@cli.command("mailmap")
@click.option('-sync', is_flag=True, default=False, help="Sync .mailmap to the database (Default)")
@click.argument('filename', required=False, type=click.Path(exists=True))
def mailmap(sync, filename):
    """
    Parse a .mailmap file and disply
    If the -sync is passed, sync the mailmap file to the kospex database.
    """
    mmap = KospexUtils.parse_mailmap(filename)
    for entry in mmap:
        print(entry)

#@cli.command("branches")
@click.option('-sync', is_flag=True, default=False, help="Sync branches to the database")
@click.argument('repo', type=GitRepo())
def branches(sync, repo):
    """
    Show the branches for a given repo
    If the -sync is passed, sync the branches to the kospex database.
    """
    kgit.set_repo(repo)
    os.chdir(repo)
    cmd = ['git', 'branch', '--all']
    result = subprocess.run(cmd, capture_output=True, text=True).stdout.split('\n')
    bob = []
    for i in result:
        if i.lstrip():
            bob.append(i.lstrip()) # remove leading spaces
    print(bob)

@cli.command("map-email")
@click.argument('alias',type=click.STRING, required=True)
@click.argument('email',type=click.STRING, required=True)
def map_email(alias,email):
    """
    Add an email alias to email mapping in the database.
    Used to consolidate/normalised addresses for per developer statistics
    alias is the email you want to map from
    email is the email you want to map to

    Example:

    12313+xyz-abs@users.noreply.github.com person@yourcompany.com
    person@oldbrand.com person@yourcompany.com
    """
    console.log(f"Attempting to map email alias {alias} to email {email}\n")
    # Check if mapping already exists
    if kospex.kospex_query.get_email_maps(alias=alias, email=email):
        console.log(f"Email mapping already exists for {alias} -> {email}", style="bold yellow")
        return
    else:
        map_entry = {
            'alias_email': alias,
            'main_email': email,
            'source': 'kgit CLI'
        }
        kospex.kospex_db.table("email_map").insert(map_entry,pk=['alias_email'])


@cli.command("list-email-mappings")
@click.option('-email',type=click.STRING, required=True)
def list_email_mappings(email):
    """
    Show all the email mappings
    the -email is the main email in the database and NOT the alias.
    """
    mappings = kospex.kospex_query.get_email_maps(email=email)
    map_num = len(mappings)
    console.log(f"\nTotal email mappings: {map_num}")
    if map_num == 0:
        console.log("No email mappings found.\n", style="bold yellow")
    else:
        table = Table(title="Email Mappings")
        table.add_column("alias_email", justify="left", style="cyan", no_wrap=True)
        table.add_column("main_email", style="magenta")
        table.add_column("source", style="bright_black")
        table.add_column("Created At", style="bright_black")

        for item in mappings:
            table.add_row(item['alias_email'], item['main_email'],
                item['source'], item['created_at'])

        console.print(table)
        console.print()

@cli.command("import-mailmap")
@click.option('-filename',  type=click.Path(exists=True), help="Mailmap file to import.")
def import_mailmap(filename):
    """
    Import mailmap file into the database.
    """
    console.log("Importing mailmap file into the database.")
    console.log("\nNOT IMPLEMENTED\n", style="bold red")

@cli.command("clone")
@click.option('-sync', is_flag=True, default=True, help="Sync the repo to the database (Default)")
@click.option('-filename',  type=click.Path(exists=True), help="File with HTTP git clone URLs")
@click.argument('repo',type=click.STRING, required=False)
def clone(sync, filename,repo):
    """
    Clone the given repo into our KOSPEX_CODE directory.
    Example:
    kgit clone https://github.com/ORG/REPO
    """
    # We're going to shell out to git to do the clone
    kospex = Kospex()

    if repo and filename:
        exit("You can't specify both a repo and a filename. Please choose one.")

    if repo:
        repo_path = kgit.clone_repo(repo)
        if sync and repo_path:
            log.info(f"Syncing repository: {repo_path}")
            print("Syncing repo: " + repo_path)  # Keep user feedback
            kospex.sync_repo(repo_path)
            kospex.kospex_query.set_repo_last_fetch(kgit.get_repo_id())

    elif filename:
        with open(filename, "r", encoding='utf-8') as file:
            for line in file:
                repo = line.strip()
                if repo.startswith("#"):
                    log.debug(f"Skipping commented line in config: {repo}")
                    print(f"\n\nSkipping commented line: {repo}\n\n")
                else:
                    repo_path = kgit.clone_repo(repo)
                    if not repo_path:
                        print(f"\n\nERROR with {repo}\n\n")

                    if sync and repo_path:
                        print("Syncing: " + repo)
                        kospex = Kospex()
                        kospex.sync_repo(repo_path)
                        kospex.kospex_query.set_repo_last_fetch(kgit.get_repo_id())


@cli.command("sync")
@click.option('--org', is_flag=True, default=False, help="Sync all repositories from an organization (URL should not include specific repo)")
@click.option('-sync-db', is_flag=True, default=True, help="Sync the repositories to the database (Default)")
@click.argument('url', type=click.STRING, required=True)
def sync(org, sync_db, url):
    """
    Sync repositories from a URL.

    Examples:
    kgit sync https://github.com/owner/repo (sync single repo)
    kgit sync --org https://github.com/owner (sync all repos from organization)
    """
    log.info(f"Starting sync operation for URL: {url}, org mode: {org}")

    if org:
        # Organization sync - URL should be like https://github.com/orgname
        log.info(f"Organization sync mode for: {url}")
        print(f"Starting organization sync for: {url}")

        # TODO: Implement organization sync logic
        # This should:
        # 1. Parse the URL to extract git provider and org name
        # 2. Use appropriate API (GitHub, BitBucket, etc.) to list all repos
        # 3. Clone each repo that doesn't exist locally
        # 4. Pull updates for repos that already exist
        # 5. Optionally sync to database

        print("Organization sync is not yet implemented")
        log.warning("Organization sync functionality is stubbed - not yet implemented")

    else:
        # Single repository sync - URL should be complete repo URL
        log.info(f"Single repository sync mode for: {url}")
        console.log(f"Starting single repository sync for: {url}")

        repo_path = kgit.clone_repo(url)
        log.info(f"Syncing repository {url} to path: {repo_path}")
        commits = kospex.sync_repo(repo_path)
        console.print(f"Synced {len(commits)} commits")


def _git_pull(path, no_prompt=False):
    """Fast-forward the clone at `path`. Returns (ok, detail, commits).

    ok=False on missing dir / non-ff / auth-or-network failure (detail says why).
    commits = number of commits fast-forwarded (0 if already up to date).
    """
    if not os.path.isdir(path):
        return False, "not cloned", 0

    def _head():
        r = subprocess.run(["git", "-C", path, "rev-parse", "HEAD"],
                           capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else None

    before = _head()
    argv, env_over = _pull_command(path, no_prompt=no_prompt)
    proc = subprocess.run(argv, capture_output=True, text=True,
                          env={**os.environ, **env_over})
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "fetch failed").strip().splitlines()
        return False, f"fetch failed: {detail[-1] if detail else 'unknown'}", 0

    after = _head()
    if before and after and before == after:
        return True, "up to date", 0
    count = 0
    if before and after:
        r = subprocess.run(["git", "-C", path, "rev-list", "--count",
                            f"{before}..{after}"], capture_output=True, text=True)
        count = int(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else 0
    return True, f"updated {count} commits", count


@cli.command("pull")
@click.option("--all", "all_flag", is_flag=True, default=False, help="All known repos")
@click.option("--org", default=None, help="Scope to an ORG_KEY, e.g. github.com~kospex")
@click.option("--server", default=None, help="Scope to a git server, e.g. github.com")
@click.option("--check", "check", is_flag=True, default=False,
              help="Offline staleness report; no pull, no network")
@click.option("--no-prompt", "no_prompt", is_flag=True, default=False,
              help="Non-interactive auth (fail fast) for unattended runs")
@click.argument("repo_id", required=False, type=click.STRING)
def pull(all_flag, org, server, check, no_prompt, repo_id):
    """Refresh the local clones kospex already knows about (git pull + sync).

    Scope is required - a REPO_ID or one of --all / --org / --server.
    """
    try:
        repos = _resolve_pull_repos(kospex.kospex_query, repo_id=repo_id,
                                    all_flag=all_flag, org=org, server=server)
    except ValueError as e:
        raise click.UsageError(str(e))

    if not repos:
        console.print("No matching repos in the kospex DB.", style="yellow")
        return

    if check:
        table = PrettyTable()
        table.field_names = ["repo_id", "last_fetch", "last_sync", "last_commit", "age"]
        table.align = "l"
        for row in _staleness_rows(repos):
            table.add_row([row["repo_id"], row["last_fetch"], row["last_sync"],
                           row["last_commit"], row["age"]])
        print(table)
        return

    updated = current = skipped = failed = 0
    for r in repos:
        rid = r.get("_repo_id")
        path = r.get("file_path")
        ok, detail, commits = _git_pull(path, no_prompt=no_prompt)
        if not ok:
            console.print(f"SKIP {rid}: {detail}", style="yellow")
            skipped += 1
            continue
        kospex.kospex_query.set_repo_last_fetch(rid)
        try:
            kospex.sync_repo(path)
        except Exception as e:  # never let one repo kill the run
            log.error(f"sync failed for {rid}: {e}")
            console.print(f"FAIL {rid}: sync error: {e}", style="red")
            failed += 1
            continue
        if commits:
            console.print(f"OK   {rid}: {detail}", style="green")
            updated += 1
        else:
            current += 1
    console.print(
        f"\nDone. updated={updated} up-to-date={current} skipped={skipped} failed={failed}"
    )


@cli.command("github")
@click.option('-no-auth', is_flag=True, help="Access the Github API unauthenticated.")
@click.option('-sync', is_flag=True, help="Clone and sync all repos to the database.")
@click.option('-test-auth', is_flag=True, default=False, help="Test GITHUB_AUTH_TOKEN can authenticate.")
@click.option('-out-repo-list', type=click.Path(), help="File to write clone URLs to.")
@click.option('-ssh-clone-url',is_flag=True, help="Write SSH clone urls to file instead of HTTPS")
@click.argument('owner', type=click.STRING, required=False)
def github(no_auth, sync, test_auth, out_repo_list, ssh_clone_url, owner):
    """
    Interact with the GitHub API.

    For authenticated access, you must set either the
    GITHUB_AUTH_TOKEN or GH_TOKEN environment variable.
    This is a Personal Access Token (PAT) with the necessary permissions.

    """
    gh = KospexGithub()
    repos = []

    if test_auth:
        found = gh.get_env_credentials()
        if found:
            print("Found Github GITHUB_AUTH_TOKEN in the environment.")
        else:
            print("Could not find Github GITHUB_AUTH_TOKEN in the environment.")
            print("Please set GITHUB_AUTH_TOKEN.")
            exit(1)

        if gh.test_auth():
            print("Authentication successful.")
        else:
            print("Authentication failed. Check your GITHUB_AUTH_TOKEN")
            exit(1)

        exit(0)
    else:
        # Need to check if we have an owner
        if not owner:
            print("You must specify an owner.")
            print("Example: kgit github [orgname] or [username]")
            exit(1)

    if no_auth:
        print("Proceeding without authentication.")
    else:
        gh.get_env_credentials()

    account_type = gh.get_account_type(owner)
    print(f"\nFinding repos for: {owner} ({account_type})\n")
    repos = gh.get_repos(owner,no_auth=no_auth)

    if repos:
        for repo in repos:
            if sync:
                clone_url = repo.get('clone_url')
                repo_path = kgit.clone_repo(clone_url)
                print(f"Syncing repo: {clone_url} in directorty {repo_path}")
                kospex.sync_repo(repo_path)

    table = kgit.get_repos_pretty_table(repos=repos)
    print(table)

    # Write out the repo list to a file
    if out_repo_list:
        with open(out_repo_list, "w", encoding='utf-8') as file:
            for repo in repos:
                if ssh_clone_url:
                    file.write(repo.get('ssh_url',"") + "\n")
                else:
                    file.write(repo.get('clone_url',"") + "\n")
        print(f"Clone URLs written to {out_repo_list}")

@cli.command("bitbucket")
@click.option('-workspace', type=click.STRING, help="Workspace to query (Mandatory)")
#@click.option('-no-auth', is_flag=True, help="Access the Github API unauthenticated.")
#@click.option('-list-repos', is_flag=True, type=click.STRING)
#@click.option('-sync', is_flag=True)
@click.option('-out-repo-list', type=click.Path(), help="File to write clone URLs to.")
@click.option('-test-auth', is_flag=True, default=False, help="Test BitBucket credentials can authenticate.")
@click.option('-out-raw', type=click.Path(), help="Output raw JSON results to the specified filename")
def bitbucket(workspace, out_repo_list, test_auth, out_raw):
    """
    Interact with the Bitbucket API to query repos in a workspace.

    \b
    Authentication (preferred — Bitbucket API token):
      Set BITBUCKET_API_TOKEN, plus one of (mutually exclusive):
        BITBUCKET_EMAIL    — recommended for REST API calls
        BITBUCKET_USERNAME — also works for REST; required for git
      One of these is effectively required — Atlassian's static
      "x-bitbucket-api-token-auth" fallback is accepted as a last resort
      but has been observed to 401 against some REST endpoints.

    Two Atlassian token types work with this command:

    \b
    * Atlassian account API token (id.atlassian.com → Security → API tokens):
      no scopes required, account-wide across Atlassian products.
    * Bitbucket-scoped API token (Bitbucket settings → API tokens): must
      include ALL of these scopes:
        - read:project:bitbucket
        - read:repository:bitbucket
        - read:workspace:bitbucket

    \b
    Authentication (legacy — being retired by Atlassian):
      BITBUCKET_USERNAME + BITBUCKET_APP_PASSWORD. The Bitbucket username
      is in "account settings" — NOT your email.
      Atlassian disables ALL existing app passwords on 2026-06-09.
      Migrate to BITBUCKET_API_TOKEN before then.

    \b
    Docs:
      https://support.atlassian.com/bitbucket-cloud/docs/api-tokens
      https://support.atlassian.com/bitbucket-cloud/docs/using-api-tokens/
    """

    bb = KospexBitbucket()
    click.echo()
    mode, reason = bb.get_env_credentials()

    if mode == MODE_TOKEN:
        print("Found Bitbucket API token credentials in the environment.")
        if os.getenv("BITBUCKET_APP_PASSWORD"):
            click.echo(
                "Note: BITBUCKET_APP_PASSWORD is also set but is being "
                "ignored — the API token wins. Unset BITBUCKET_API_TOKEN "
                "to use the legacy path.",
                err=True,
            )
    elif mode == MODE_LEGACY:
        print("Found Bitbucket app password credentials in the environment.")
        click.echo(f"WARNING: {reason}", err=True)
    elif mode == MODE_CONFIG_ERROR:
        click.echo(f"ERROR: {reason}\n", err=True)
        exit(1)
    else:  # MODE_NONE
        print("Could not find Bitbucket credentials in the environment.")
        print("Set BITBUCKET_API_TOKEN (and optionally BITBUCKET_EMAIL or "
              "BITBUCKET_USERNAME),")
        print("or the legacy BITBUCKET_USERNAME + BITBUCKET_APP_PASSWORD.")
        print("See `kgit bitbucket --help` for details.\n")
        exit(1)

    if test_auth:
        if not workspace:
            click.echo(
                "\nERROR: -test-auth requires -workspace so we can verify "
                "auth against the same endpoint get_repos uses "
                "(/2.0/repositories/{workspace}). Pass any workspace you "
                "have access to.\n",
                err=True,
            )
            exit(1)
        ok, status = bb.test_auth(workspace_id=workspace)
        if ok:
            print("Authentication successful.\n")
        elif status == 401:
            print("\nAuthentication FAILED (HTTP 401 — bad credentials).")
            print("Check your BITBUCKET_API_TOKEN (and BITBUCKET_EMAIL or "
                  "BITBUCKET_USERNAME), or for the legacy path your "
                  "BITBUCKET_USERNAME + BITBUCKET_APP_PASSWORD.\n")
        elif status == 403:
            print("\nAuthentication FAILED (HTTP 403 — token is missing "
                  "required scopes).")
            print("If you are using a Bitbucket-scoped API token, it must "
                  "carry all of:")
            for scope in REQUIRED_SCOPES:
                print(f"    {scope}")
            print("Alternatively, use an unscoped Atlassian account API "
                  "token (id.atlassian.com).\n")
        elif status == 404:
            print(f"\nAuthentication ok, but workspace '{workspace}' was "
                  "not found (HTTP 404). Check the workspace name.\n")
        else:
            print(f"\nAuthentication FAILED (HTTP {status}).\n")
        exit(0)

    if not workspace:
        print("\nERROR: You MUST specify a workspace.\n")
        exit(1)

    table = PrettyTable()
    table.field_names = ["Name", "clone_url", "is_private"]
    table.align["Name"] = "l"
    table.align["clone_url"] = "l"
    table.align["is_private"] = "c"

    repos = bb.get_repos(workspace)

    # TODO - provide an option to write this table to a CSV
    # TODO - add extra metadata like created, last updated and repo status
    for r in repos:
        #print(r.get("full_name"), bb.get_https_clone_url(r))
        table.add_row([r.get("slug"), bb.get_https_clone_url(r), r.get("is_private")])

    print(table)
    print(f"\nFound {len(repos)} repo(s) in workspace '{workspace}'.\n")

    if out_repo_list:
        with open(out_repo_list, "w", encoding='utf-8') as file:
            for r in repos:
                file.write(bb.get_https_clone_url(r) + "\n")

    if out_raw:
        with open(out_raw, "w", encoding='utf-8') as raw_file:
            raw_file.write(json.dumps(repos))


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
        kgit sync-repo -verbose https://github.com/owner/repo
        kgit sync-repo -force-full /path/to/repo

    The command will:
    1. Verify DuckDB database exists (suggest 'kospex init-duckdb' if not)
    2. Clone the repository if a URL is provided and repo doesn't exist
    3. Perform full sync if never synced, incremental sync otherwise
    """
    console.print("[bold yellow]Warning:[/bold yellow] sync-repo (DuckDB) is experimental, no UI available")
    log.info(f"Starting sync-repo for: {repo}")

    # Import DuckDB components (deferred to avoid requiring duckdb for other commands)
    from kospex import GitIngest, GitDuckDB

    # Step 1: Check DuckDB database exists
    db = GitDuckDB()
    try:
        db.connect(create_new=False, verbose=verbose)
    except FileNotFoundError:
        log.error("DuckDB database not found")
        console.print("[bold red]Error:[/bold red] DuckDB database does not exist.")
        console.print("\nTo initialize the DuckDB database, run:")
        console.print("  [cyan]kospex init-duckdb[/cyan]")
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

    # Use KospexGit to extract repo_id from the path
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


if __name__ == '__main__':
    cli()
