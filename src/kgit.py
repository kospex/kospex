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
from kospex_core import GitRepo, Kospex, RepoPathConflict, repo_path_conflict
from rich.console import Console
import kospex_utils as KospexUtils
from kospex.db.migrator import warn_if_behind
from kospex_git import KospexGit
from kospex_github import GitHubListingIncomplete, KospexGithub
import kospex.org_sync as OrgSync
import kospex.org_sync_manifest as OrgSyncManifest
from kospex.habitat_config import HabitatConfig
from kospex.run_lock import LockBusy, RunLock
from kospex_bitbucket import (
    KospexBitbucket,
    MODE_TOKEN,
    MODE_LEGACY,
    MODE_CONFIG_ERROR,
    MODE_NONE,
    REQUIRED_SCOPES,
)
from rich.table import Table
from rich.text import Text

# Initialize Kospex environment with logging
KospexUtils.init(create_directories=True, setup_logging=True, verbose=False)
kgit = KospexGit()
kospex = Kospex()
# markup=False: values printed here come from git metadata (author emails,
# remote URLs, repo_ids). Rich would parse them as markup (CWE-150), so style
# with style= or rich.text.Text instead of [tags].
console = Console(markup=False)

# Get logger using the new centralized logging system
log = KospexUtils.get_kospex_logger('kgit')


def clone_path_conflict(kospex, repo_url, force=False):
    """Report whether cloning repo_url would repoint an already-synced repo.

    Checked before the clone so no network work is spent on a repo the sync
    would refuse anyway. Returns True (and explains) when the clone should not
    go ahead.
    """
    planned = kgit.planned_clone_path(repo_url)
    if not planned:
        # Unparseable URL or one that escapes KOSPEX_CODE — clone_repo reports it.
        return False

    recorded = kospex.kospex_query.get_repo_by_id(planned["repo_id"])
    recorded_path = recorded.get("file_path") if recorded else None
    conflict, reason = repo_path_conflict(recorded_path, planned["path"], force=force)

    if conflict:
        log.error(f"refusing to clone {repo_url}: {reason}")
        console.print(Text.assemble(
            ("Refusing to clone", "bold red"),
            f" {repo_url}\n"
            f"  {planned['repo_id']} is already synced from {recorded_path}, "
            f"which still exists.\n"
            f"  Cloning to {planned['path']} would repoint it and lose which clone "
            f"the existing data came from.\n"
            f"  Re-run with -force to clone anyway, or remove the repo from the DB first."
        ))

    return conflict


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
            "fetch_age": "never" if days is None else f"{int(days)}d",
            "_sort": float("inf") if days is None else days,
        })
    # stalest first: never-fetched (inf) first, then largest age
    rows.sort(key=lambda x: x["_sort"], reverse=True)
    for x in rows:
        del x["_sort"]
    return rows


def _pull_command(path, no_prompt=False):
    """Build (argv, env_overrides) for a fast-forward-only pull of `path`.

    The one pull every kospex command uses; see KospexGit.pull_command.
    """
    return KospexGit.pull_command(path, no_prompt=no_prompt)


@click.group()
@click.version_option(version=Kospex.VERSION)
def cli():
    """kgit (Kospex Git) is a utility for doing git things with kospex use cases.

    For documentation on how commands run `kgit COMMAND --help`.

    """
    # kgit pull / clone write repos.last_fetch, which a behind DB does not have.
    warn_if_behind(kospex.kospex_db)

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
    table = Table(title="Repo Status")
    table.add_column("Subject", style="cyan", no_wrap=True)
    table.add_column("Value", justify="right")
    # get_git_stats() reports directory sizes in KB (du -k); show them in MB
    size_keys = ("total_size", "git_dir_size", "repo_size_without_git")
    for subject, details in stats.items():
        if subject in size_keys:
            details = f"{details / 1024:.3f}"
        table.add_row(subject, str(details))
    KospexUtils.print_table(table, console)
    print("Notes:")
    print("\tdirectory sizes are in MB.")
    print("\tunique authors are the number of unique authors in the last 90 days.")
    et = time.time()
    elapsed_time = et - st
    print(f"\nExecution time: {elapsed_time:.4f} seconds\n")

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
@click.option('-filename',  type=click.Path(exists=True), help="File with git clone URLs (HTTPS or SSH)")
@click.option('-force', is_flag=True, default=False,
              help="Clone even if the repo is already synced from another directory.")
@click.argument('repo',type=click.STRING, required=False)
@click.pass_context
def clone(ctx, sync, filename, force, repo):
    """
    Clone the given repo into our KOSPEX_CODE directory.

    Accepts HTTPS and scp-style SSH URLs:

    \b
    kgit clone https://github.com/ORG/REPO
    kgit clone git@github.com:ORG/REPO.git
    kgit clone -filename repos.txt
    """
    # We're going to shell out to git to do the clone
    kospex = Kospex()

    if repo and filename:
        raise click.UsageError("Specify either a repo URL or -filename, not both.")

    if not repo and not filename:
        click.echo(ctx.get_help())
        ctx.exit(0)

    if repo:
        if clone_path_conflict(kospex, repo, force):
            ctx.exit(1)

        repo_path = kgit.clone_repo(repo)
        if sync and repo_path:
            log.info(f"Syncing repository: {repo_path}")
            print("Syncing repo: " + repo_path)  # Keep user feedback
            kospex.sync_repo(repo_path, force=force)
            kospex.kospex_query.set_repo_last_fetch(kospex.git.get_repo_id())

    elif filename:
        with open(filename, "r", encoding='utf-8') as file:
            for line in file:
                repo = line.strip()
                if repo.startswith("#"):
                    log.debug(f"Skipping commented line in config: {repo}")
                    print(f"\n\nSkipping commented line: {repo}\n\n")
                elif clone_path_conflict(kospex, repo, force):
                    # One bad URL must not stop the rest of the file.
                    continue
                else:
                    repo_path = kgit.clone_repo(repo)
                    if not repo_path:
                        print(f"\n\nERROR with {repo}\n\n")

                    if sync and repo_path:
                        print("Syncing: " + repo)
                        kospex = Kospex()
                        kospex.sync_repo(repo_path, force=force)
                        kospex.kospex_query.set_repo_last_fetch(kospex.git.get_repo_id())


@cli.command("sync")
@click.option('--org', 'org', is_flag=True, default=False,
              help="TARGET is a GitHub organisation: list its repos and sync them all.")
@click.option('--user', 'user', is_flag=True, default=False,
              help="TARGET is a GitHub user: list the repos they own and sync them all.")
@click.option('-inventory', is_flag=True, default=False,
              help="List the owner's repos and save them as a run, without cloning or syncing.")
@click.option('-next', 'next_', type=click.INT, default=None,
              help="Sync at most N repos from the run, then stop. For schedules.")
@click.option('-status', is_flag=True, default=False,
              help="Show the latest run from the DB. No network.")
@click.option('-relist', is_flag=True, default=False,
              help="Close the unfinished run and start again from a fresh listing.")
@click.option('-retry-failed', is_flag=True, default=False,
              help="Also retry repos that failed earlier in the unfinished run.")
@click.option('-include-forks', is_flag=True, default=False,
              help="Sync forks. They're skipped (but recorded) by default.")
@click.option('-skip-forks', is_flag=True, default=False,
              help="Skip forks even if KOSPEX_CLONE_FORKS_DEFAULT is true.")
@click.option('-skip-archived', is_flag=True, default=False,
              help="Skip archived repos (recorded as skipped).")
@click.option('-ssh', is_flag=True, default=False, help="Clone with SSH URLs instead of HTTPS.")
@click.option('-no-prompt', is_flag=True, default=False,
              help="Make git fail instead of asking for credentials. For unattended runs.")
@click.option('-no-auth', is_flag=True, default=False,
              help="No GitHub token: public repos only, 60 API requests an hour.")
@click.option('-sync-db', is_flag=True, default=True, help="Sync the repositories to the database (Default)")
@click.argument('url', metavar="URL|TARGET", type=click.STRING, required=True)
def sync(org, user, inventory, next_, status, relist, retry_failed, include_forks,
         skip_forks, skip_archived, ssh, no_prompt, no_auth, sync_db, url):
    """
    Sync one repository, or every repository of a GitHub organisation or user.

    \b
    kgit sync https://github.com/owner/repo      one repository
    kgit sync --org acme                         every repo in an organisation
    kgit sync --user octocat                     every repo a user owns
    kgit sync --org acme -inventory              list and save the repos, clone nothing
    kgit sync --org acme -next 10                sync the next 10, then stop
    kgit sync --org acme -status                 progress so far, from the DB

    An org sync saves the listing as a run in the DB and records each repo's
    progress, so an interrupted sync resumes where it stopped. Forks are
    recorded but skipped unless -include-forks or KOSPEX_CLONE_FORKS_DEFAULT=true.
    TARGET can be `acme`, `github.com/acme`, `https://github.com/acme` or
    `github.com~acme`.
    """
    log.info(f"Starting sync operation for URL: {url}, org: {org}, user: {user}")

    if org or user:
        _sync_owner(
            owner_kind="org" if org else "user", both=org and user, target=url,
            inventory=inventory, next_=next_, status=status, relist=relist,
            retry_failed=retry_failed, include_forks=include_forks,
            skip_forks=skip_forks, skip_archived=skip_archived, ssh=ssh,
            no_prompt=no_prompt, no_auth=no_auth)
        return

    owner_only = [name for name, value in (
        ("-inventory", inventory), ("-next", next_ is not None), ("-status", status),
        ("-relist", relist), ("-retry-failed", retry_failed),
        ("-include-forks", include_forks), ("-skip-forks", skip_forks),
        ("-skip-archived", skip_archived), ("-ssh", ssh), ("-no-prompt", no_prompt),
        ("-no-auth", no_auth)) if value]
    if owner_only:
        raise click.UsageError(f"{', '.join(owner_only)} only apply with --org or --user.")

    # Single repository sync - URL should be complete repo URL
    log.info(f"Single repository sync mode for: {url}")
    console.log(f"Starting single repository sync for: {url}")

    repo_path = kgit.clone_repo(url)
    if not repo_path:
        console.print(Text.assemble(("Error:", "bold red"), f" Failed to clone {url}"))
        return
    log.info(f"Syncing repository {url} to path: {repo_path}")
    commits = kospex.sync_repo(repo_path)
    console.print(f"Synced {len(commits)} commits")


def _error(message):
    console.print(Text.assemble(("Error:", "bold red"), f" {message}"))
    exit(1)


def _sync_owner(owner_kind, both, target, inventory, next_, status, relist,
                retry_failed, include_forks, skip_forks, skip_archived, ssh,
                no_prompt, no_auth):
    """`kgit sync --org` / `--user`. See changes/202610-kgit-sync-org.md."""
    if both:
        raise click.UsageError("Use --org or --user, not both.")
    if sum([inventory, next_ is not None, status]) > 1:
        raise click.UsageError("Use one of -inventory, -next and -status.")
    if next_ is not None and next_ < 1:
        raise click.UsageError("-next needs a positive number of repositories.")
    if include_forks and skip_forks:
        raise click.UsageError("Use -include-forks or -skip-forks, not both.")

    try:
        host, owner = OrgSync.parse_target(target)
        forks = OrgSync.forks_default()
    except ValueError as e:
        raise click.UsageError(str(e))
    forks = (forks or include_forks) and not skip_forks
    key = KospexGit.generate_org_key(host, owner)
    flag = f"--{owner_kind}"
    db = kospex.kospex_db

    if status:
        _print_status(db, key, owner, flag)
        return

    gh = KospexGithub()
    if no_auth:
        console.print("No token: public repositories only, 60 API requests an hour.",
                      style="yellow")
    else:
        if not gh.get_env_credentials():
            _error("Could not find a GitHub token in the environment.\n"
                   f"Set one of: {', '.join(KospexGithub.TOKEN_ENV_VARS)} "
                   "(checked in that order), or use -no-auth for public repos only.")
        # The variable's name only, never the token.
        console.print(f"Using token from {gh.token_source}")
        if not gh.test_auth():
            _error(f"GitHub rejected the token in {gh.token_source}.")

    _check_owner_type(gh, owner, owner_kind)

    lock = RunLock(OrgSync.lock_path(HabitatConfig.get_instance().home, key),
                   f"kgit sync {flag} {owner}", OrgSync.LOCK_STALE_AFTER_SECONDS, log)
    try:
        lock.acquire()
    except LockBusy as busy:
        # Overlapping scheduled runs are normal, not an error (as osi -next).
        console.print(f"{busy}. Nothing to do.", style="yellow")
        return

    try:
        _sync_owner_locked(db, gh, key, owner, owner_kind, flag, inventory, next_,
                           relist, retry_failed, forks, skip_archived, ssh,
                           no_prompt, no_auth)
    finally:
        lock.release()


def _check_owner_type(gh, owner, owner_kind):
    """Stop unless `owner` is the kind of account the flag says it is."""
    url = f"{gh.base_url}/users/{owner}"
    data, response = gh._make_request(url)
    if not data:
        if response is not None and response.status_code == 404:
            _error(f"No GitHub organisation or user called {owner!r}.")
        _error(f"Couldn't look up {owner!r} on GitHub ({gh._failure_reason(response)}).")

    kind = data.get("type")
    if owner_kind == "org" and kind == OrgSync.GITHUB_USER:
        _error(f"{owner!r} is a GitHub user, not an organisation. "
               f"Use: kgit sync --user {owner}")
    if owner_kind == "user" and kind == OrgSync.GITHUB_ORG:
        _error(f"{owner!r} is a GitHub organisation, not a user. "
               f"Use: kgit sync --org {owner}")
    if kind not in (OrgSync.GITHUB_ORG, OrgSync.GITHUB_USER):
        _error(f"{owner!r} is a GitHub {kind}, which kgit sync can't list.")


def _list_items(gh, owner, forks, skip_archived, no_auth):
    """List the owner's repos as manifest items. Stops on an incomplete listing."""
    try:
        repos = gh.get_repos(owner, no_auth=no_auth)
    except GitHubListingIncomplete as e:
        _error(f"{e}\nNothing was saved or synced. Re-run to retry.")
    items, others = OrgSync.build_items(repos, owner, include_forks=forks,
                                        skip_archived=skip_archived)
    if others:
        console.print(f"Left out {others} repos owned by other accounts "
                      "(ones this account collaborates on or is a member of).")
    return items


def _sync_owner_locked(db, gh, key, owner, owner_kind, flag, inventory, next_,
                       relist, retry_failed, forks, skip_archived, ssh,
                       no_prompt, no_auth):
    run = OrgSyncManifest.unfinished_run(db, key)

    if inventory:
        items = _list_items(gh, owner, forks, skip_archived, no_auth)
        if run and not relist:
            new, gone = OrgSync.compare_with_run(db, run["run_id"], items)
            counts = OrgSyncManifest.run_counts(db, run["run_id"])
            console.print(f"Listed {len(items):,} repos for {key} (not saved)")
            console.print(
                f"Run {run['run_id']} is unfinished: {counts['synced']} synced, "
                f"{counts['failed']} failed, "
                f"{counts['pending'] + counts['in_progress']} pending.")
            console.print(f"  {len(new)} repos are new since that run, {len(gone)} are gone. "
                          "Kept the unfinished run.")
            console.print(f"To replace it with this listing: "
                          f"kgit sync {flag} {owner} -inventory -relist")
            return
        run_id = OrgSyncManifest.save_listing(db, key, items, owner_type=owner_kind,
                                              replace=bool(run))
        _print_inventory(db, run_id, key, saved=True)
        console.print(f"Nothing cloned. Run `kgit sync {flag} {owner} -next 10` "
                      "to start syncing.")
        return

    if run and not relist:
        run_id = run["run_id"]
        counts = OrgSyncManifest.run_counts(db, run_id)
        console.print(
            f"Resuming run {run_id} for {key}: {counts['synced']} synced, "
            f"{counts['skipped']} skipped, {counts['failed']} failed, "
            f"{counts['pending'] + counts['in_progress']} to do.")
    else:
        items = _list_items(gh, owner, forks, skip_archived, no_auth)
        run_id = OrgSyncManifest.save_listing(db, key, items, owner_type=owner_kind,
                                              replace=bool(run))
        _print_inventory(db, run_id, key, saved=True)

    todo = OrgSyncManifest.next_items(db, run_id, n=next_, retry_failed=retry_failed)
    if not todo:
        console.print("Nothing to sync in this run.")
    else:
        console.print(f"\nSyncing {len(todo)} repos\n")

    no_prompt_env = OrgSync.no_prompt_env() if no_prompt else {}
    saved_env = {name: os.environ.get(name) for name in no_prompt_env}

    def clone(url):
        return kgit.clone_repo(url)

    def sync_one(path, repo_id):
        kospex.sync_repo(path)
        # Stamp the id the sync wrote, which comes from the clone's remote, as
        # `kgit clone` does. It matches the listing's id unless the remote was
        # rewritten (insteadOf) or the clone predates a rename.
        synced_id = kospex.git.get_repo_id() or repo_id
        if synced_id != repo_id:
            log.warning(f"{repo_id} synced as {synced_id}: the clone's remote differs")
        kospex.kospex_query.set_repo_last_fetch(synced_id)

    def conflict(url):
        return clone_path_conflict(kospex, url)

    def on_item(item, item_status, detail):
        style = {"synced": "green", "skipped": "yellow", "failed": "red"}[item_status]
        line = f"{item_status.upper():8} {item['_repo_id']}"
        console.print(line + (f": {detail}" if detail else ""), style=style)

    # clone_repo builds its own git command, so -no-prompt goes through the
    # environment, and only for this loop.
    os.environ.update(no_prompt_env)
    try:
        done = OrgSync.sync_items(db, run_id, todo, clone=clone, sync=sync_one,
                                  conflict=conflict, use_ssh=ssh, on_item=on_item)
    except KeyboardInterrupt:
        console.print(f"\nInterrupted. Resume with: kgit sync {flag} {owner}",
                      style="yellow")
        raise SystemExit(130)
    finally:
        for name, value in saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    closed = OrgSyncManifest.close_run_if_done(db, run_id)
    counts = OrgSyncManifest.run_counts(db, run_id)
    remaining = counts["pending"] + counts["in_progress"]
    console.print(
        f"\nThis run: synced {done['synced']}, skipped {done['skipped']}, "
        f"failed {done['failed']}.")
    if closed:
        console.print(f"Run {run_id} is finished: {counts['synced']} synced, "
                      f"{counts['skipped']} skipped, {counts['failed']} failed.")
    else:
        more = f" -next {next_}" if next_ else ""
        console.print(f"{remaining} repos still to do. Continue with: "
                      f"kgit sync {flag} {owner}{more}")
    if counts["failed"]:
        console.print(f"See the failures with: kgit sync {flag} {owner} -status")


def _print_inventory(db, run_id, key, saved):
    inv = OrgSyncManifest.inventory(db, run_id)
    console.print(f"Listed {inv['listed']:,} repos for {key}"
                  + (f", saved as run {run_id}" if saved else ""))
    console.print(f"  active {inv['active']:>9,}     archived {inv['archived']:>7,}     "
                  f"forks {inv['forks']:>7,}")
    console.print(f"  in kospex {inv['in_kospex']:>6,}     not yet {inv['not_in_kospex']:>8,}     "
                  f"changed since last fetch {inv['changed_since_fetch']:,}")
    if inv["gone"]:
        console.print(f"  in kospex but not in this listing: {len(inv['gone'])}    (see -status)")


def _print_status(db, key, owner, flag):
    """The latest run, from the DB only."""
    run = OrgSyncManifest.latest_run(db, key)
    if not run:
        console.print(f"No sync runs for {key}. Start one with: "
                      f"kgit sync {flag} {owner} -inventory")
        return
    run_id = run["run_id"]
    counts = OrgSyncManifest.run_counts(db, run_id)
    state = f"finished {run['finished_at']}" if run["finished_at"] else "unfinished"
    console.print(f"Run {run_id} for {key} ({run['owner_type']}), "
                  f"started {run['started_at']}, {state}")
    console.print(f"  synced {counts['synced']}, skipped {counts['skipped']}, "
                  f"failed {counts['failed']}, pending {counts['pending']}, "
                  f"in progress {counts['in_progress']}")
    _print_inventory(db, run_id, key, saved=False)

    skipped = list(db.query(
        f"SELECT detail, COUNT(*) AS n FROM {OrgSyncManifest.TBL_ORG_SYNC_ITEMS} "
        "WHERE run_id = ? AND status = ? GROUP BY detail ORDER BY n DESC",
        [run_id, OrgSyncManifest.SKIPPED]))
    if skipped:
        console.print("  skipped: " + ", ".join(f"{r['detail']} {r['n']}" for r in skipped))

    failed = list(db.query(
        f"SELECT _repo_id, detail FROM {OrgSyncManifest.TBL_ORG_SYNC_ITEMS} "
        "WHERE run_id = ? AND status = ? ORDER BY _repo_id",
        [run_id, OrgSyncManifest.FAILED]))
    if failed:
        table = Table(title=f"Failed in run {run_id}")
        table.add_column("repo_id", style="cyan", no_wrap=True)
        table.add_column("detail")
        for row in failed:
            table.add_row(row["_repo_id"], row["detail"] or "")
        KospexUtils.print_table(table, console)

    gone = OrgSyncManifest.inventory(db, run_id)["gone"]
    if gone:
        console.print(f"In kospex but not in this listing ({len(gone)}):")
        for repo_id in gone:
            console.print(f"  {repo_id}")


def _git_pull(path, no_prompt=False):
    """Fast-forward the clone at `path`. Returns (ok, detail, commits).

    ok=False on missing dir / non-ff / auth-or-network failure (detail says why).
    commits = number of commits fast-forwarded (0 if already up to date).
    """
    if path is None:
        return False, "no local path recorded", 0
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
        table = Table(title="Repo Staleness")
        table.add_column("repo_id", style="cyan", no_wrap=True)
        table.add_column("last_fetch")
        table.add_column("last_sync")
        table.add_column("last_commit")
        table.add_column("fetch_age", justify="right", style="magenta")
        for row in _staleness_rows(repos):
            table.add_row(row["repo_id"], row["last_fetch"], row["last_sync"],
                          row["last_commit"], row["fetch_age"])
        KospexUtils.print_table(table, console)
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
@click.option('-test-auth', is_flag=True, default=False,
              help=f"Test the GitHub token ({', '.join(KospexGithub.TOKEN_ENV_VARS)}) can authenticate.")
@click.option('-out-repo-list', type=click.Path(), help="File to write clone URLs to.")
@click.option('-ssh-clone-url',is_flag=True, help="Write SSH clone urls to file instead of HTTPS")
@click.argument('owner', type=click.STRING, required=False)
def github(no_auth, sync, test_auth, out_repo_list, ssh_clone_url, owner):
    """
    Interact with the GitHub API.

    For authenticated access, set a GitHub Personal Access Token (PAT) in
    one of these environment variables, checked in this order:
    GITHUB_AUTH_TOKEN, GH_TOKEN, GITHUB_TOKEN. Empty values are ignored.

    """
    gh = KospexGithub()
    repos = []

    if test_auth:
        token_vars = ", ".join(KospexGithub.TOKEN_ENV_VARS)
        found = gh.get_env_credentials()
        if found:
            # The variable's name only, never the token.
            print(f"Using token from {gh.token_source}")
        else:
            print("Could not find a GitHub token in the environment.")
            print(f"Set one of: {token_vars} (checked in that order).")
            exit(1)

        if gh.test_auth():
            print("Authentication successful.")
        else:
            print(f"Authentication failed. Check the token in {gh.token_source}")
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
    try:
        repos = gh.get_repos(owner, no_auth=no_auth)
    except GitHubListingIncomplete as e:
        # Nothing is cloned, printed or written from a partial list: a short
        # -out-repo-list file would silently drop repos from `kgit clone -filename`.
        console.print(Text.assemble(("Error:", "bold red"), f" {e}"))
        console.print("No repos were synced and no repo list was written. Re-run to retry.")
        exit(1)

    if repos:
        for repo in repos:
            if sync:
                clone_url = repo.get('clone_url')
                if clone_path_conflict(kospex, clone_url):
                    # One conflicting repo must not stop the rest of the org.
                    continue
                repo_path = kgit.clone_repo(clone_url)
                if not repo_path:
                    print(f"ERROR: failed to clone {clone_url}, skipping")
                    continue
                print(f"Syncing repo: {clone_url} in directory {repo_path}")
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


# Parked: DuckDB proof of concept, being researched further for feasibility and
# usefulness. Commented out rather than deleted so it can be reviewed and restored.
# The duckdb dependency is commented out in pyproject.toml alongside it.
# @cli.command("sync-repo")
# @click.option('-verbose', is_flag=True, default=False, help="Show detailed sync output")
# @click.option('-force-full', is_flag=True, default=False, help="Force full sync even if already synced")
# @click.argument('repo', type=click.STRING, required=True)
# def sync_repo(verbose, force_full, repo):
#     """
#     Sync a git repository to the DuckDB database.
#
#     REPO can be either:
#     - A git URL (https://github.com/owner/repo) - will clone if needed
#     - A local path to an existing git repository
#
#     Examples:
#         kgit sync-repo https://github.com/kospex/kospex
#         kgit sync-repo /path/to/local/repo
#         kgit sync-repo -verbose https://github.com/owner/repo
#         kgit sync-repo -force-full /path/to/repo
#
#     The command will:
#     1. Verify DuckDB database exists (suggest 'kospex init-duckdb' if not)
#     2. Clone the repository if a URL is provided and repo doesn't exist
#     3. Perform full sync if never synced, incremental sync otherwise
#     """
#     console.print("[bold yellow]Warning:[/bold yellow] sync-repo (DuckDB) is experimental, no UI available")
#     log.info(f"Starting sync-repo for: {repo}")
#
#     # Import DuckDB components (deferred to avoid requiring duckdb for other commands)
#     from kospex import GitIngest, GitDuckDB
#
#     # Step 1: Check DuckDB database exists
#     db = GitDuckDB()
#     try:
#         db.connect(create_new=False, verbose=verbose)
#     except FileNotFoundError:
#         log.error("DuckDB database not found")
#         console.print("[bold red]Error:[/bold red] DuckDB database does not exist.")
#         console.print("\nTo initialize the DuckDB database, run:")
#         console.print("  [cyan]kospex init-duckdb[/cyan]")
#         exit(1)
#
#     # Step 2: Determine if repo is URL or local path
#     repo_path = None
#     if repo.startswith(('http://', 'https://', 'git@')):
#         # It's a URL - clone if needed
#         log.info(f"Repository URL detected: {repo}")
#         repo_path = kgit.clone_repo(repo)
#         if not repo_path:
#             log.error(f"Failed to clone repository: {repo}")
#             console.print(f"[bold red]Error:[/bold red] Failed to clone {repo}")
#             db.close()
#             exit(1)
#         console.print(f"Repository cloned/updated: {repo_path}")
#     else:
#         # It's a local path
#         repo_path = os.path.abspath(repo)
#         if not os.path.isdir(repo_path):
#             log.error(f"Directory does not exist: {repo_path}")
#             console.print(f"[bold red]Error:[/bold red] Directory does not exist: {repo_path}")
#             db.close()
#             exit(1)
#         if not KospexUtils.is_git(repo_path):
#             log.error(f"Not a git repository: {repo_path}")
#             console.print(f"[bold red]Error:[/bold red] Not a git repository: {repo_path}")
#             db.close()
#             exit(1)
#
#     # Step 3: Check if repo has been synced before
#     ingest = GitIngest(db)
#
#     # Use KospexGit to extract repo_id from the path
#     kg = KospexGit()
#     kg.set_repo(repo_path)
#     repo_id = kg.repo_id
#
#     last_sync_date = db.get_latest_commit_date(repo_id)
#
#     # Step 4: Perform sync
#     if last_sync_date and not force_full:
#         # Incremental sync
#         log.info(f"Performing incremental sync since {last_sync_date}")
#         console.print(f"[blue]Incremental sync[/blue] (last sync: {last_sync_date})")
#         stats = ingest.sync(
#             repo_directory=repo_path,
#             last_commit=last_sync_date,
#             verbose=verbose
#         )
#     else:
#         # Full sync
#         sync_type = "Full sync (forced)" if force_full else "Full sync (first time)"
#         log.info(f"Performing full sync for {repo_id}")
#         console.print(f"[blue]{sync_type}[/blue]")
#         stats = ingest.sync(
#             repo_directory=repo_path,
#             verbose=verbose
#         )
#
#     # Step 5: Report results
#     log.info(f"Sync complete: {stats}")
#     console.print(f"\n[bold green]Sync complete![/bold green]")
#     console.print(f"  Repository: {stats.get('repo_id', repo_id)}")
#     console.print(f"  Commits added: {stats.get('commits_added', 0)}")
#     console.print(f"  Files processed: {stats.get('files_added', 0)}")
#     console.print(f"  Sync type: {'Incremental' if stats.get('incremental') else 'Full'}")
#
#     db.close()


if __name__ == '__main__':
    cli()
