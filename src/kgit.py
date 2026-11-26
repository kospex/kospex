#!/usr/bin/env python3
"""
The kospex git CLI helper tool.
"""
import time
import os
import json
import subprocess
from datetime import datetime, timezone
import click
from prettytable import PrettyTable
from kospex_core import GitRepo, Kospex, RepoPathConflict, repo_path_conflict
from rich.console import Console
import kospex_utils as KospexUtils
from kospex.db.migrator import warn_if_behind
from kospex_git import KospexGit
from kospex_github import GitHubListingIncomplete, KospexGithub
import kospex.github_properties as GitHubProperties
import kospex.github_dependabot as GitHubDependabot
import kospex.github_export as GitHubExport
import kospex.org_sync as OrgSync
import kospex.org_sync_manifest as OrgSyncManifest
import kospex.org_sync_command as OrgSyncCommand
from kospex import staleness
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
from kospex.integrations.gitlab.client import (
    GitLabClient as KospexGitlabClient,
    GitLabError,
    has_path_prefix,
    server_url,
)
import kospex.integrations.gitlab.queries as GitLabQueries
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
        rows = kospex_query.kospex_db.query(
            "SELECT DISTINCT _git_server, _git_owner, _git_repo FROM repos")
        server, owner = OrgSync.resolve_pull_org(
            org, [(r["_git_server"], r["_git_owner"], r["_git_repo"]) for r in rows])
        key = KospexGit.generate_org_key(server, owner)
        if org != key:
            console.print(f"Using org {key}")
        # By the stored values, not a rebuilt key: a DB from before ids were
        # lowercased can hold a mixed-case owner.
        return [r for r in kospex_query.get_repos(server=server)
                if (r.get("_git_owner") or "") == owner]
    return kospex_query.get_repos(server=server)


def _staleness_rows(repos, now=None):
    """Offline staleness display rows, stalest first, by the shared rule.

    now: ISO string reference time (defaults to now); used so the function is
    deterministic under test. See kospex.staleness.
    """
    return staleness.staleness_rows(repos, staleness.parse_time(now) if now else None)


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
        failed, refused = [], []
        with open(filename, "r", encoding='utf-8') as file:
            for line in file:
                repo = line.strip()
                if not repo:
                    continue
                if repo.startswith("#"):
                    log.debug(f"Skipping commented line in config: {repo}")
                    print(f"\n\nSkipping commented line: {repo}\n\n")
                    continue
                # One bad URL must not stop the rest of the file, however it fails.
                try:
                    if clone_path_conflict(kospex, repo, force):
                        refused.append(repo)
                        continue
                    repo_path = kgit.clone_repo(repo)
                    if not repo_path:
                        print(f"\n\nERROR with {repo}\n\n")
                        failed.append(repo)
                    elif sync:
                        print("Syncing: " + repo)
                        kospex = Kospex()
                        kospex.sync_repo(repo_path, force=force)
                        kospex.kospex_query.set_repo_last_fetch(kospex.git.get_repo_id())
                except Exception as e:
                    shown = KospexGit._without_userinfo(repo)
                    log.error(f"clone -filename: {shown} failed: {type(e).__name__}: {e}")
                    console.print(Text.assemble(
                        ("ERROR", "bold red"), f" with {shown}: {type(e).__name__}: {e}"))
                    failed.append(repo)

        for urls, what, style in ((refused, "refused (see above)", "yellow"),
                                  (failed, "failed", "red")):
            if urls:
                console.print(f"\n{len(urls)} {what}:", style=style)
                for repo in urls:
                    # Without any 'user:password@' (CWE-532).
                    console.print(f"  {KospexGit._without_userinfo(repo)}", soft_wrap=True)
        if failed:
            ctx.exit(1)


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
@click.option('-history', is_flag=True, default=False,
              help="Show every saved listing and what changed in each, from the DB. No network.")
@click.option('-active', 'active', type=click.INT, is_flag=False,
              flag_value=OrgSync.ACTIVE_DAYS_DEFAULT, default=None, metavar="[DAYS]",
              help="Only sync repos pushed to in the last DAYS (90 if no number). "
                   "Put the target first: --org acme -active 365.")
@click.option('-refresh', is_flag=True, default=False,
              help="List the owner again and merge it into the unfinished run, keeping progress.")
@click.option('-restart', is_flag=True, default=False,
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
def sync(org, user, inventory, next_, status, history, active, refresh, restart, retry_failed,
         include_forks, skip_forks, skip_archived, ssh, no_prompt, no_auth, sync_db, url):
    """
    Sync one repository, or every repository of a GitHub organisation or user.

    \b
    kgit sync https://github.com/owner/repo      one repository
    kgit sync --org acme                         every repo in an organisation
    kgit sync --user octocat                     every repo a user owns
    kgit sync --org acme -inventory              list and save the repos, clone nothing
    kgit sync --org acme -next 10                sync the next 10, then stop
    kgit sync --org acme -status                 progress so far, from the DB
    kgit sync --org acme -history                every listing and what changed
    kgit sync --org acme -active 365             only repos pushed to in the last year
    kgit sync --org acme -inventory -refresh     list again into the unfinished run

    An org sync saves the listing as a run in the DB and records each repo's
    progress, so an interrupted sync resumes where it stopped. A resumed run
    isn't listed again unless -refresh, or -active finds the listing older
    than KOSPEX_ORG_SYNC_REFRESH_DAYS (default 2). Forks are
    recorded but skipped unless -include-forks or KOSPEX_CLONE_FORKS_DEFAULT=true.
    TARGET can be `acme`, `github.com/acme`, `https://github.com/acme` or
    `github.com~acme`.
    """
    log.info(f"Starting sync operation for URL: {url}, org: {org}, user: {user}")

    if org or user:
        _sync_owner(
            owner_kind="org" if org else "user", both=org and user, target=url,
            inventory=inventory, next_=next_, status=status, history=history, active=active,
            refresh=refresh, restart=restart, retry_failed=retry_failed,
            include_forks=include_forks, skip_forks=skip_forks,
            skip_archived=skip_archived, ssh=ssh,
            no_prompt=no_prompt, no_auth=no_auth)
        return

    owner_only = [name for name, value in (
        ("-inventory", inventory), ("-next", next_ is not None), ("-status", status),
        ("-history", history),
        ("-active", active is not None), ("-refresh", refresh), ("-restart", restart),
        ("-retry-failed", retry_failed),
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


class _Stopped(SystemExit):
    """`_error`'s exit: code 1, carrying the message for the recorded command."""

    def __init__(self, message):
        super().__init__(1)
        self.message = message


def _error(message):
    console.print(Text.assemble(("Error:", "bold red"), f" {message}"))
    raise _Stopped(message)


def _sync_log(key):
    """kgit.log prefix for an owner sync: `kgit sync github.com~acme: `, greppable per owner."""
    return f"kgit sync {key}: "


def _token_text(identity):
    """`token octocat (classic, from GH_TOKEN, expires ...)`. Never the token itself."""
    if identity.get("kind") == "none":
        return "no token"
    return (f"token {identity.get('login')} ({identity.get('kind')}, "
            f"from {identity.get('source')}, expires {identity.get('expires_at') or 'never'})")


def _sync_owner(owner_kind, both, target, inventory, next_, status, retry_failed,
                include_forks, skip_forks, skip_archived, ssh, no_prompt, no_auth,
                active=None, refresh=False, restart=False, history=False):
    """`kgit sync --org` / `--user`. See changes/202610-kgit-sync-org.md."""
    if both:
        raise click.UsageError("Use --org or --user, not both.")
    if sum([inventory, next_ is not None, status, history]) > 1:
        raise click.UsageError("Use one of -inventory, -next, -status and -history.")
    if history and (refresh or restart or active is not None):
        raise click.UsageError("-history reads the DB only: use it without "
                               "-active, -refresh or -restart.")
    if next_ is not None and next_ < 1:
        raise click.UsageError("-next needs a positive number of repositories.")
    if include_forks and skip_forks:
        raise click.UsageError("Use -include-forks or -skip-forks, not both.")
    if refresh and restart:
        raise click.UsageError("Use -refresh or -restart, not both.")
    if status and (refresh or restart):
        raise click.UsageError("-status reads the DB only: it can't -refresh or -restart.")
    if active is not None and active < 1:
        raise click.UsageError("-active needs a positive number of days.")
    if active is not None and inventory:
        raise click.UsageError("-inventory saves the whole listing; -active chooses what "
                               "to sync from it. Run them separately.")

    try:
        host, owner = OrgSync.parse_target(target)
        forks = OrgSync.forks_default()
        refresh_days = OrgSync.refresh_days()
    except ValueError as e:
        raise click.UsageError(str(e))
    forks = (forks or include_forks) and not skip_forks
    key = KospexGit.generate_org_key(host, owner)
    flag = f"--{owner_kind}"
    # The options as typed: every suggestion repeats them (org_sync_command).
    typed = dict(inventory=inventory, refresh=refresh, restart=restart, active=active,
                 next_=next_, retry_failed=retry_failed, include_forks=include_forks,
                 skip_forks=skip_forks, skip_archived=skip_archived, ssh=ssh,
                 no_prompt=no_prompt, no_auth=no_auth)
    db = kospex.kospex_db

    if status:
        _print_status(db, key, owner, flag, active=active)
        return
    if history:
        _print_history(db, key, owner, flag)
        return

    gh = KospexGithub()
    if no_auth:
        console.print("No token: public repositories only, 60 API requests an hour.",
                      style="yellow")
        identity = {"login": None, "kind": "none", "source": None, "expires_at": None}
    else:
        if not gh.get_env_credentials():
            _error("Could not find a GitHub token in the environment.\n"
                   f"Set one of: {', '.join(KospexGithub.TOKEN_ENV_VARS)} "
                   "(checked in that order), or use -no-auth for public repos only.")
        # The variable's name only, never the token.
        console.print(f"Using token from {gh.token_source}")
        identity = gh.auth_identity()
        if not identity:
            _error(f"GitHub rejected the token in {gh.token_source}.")
        console.print(f"Authenticated as: {identity['login']}")

    _check_owner_type(gh, owner, owner_kind)

    lock = RunLock(OrgSync.lock_path(HabitatConfig.get_instance().home, key),
                   f"kgit sync {flag} {owner}", OrgSync.LOCK_STALE_AFTER_SECONDS, log)
    try:
        lock.acquire()
    except LockBusy as busy:
        # Overlapping scheduled runs are normal, not an error (as osi -next).
        console.print(f"{busy}. Nothing to do.", style="yellow")
        log.info(_sync_log(key) + "another run holds the lock, nothing to do")
        return

    # From here the command does something, so it's recorded (org_sync_commands):
    # before any listing or syncing, so a stopped command still shows.
    opts = OrgSyncCommand.options(**typed)
    command = OrgSyncCommand.command(flag, owner, opts)
    log.info(_sync_log(key) + f"started: {command}, {_token_text(identity)}")
    command_id = OrgSyncManifest.start_command(
        db, key, command, " ".join(opts), identity)
    record = {"run_id": None, "listing": None, "synced_any": False, "synced": 0,
              "failed": 0, "skipped": 0, "new": None, "gone": None}

    def finish(**kw):
        OrgSyncManifest.finish_command(db, command_id, **record, **kw)

    try:
        _sync_owner_locked(db, gh, key, owner, owner_kind, flag, inventory, next_,
                           retry_failed, forks, skip_archived, ssh, no_prompt, no_auth,
                           active=active, refresh=refresh, restart=restart,
                           refresh_days=refresh_days, typed=typed, record=record)
    except _Stopped as stopped:
        log.warning(_sync_log(key) + f"stopped: {stopped.message}")
        finish(error=stopped.message)
        raise
    except SystemExit as stop:
        if stop.code == 130:  # Ctrl-C during the sync loop (logged there): not finished
            finish(finished=False)
        else:
            log.warning(_sync_log(key) + f"stopped: exited with {stop.code}")
            finish(error=f"exited with {stop.code}")
        raise
    except KeyboardInterrupt:  # Ctrl-C anywhere else, e.g. while listing
        log.warning(_sync_log(key) + "interrupted")
        finish(finished=False)
        raise
    except Exception as e:
        log.error(_sync_log(key) + f"failed: {type(e).__name__}: {e}")
        finish(error=f"{type(e).__name__}: {e}")
        raise
    else:
        finish()
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


def _days(n):
    """`1 day`, `90 days`."""
    return f"{n} day{'s' if n != 1 else ''}"


def _age_text(days):
    """`3 days`, `5 hours`, `a few minutes`: how old a listing is."""
    if days is None:
        return "an unknown time"
    if days >= 1:
        return f"{days:.0f} day{'s' if round(days) != 1 else ''}"
    hours = days * 24
    if hours >= 1:
        return f"{hours:.0f} hour{'s' if round(hours) != 1 else ''}"
    return "a few minutes"


def _refresh_run(db, gh, run_id, key, owner, forks, skip_archived, no_auth):
    """List the owner again and merge it into the run. Prints what changed."""
    items = _list_items(gh, owner, forks, skip_archived, no_auth)
    changed = OrgSyncManifest.refresh_listing(db, run_id, items)
    console.print(f"Refreshed run {run_id} for {key} from a new listing of "
                  f"{len(items):,}: {_changes_text(changed)}.")


def _note_listing(db, key, record, run_id, kind):
    """Put the listing this command saved, and what it changed, in `record`."""
    last = OrgSyncManifest.listings(db, key)[-1]
    record.update(run_id=run_id, listing=kind, new=last["new"], gone=last["gone"])
    log.info(_sync_log(key) + f"{kind} listing: {last['listed']} repos, {last['new']} new, "
                              f"{last['gone']} gone, run {run_id}")


def _changes_text(changed):
    """`3 new, 1 gone, 2 newly archived`: new and gone always, the rest if any."""
    parts = [f"{changed['new']} new", f"{changed['gone']} gone"]
    for name, label in (("back", "back"), ("renamed", "renamed"),
                        ("newly_archived", "newly archived"), ("unarchived", "unarchived")):
        if changed.get(name):
            parts.append(f"{changed[name]} {label}")
    return ", ".join(parts)


def _sync_owner_locked(db, gh, key, owner, owner_kind, flag, inventory, next_,
                       retry_failed, forks, skip_archived, ssh, no_prompt, no_auth,
                       active=None, refresh=False, restart=False,
                       refresh_days=OrgSync.REFRESH_DAYS_DEFAULT, typed=None, record=None):
    """record: what this command did, filled in as it goes (org_sync_commands)."""
    record = {} if record is None else record
    run = OrgSyncManifest.unfinished_run(db, key)
    if run:
        record["run_id"] = run["run_id"]

    def suggest(**changes):
        # Every suggestion repeats the options as typed (-no-auth, -ssh, the
        # fork and archive choices...), so a copied one does what was asked.
        return OrgSyncCommand.suggestion(flag, owner, typed or {}, **changes)

    if inventory:
        if run and refresh:
            _refresh_run(db, gh, run["run_id"], key, owner, forks, skip_archived, no_auth)
            _note_listing(db, key, record, run["run_id"], OrgSyncManifest.REFRESH)
            _print_inventory(db, run["run_id"], key, saved=False)
            return
        items = _list_items(gh, owner, forks, skip_archived, no_auth)
        if run and not restart:
            new, gone = OrgSync.compare_with_run(db, run["run_id"], items)
            counts = OrgSyncManifest.run_counts(db, run["run_id"])
            console.print(f"Listed {len(items):,} repos for {key} (not saved)")
            console.print(
                f"Run {run['run_id']} is unfinished: {counts['synced']} synced, "
                f"{counts['failed']} failed, "
                f"{counts['pending'] + counts['in_progress']} pending.")
            console.print(f"  {len(new)} repos are new since that run, {len(gone)} are gone. "
                          "Kept the unfinished run.")
            log.info(_sync_log(key) + f"listed {len(items)} repos, not saved: {len(new)} new, "
                                      f"{len(gone)} gone since run {run['run_id']}")
            console.print("To merge this listing into it: "
                          + suggest(inventory=True, refresh=True))
            console.print("To replace it: " + suggest(inventory=True, restart=True))
            return
        run_id = OrgSyncManifest.save_listing(db, key, items, owner_type=owner_kind,
                                              replace=bool(run))
        _note_listing(db, key, record, run_id, OrgSyncManifest.RESTART if run else OrgSyncManifest.FIRST)
        _print_inventory(db, run_id, key, saved=True)
        console.print(f"Nothing cloned. Run `{suggest(next_=10)}` to start syncing.")
        return

    if run and not restart:
        run_id = run["run_id"]
        listed_at = OrgSyncManifest.listed_at(db, run_id)
        age = OrgSync.listing_age_days(listed_at)
        stale = active is not None and OrgSync.listing_is_stale(listed_at, refresh_days)
        if refresh or stale:
            if stale and not refresh:
                console.print(f"Run {run_id} was listed {_age_text(age)} ago, "
                              f"over {OrgSync.ENV_REFRESH_DAYS}={refresh_days}: listing again.")
            _refresh_run(db, gh, run_id, key, owner, forks, skip_archived, no_auth)
            _note_listing(db, key, record, run_id, OrgSyncManifest.REFRESH)
            age = 0
        counts = OrgSyncManifest.run_counts(db, run_id)
        console.print(
            f"Resuming run {run_id} for {key}: {counts['synced']} synced, "
            f"{counts['skipped']} skipped, {counts['failed']} failed"
            + (f", {counts['gone']} gone" if counts["gone"] else "")
            + f", {counts['pending'] + counts['in_progress']} to do.")
        if active is not None:
            inside, outside = OrgSyncManifest.window_counts(
                db, run_id, active, retry_failed=retry_failed)
            console.print(f"  {inside} to do in the last {_days(active)}, {outside} pending "
                          f"outside it. Listed {_age_text(age)} ago.")
    else:
        items = _list_items(gh, owner, forks, skip_archived, no_auth)
        run_id = OrgSyncManifest.save_listing(db, key, items, owner_type=owner_kind,
                                              replace=bool(run))
        _note_listing(db, key, record, run_id, OrgSyncManifest.RESTART if run else OrgSyncManifest.FIRST)
        _print_inventory(db, run_id, key, saved=True)
        if active is not None:
            inside, outside = OrgSyncManifest.window_counts(
                db, run_id, active, retry_failed=retry_failed)
            console.print(f"  {inside} to do in the last {_days(active)}, "
                          f"{outside} pending outside it.")

    record["synced_any"] = True
    todo = OrgSyncManifest.next_items(db, run_id, n=next_, retry_failed=retry_failed,
                                      active_days=active)
    if not todo:
        console.print("Nothing to sync in this run"
                      + (f" in the last {_days(active)}." if active is not None else "."))
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
        message = f"{item_status.upper()} {item['_repo_id']}" + (f": {detail}" if detail else "")
        if item_status == "synced":
            log.info(_sync_log(key) + message)
        else:
            log.warning(_sync_log(key) + message)

    # clone_repo builds its own git command, so -no-prompt goes through the
    # environment, and only for this loop.
    os.environ.update(no_prompt_env)
    try:
        done = OrgSync.sync_items(db, run_id, todo, clone=clone, sync=sync_one,
                                  conflict=conflict, use_ssh=ssh, on_item=on_item)
    except KeyboardInterrupt:
        console.print(f"\nInterrupted. Resume with: {suggest()}", style="yellow")
        log.warning(_sync_log(key) + f"interrupted. Resume with: {suggest()}")
        raise SystemExit(130)
    finally:
        for name, value in saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    record.update(synced=done["synced"], failed=done["failed"], skipped=done["skipped"])
    closed = OrgSyncManifest.close_run_if_done(db, run_id)
    counts = OrgSyncManifest.run_counts(db, run_id)
    remaining = counts["pending"] + counts["in_progress"]
    log.info(_sync_log(key) + f"synced {done['synced']}, skipped {done['skipped']}, "
                              f"failed {done['failed']}; "
             + (f"run {run_id} is finished" if closed
                else f"{remaining} still to do in run {run_id}"))
    console.print(
        f"\nThis run: synced {done['synced']}, skipped {done['skipped']}, "
        f"failed {done['failed']}.")
    if closed:
        console.print(f"Run {run_id} is finished: {counts['synced']} synced, "
                      f"{counts['skipped']} skipped, {counts['failed']} failed.")
    elif active is not None:
        inside, outside = OrgSyncManifest.window_counts(
            db, run_id, active, retry_failed=retry_failed)
        if inside:
            console.print(f"{inside} repos still to do in the last {_days(active)}. "
                          f"Continue with: {suggest()}")
        else:
            console.print(f"Nothing left to do in the last {_days(active)}.")
        console.print(f"{outside} repos are pending outside the window. "
                      f"A wider -active, or none, syncs them.")
    else:
        console.print(f"{remaining} repos still to do. Continue with: {suggest()}")
    if counts["failed"]:
        console.print(f"See the failures with: kgit sync {flag} {owner} -status")


def _print_inventory(db, run_id, key, saved):
    inv = OrgSyncManifest.inventory(db, run_id)
    console.print(f"Listed {inv['listed']:,} repos for {key}"
                  + (f", saved as run {run_id}" if saved else ""))
    console.print(f"  active {inv['active']:>9,}     archived {inv['archived']:>7,}     "
                  f"forks {inv['forks']:>7,}")
    visibility = (f"  public {inv['public']:>9,}     private {inv['private']:>8,}     "
                  f"internal {inv['internal']:>4,}")
    if inv["visibility_other"]:
        visibility += f"     other {inv['visibility_other']:,}"
    if inv["visibility_unknown"]:
        visibility += f"     not recorded {inv['visibility_unknown']:,}"
    console.print(visibility)
    console.print(f"  in kospex {inv['in_kospex']:>6,}     not yet {inv['not_in_kospex']:>8,}     "
                  f"changed since last fetch {inv['changed_since_fetch']:,}")
    if inv["gone"]:
        console.print(f"  in kospex but not in this listing: {len(inv['gone'])}    (see -status)")


def _print_history(db, key, owner, flag):
    """Every saved listing of the owner, oldest first, from the DB only."""
    listings = OrgSyncManifest.listings(db, key)
    if not listings:
        console.print(f"No listings for {key}. Start with: "
                      f"kgit sync {flag} {owner} -inventory")
        return
    table = Table(title=f"Listings of {key}")
    table.add_column("listed at", no_wrap=True)
    table.add_column("run", style="cyan", no_wrap=True)
    table.add_column("kind")
    columns = (("listed", "repos"), ("new", "new"), ("gone", "gone"), ("back", "back"),
               ("renamed", "renamed"), ("newly_archived", "archived+"),
               ("unarchived", "unarchived"), ("archived", "archived"),
               ("forks", "forks"), ("private", "private"), ("internal", "internal"))
    for _, label in columns:
        table.add_column(label, justify="right")
    for row in listings:
        table.add_row(row["listed_at"], row["run_id"], row["kind"],
                      *("" if row[name] is None else f"{row[name]:,}" for name, _ in columns))
    KospexUtils.print_table(table, console)
    console.print("Changes are since the listing before. archived+ is newly archived. "
                  "Which repos: first_listed_at and archived_seen_at in org_sync_items "
                  "(see docs/kgit.md).")


def _print_status(db, key, owner, flag, active=None):
    """The latest run, from the DB only. With `active`, also the window's split."""
    run = OrgSyncManifest.latest_run(db, key)
    if not run:
        console.print(f"No sync runs for {key}. Start one with: "
                      f"kgit sync {flag} {owner} -inventory")
        return
    run_id = run["run_id"]
    counts = OrgSyncManifest.run_counts(db, run_id)
    state = f"finished {run['finished_at']}" if run["finished_at"] else "unfinished"
    listed_at = run.get("listed_at") or run["started_at"]
    console.print(f"Run {run_id} for {key} ({run['owner_type']}), "
                  f"started {run['started_at']}, {state}")
    if listed_at != run["started_at"]:
        console.print(f"  listed again {listed_at}")
    listings = OrgSyncManifest.listings(db, key)
    if len(listings) > 1:
        console.print(f"  {key} listed {len(listings)} times since "
                      f"{listings[0]['listed_at']}. See them with: "
                      f"kgit sync {flag} {owner} -history")
    console.print(f"  synced {counts['synced']}, skipped {counts['skipped']}, "
                  f"failed {counts['failed']}, pending {counts['pending']}, "
                  f"in progress {counts['in_progress']}"
                  + (f", gone {counts['gone']}" if counts["gone"] else ""))
    if active is not None and not run["finished_at"]:
        inside, outside = OrgSyncManifest.window_counts(db, run_id, active)
        age = OrgSync.listing_age_days(listed_at)
        console.print(f"  {inside} to do in the last {_days(active)}, {outside} pending "
                      f"outside it (pushed dates from a listing {_age_text(age)} old)")
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
@click.option("--org", default=None,
              help="Scope to an org already in kospex: github.com~kospex, kospex, "
                   "github.com/kospex or https://github.com/kospex. Matched against "
                   "the DB, any host, ignoring case; an ambiguous name is an error.")
@click.option("--server", default=None, help="Scope to a git server, e.g. github.com")
@click.option("--check", "check", is_flag=True, default=False,
              help="Offline staleness report; no pull, no network")
@click.option("--no-prompt", "no_prompt", is_flag=True, default=False,
              help="Non-interactive auth (fail fast) for unattended runs")
@click.option("--stale", "stale", type=click.INT, default=None, metavar="DAYS",
              help="Only repos not refreshed in DAYS days (last fetch, else last sync).")
@click.argument("repo_id", required=False, type=click.STRING)
def pull(all_flag, org, server, check, no_prompt, stale, repo_id):
    """Refresh the local clones kospex already knows about (git pull + sync).

    Scope is required - a REPO_ID or one of --all / --org / --server.

    \b
    kgit pull --all --stale 30           only repos not refreshed in 30 days
    kgit pull --check --org acme --stale 7   which ones, offline
    """
    if stale is not None and stale < 1:
        raise click.UsageError("--stale needs a whole number of days, 1 or more.")
    try:
        repos = _resolve_pull_repos(kospex.kospex_query, repo_id=repo_id,
                                    all_flag=all_flag, org=org, server=server)
    except ValueError as e:
        raise click.UsageError(str(e))

    if not repos:
        console.print("No matching repos in the kospex DB.", style="yellow")
        return

    if stale is not None:
        now = datetime.now(timezone.utc)
        in_scope = len(repos)
        repos = [r for r in repos if staleness.is_stale(r, stale, now)]
        if not repos:
            console.print(f"No repos in scope were last refreshed more than "
                          f"{_days(stale)} ago.")
            return
        if not check:
            console.print(f"Pulling {len(repos)} of {in_scope} repos not refreshed "
                          f"in {_days(stale)}.")

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


def _github_export_checks(flag, org, repo_id, filename):
    """The options every `kgit github` export needs: -filename and one of -org / -repo_id.

    Returns -filename as an absolute path, so kgit.log says where a file went
    whatever directory the run was started from.
    """
    if not filename:
        raise click.UsageError(f"{flag} needs -filename.")
    if bool(org) == bool(repo_id):
        raise click.UsageError(f"{flag} needs exactly one of -org or -repo_id.")
    return os.path.abspath(filename)


def _require_github_token(gh, flag):
    if not gh.get_env_credentials():
        token_vars = ", ".join(KospexGithub.TOKEN_ENV_VARS)
        raise click.ClickException(f"{flag} needs a GitHub token. Set one of: {token_vars}")


def _github_export_targets(gh, flag, org, repo_id, listing_message):
    """(owner, repo, github_repo_id) for -repo_id, or for every repo in -org's listing.

    Nothing has been written yet, so a failure or Ctrl-C here says so.
    """
    try:
        if repo_id:
            try:
                owner, repo = GitHubProperties.parse_github_repo_id(repo_id)
            except ValueError as e:
                raise click.UsageError(str(e))
            return [GitHubProperties.lookup_repo(gh, owner, repo)]
        console.print(listing_message)
        targets = GitHubProperties.list_org_repos(gh, org)
    except GitHubListingIncomplete as e:
        log.warning("kgit github %s: listing %s failed: %s", flag, org, e)
        raise click.ClickException(f"{e}. Nothing was written. Rerun to retry.")
    except GitHubProperties.RateLimited as e:
        log.warning("kgit github %s: rate limit hit at %s before any repo was read", flag, e)
        raise click.ClickException(
            f"GitHub rate limit hit at {e}. Nothing was written. Rerun after the limit resets.")
    except KeyboardInterrupt:
        log.warning("kgit github %s: interrupted while listing %s", flag, org or repo_id)
        raise click.ClickException("Interrupted while listing repos. Nothing was written.")
    console.print(f"{len(targets)} repos")
    return targets


class _ExportProgress:
    """Console and kgit.log reporting for a per-repo `kgit github` export."""

    def __init__(self, flag, files, total, filename):
        self.flag, self.files, self.total, self.filename = flag, files, total, filename

    def repo(self, progress):
        console.print(progress)
        log.info("kgit github %s: %s", self.flag, progress)

    def stopped(self, reason):
        message = GitHubExport.stop_message(reason, self.files, self.total, self.filename)
        log.warning("kgit github %s: %s", self.flag, message)
        return message


def _github_properties_org_level(gh, org, filename, fetched_at):
    """-level org: one paginated org-wide query, written in one go (a failure writes nothing)."""
    console.print(f"Reading custom properties for {org} (org level)")
    log.info("kgit github --properties: reading %s at org level, writing %s", org, filename)
    try:
        lines = GitHubProperties.org_level_lines(gh, org, fetched_at)
    except GitHubListingIncomplete as e:
        log.warning("kgit github --properties: org level failed for %s: %s", org, e)
        if e.reason in ("HTTP 403", "HTTP 404"):
            raise click.ClickException(
                f"{e.reason} reading {org}'s properties at org level. Either the org "
                "doesn't exist or the token isn't an org member (a classic PAT also "
                "needs read:org). Nothing was written. Rerun with -level repo, which "
                "needs only read access to each repo.")
        raise click.ClickException(f"{e}. Nothing was written. Rerun to retry.")
    except KeyboardInterrupt:
        log.warning("kgit github --properties: interrupted reading %s at org level", org)
        raise click.ClickException("Interrupted. Nothing was written.")
    GitHubProperties.write_jsonl(filename, lines)
    log.info("kgit github --properties: finished, %s repos in %s (org level)",
             len(lines), filename)
    return lines


def _github_properties(gh, org, repo_id, filename, level):
    """`kgit github --properties`: see kospex.github_properties."""
    filename = _github_export_checks("--properties", org, repo_id, filename)
    if repo_id and level == GitHubProperties.LEVEL_ORG:
        raise click.UsageError("-repo_id is always repo level; drop -level org.")
    _require_github_token(gh, "--properties")

    fetched_at = GitHubProperties.now_utc()
    if org and (level or GitHubProperties.LEVEL_ORG) == GitHubProperties.LEVEL_ORG:
        lines = _github_properties_org_level(gh, org, filename, fetched_at)
    else:
        targets = _github_export_targets(
            gh, "--properties", org, repo_id,
            f"Listing {org}'s repos, then reading properties per repo (repo level)")
        log.info("kgit github --properties: %s repos for %s (repo level), writing %s",
                 len(targets), org or repo_id, filename)
        with GitHubExport.RunFiles(filename) as files:
            progress = _ExportProgress("--properties", files, len(targets), filename)

            def on_repo(index, total, line):
                files.add_repo([line])
                progress.repo(GitHubProperties.progress_line(index, total, line))

            try:
                lines = GitHubProperties.repo_level_lines(gh, targets, fetched_at,
                                                          on_repo=on_repo)
            except GitHubProperties.RateLimited as e:
                raise click.ClickException(
                    progress.stopped(f"GitHub rate limit hit at {e}")
                    + " Wait for the limit to reset.")
            except KeyboardInterrupt:
                raise click.ClickException(progress.stopped("Interrupted"))
            files.finish()
        log.info("kgit github --properties: finished, %s repos in %s (repo level)",
                 files.repos, filename)

    s = GitHubProperties.summarise(lines)
    console.print(f"Wrote {s['repos']} repos to {filename}: {s['with_properties']} with "
                  f"properties, {s['no_properties']} without, {s['errors']} errors.")
    for note in GitHubProperties.notes(lines, org_scope=bool(org)):
        console.print(f"Note: {note}")


def _github_dependabot(gh, org, repo_id, filename, level):
    """`kgit github --dependabot`: see kospex.github_dependabot."""
    filename = _github_export_checks("--dependabot", org, repo_id, filename)
    if level:
        raise click.UsageError("-level doesn't apply to --dependabot: it always reads "
                               "each repo, which also gives each repo's Dependabot status.")
    _require_github_token(gh, "--dependabot")

    status_file = GitHubDependabot.status_filename(filename)
    fetched_at = GitHubProperties.now_utc()
    targets = _github_export_targets(
        gh, "--dependabot", org, repo_id,
        f"Listing {org}'s repos, then reading open Dependabot alerts per repo")

    log.info("kgit github --dependabot: %s repos for %s, writing %s",
             len(targets), org or repo_id, filename)
    with GitHubDependabot.RunFiles(filename, status_file) as files:
        progress = _ExportProgress("--dependabot", files, len(targets), filename)

        def on_repo(index, total, status, repo_alerts):
            files.add(status, repo_alerts)
            progress.repo(GitHubDependabot.progress_line(index, total, status))

        try:
            alert_lines, status_lines = GitHubDependabot.collect(
                gh, targets, fetched_at, early_stop=bool(org), on_repo=on_repo)
        except GitHubProperties.RateLimited as e:
            raise click.ClickException(
                progress.stopped(f"GitHub rate limit hit at {e}")
                + " Wait for the limit to reset.")
        except GitHubDependabot.NoAlertAccess as e:
            raise click.ClickException(
                progress.stopped(f"This token can't see Dependabot alerts: {e}")
                + f" {GitHubDependabot.ACCESS_NEEDED}")
        except KeyboardInterrupt:
            raise click.ClickException(progress.stopped("Interrupted"))
        files.finish()

    log.info("kgit github --dependabot: finished, %s repos and %s alerts in %s",
             files.repos, files.alerts, filename)
    s = GitHubDependabot.summarise(alert_lines, status_lines)
    by_status = ", ".join(f"{n} {k}" for k, n in sorted(s["by_status"].items()))
    by_severity = ", ".join(f"{n} {k}" for k, n in sorted(s["by_severity"].items()))
    console.print(f"Wrote {s['alerts']} open alerts to {filename}"
                  + (f" ({by_severity})" if by_severity else ""))
    console.print(f"Wrote {s['repos']} repo statuses to {status_file}: {by_status}")
    for note in GitHubDependabot.notes(status_lines, org_scope=bool(org)):
        console.print(f"Note: {note}")


@cli.command("github")
@click.option('-no-auth', is_flag=True, help="Access the Github API unauthenticated.")
@click.option('-sync', is_flag=True,
              help="Clone and sync the owner's repos: runs `kgit sync --org` / `--user`.")
@click.option('-include-member', is_flag=True, default=False,
              help="For a user: also list repos they don't own (ones they collaborate "
                   "on or reach through an organisation). By default only their own.")
@click.option('-test-auth', is_flag=True, default=False,
              help=f"Test the GitHub token ({', '.join(KospexGithub.TOKEN_ENV_VARS)}) can authenticate.")
@click.option('-out-repo-list', type=click.Path(), help="File to write clone URLs to.")
@click.option('-ssh-clone-url',is_flag=True, help="Write SSH clone urls to file instead of HTTPS")
@click.option('-properties', '--properties', 'properties', is_flag=True,
              help="Write custom property values to -filename as JSONL, one line per repo.")
@click.option('-dependabot', '--dependabot', 'dependabot', is_flag=True,
              help="Write open Dependabot alerts to -filename as JSONL, one line per alert, "
                   "and each repo's Dependabot status to FILENAME-status.jsonl.")
@click.option('-org', 'prop_org', default=None,
              help="With --properties or --dependabot: every repo GitHub lists for this org.")
@click.option('-repo_id', 'prop_repo_id', default=None,
              help="With --properties or --dependabot: one repo, e.g. github.com~acme~payments.")
@click.option('-filename', 'prop_filename', type=click.Path(dir_okay=False), default=None,
              help="With --properties or --dependabot: JSONL output file (overwritten). Required.")
@click.option('-level', 'prop_level', type=click.Choice(['org', 'repo']), default=None,
              help="With --properties: 'org' (default for -org) is one org-wide query and "
                   "needs org membership; 'repo' is one request per repo and needs only "
                   "read access.")
@click.argument('owner', type=click.STRING, required=False)
def github(no_auth, sync, include_member, test_auth, out_repo_list, ssh_clone_url,
           properties, dependabot, prop_org, prop_repo_id, prop_filename, prop_level, owner):
    """
    Interact with the GitHub API.

    For authenticated access, set a GitHub Personal Access Token (PAT) in
    one of these environment variables, checked in this order:
    GITHUB_AUTH_TOKEN, GH_TOKEN, GITHUB_TOKEN. Empty values are ignored.

    \b
    List an owner's repos:
      kgit github acme                     an organisation's repos
      kgit github octocat                  the repos a user owns
      kgit github octocat -include-member  also ones they don't own
      kgit github acme -sync               same as: kgit sync --org acme

    \b
    Custom properties to JSONL:
      kgit github --properties -org acme -filename props.jsonl [-level repo]
      kgit github --properties -repo_id github.com~acme~payments -filename props.jsonl

    \b
    Open Dependabot alerts to JSONL (also writes alerts-status.jsonl):
      kgit github --dependabot -org acme -filename alerts.jsonl
      kgit github --dependabot -repo_id github.com~acme~payments -filename alerts.jsonl

    """
    gh = KospexGithub()
    repos = []

    if properties and dependabot:
        raise click.UsageError("Use --properties or --dependabot, not both.")
    if properties:
        _github_properties(gh, prop_org, prop_repo_id, prop_filename, prop_level)
        return
    if dependabot:
        _github_dependabot(gh, prop_org, prop_repo_id, prop_filename, prop_level)
        return

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

    if sync:
        # One org-sync implementation: -sync is `kgit sync --org` / `--user`, so it
        # gets the manifest, resume, lock and per-repo failures that command has.
        listing_only = [name for name, value in (
            ("-include-member", include_member), ("-out-repo-list", out_repo_list),
            ("-ssh-clone-url", ssh_clone_url)) if value]
        if listing_only:
            raise click.UsageError(
                f"{', '.join(listing_only)} only apply to the listing, not -sync. "
                f"For more sync options use kgit sync --org / --user {owner}.")

    account_type = gh.get_account_type(owner)

    if sync:
        kind = {OrgSync.GITHUB_ORG: "org", OrgSync.GITHUB_USER: "user"}.get(account_type)
        if not kind:
            _error(f"Couldn't find a GitHub organisation or user called {owner!r}.")
        console.print(f"Running: kgit sync --{kind} {owner}{' -no-auth' if no_auth else ''}")
        _sync_owner(owner_kind=kind, both=False, target=owner, inventory=False,
                    next_=None, status=False, retry_failed=False,
                    include_forks=False, skip_forks=False, skip_archived=False,
                    ssh=False, no_prompt=False, no_auth=no_auth)
        return

    # A user's listing also returns repos they don't own. Only their own unless
    # -include-member, the same set `kgit sync --user` keeps.
    owned_only = account_type == OrgSync.GITHUB_USER and not include_member
    print(f"\nFinding repos for: {owner} ({account_type})\n")
    try:
        repos = gh.get_repos(owner, no_auth=no_auth, owned_only=owned_only)
    except GitHubListingIncomplete as e:
        # Nothing is printed or written from a partial list: a short
        # -out-repo-list file would silently drop repos from `kgit clone -filename`.
        console.print(Text.assemble(("Error:", "bold red"), f" {e}"))
        console.print("No repo list was written. Re-run to retry.")
        exit(1)

    table = kgit.get_repos_pretty_table(repos=repos)
    print(table)
    if owned_only:
        print(f"Repos {owner} owns. -include-member also lists ones they collaborate "
              "on or reach through an organisation.")
    elif include_member and account_type != OrgSync.GITHUB_USER:
        print("-include-member applies to a user; an organisation lists its own repos.")

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


def _gitlab_table(columns, rows):
    table = Table()
    for column in columns:
        table.add_column(column, overflow="fold")
    for row in rows:
        table.add_row(*[GitLabQueries.printable(value) for value in row])
    KospexUtils.print_table(table, console)


def _gitlab_write(path, text, what):
    with open(path, "w", encoding="utf-8") as out:
        out.write(text)
    console.print(f"{what} written to {path}")


def _gitlab_auth(client):
    who = client.identity()
    console.print("Authentication successful.", style="green")
    scopes = ", ".join(who["scopes"]) if who["scopes"] else None
    for label, value in (("User", who["username"]), ("GitLab version", who["version"]),
                         ("Token scopes", scopes), ("Token expires", who["expires_at"])):
        if value:
            console.print(f"  {label}: {GitLabQueries.printable(value)}")


def _gitlab_write_raw(path, items):
    if path:
        data = GitLabQueries.without_secrets(items)
        _gitlab_write(path, json.dumps(data, indent=2) + "\n", "Raw JSON")


def _gitlab_groups(client, group, out_raw):
    groups = GitLabQueries.list_groups(client, group)
    _gitlab_table(["full_path", "id", "visibility"],
                  [(g.get("full_path"), g.get("id"), g.get("visibility")) for g in groups])
    console.print(f"{len(groups)} groups")
    _gitlab_write_raw(out_raw, groups)


def _gitlab_projects(client, group, active, include_forks, skip_archived,
                     out_repo_list, ssh, out_raw):
    # The whole listing first: nothing is written unless all of it was read.
    projects = GitLabQueries.list_projects(client, group)
    shown = projects if active is None else GitLabQueries.active_projects(projects, active)
    reasons = [GitLabQueries.skip_reason(p, include_forks, skip_archived, ssh) for p in shown]

    _gitlab_table(
        ["project", "last activity", "visibility", "fork", "archived", "left out"],
        [(p.get("path_with_namespace"), (p.get("last_activity_at") or "")[:10],
          p.get("visibility"), "yes" if p.get("forked_from_project") else "",
          "yes" if p.get("archived") else "", reason)
         for p, reason in zip(shown, reasons)])

    console.print(f"{len(projects)} projects")
    if active is not None:
        console.print(f"{len(shown)} active in the last {active} days "
                      "(GitLab's last activity, not last push)")
    urls = GitLabQueries.clone_urls(shown, ssh=ssh, include_forks=include_forks,
                                    skip_archived=skip_archived)
    left_out = ", ".join(f"{reasons.count(r)} {r}" for r in sorted(set(reasons) - {None}))
    console.print(f"{len(urls)} in the clone list"
                  + (f"; left out: {left_out}" if left_out else ""))

    if out_repo_list:
        _gitlab_write(out_repo_list, "".join(f"{url}\n" for url in urls), "Clone URLs")
    _gitlab_write_raw(out_raw, projects)


@cli.command("gitlab")
@click.option("-test-auth", "test_auth", is_flag=True, default=False,
              help="Check the token works, and show its user, scopes and expiry.")
@click.option("--groups", "groups", is_flag=True, default=False,
              help="List the groups you're a member of, or with --group, every level under it.")
@click.option("--group", "--org", "-group", "group", default=None,
              help="A group's full path (acme/platform), id or URL. Lists its projects, "
                   "including all subgroups.")
@click.option("--server", "-server", "server", default=None,
              help="Host, host:port or https URL. Default: GITLAB_URL, else gitlab.com.")
@click.option("-active", "active", type=click.INT, is_flag=False, flag_value=90, default=None,
              metavar="[DAYS]", help="Only projects with activity in the last 90 (or DAYS) days.")
@click.option("-include-forks", "include_forks", is_flag=True, default=False,
              help="Put forks in the clone list (left out by default).")
@click.option("-skip-archived", "skip_archived", is_flag=True, default=False,
              help="Leave archived projects out of the clone list.")
@click.option("-out-repo-list", "out_repo_list", type=click.Path(dir_okay=False),
              help="Write clone URLs to this file, for `kgit clone -filename`.")
@click.option("-ssh-clone-url", "ssh_clone_url", is_flag=True, default=False,
              help="Write SSH clone URLs instead of HTTPS.")
@click.option("-out-raw", "out_raw", type=click.Path(dir_okay=False),
              help="Write everything listed as JSON, without secret fields.")
@click.pass_context
def gitlab(ctx, test_auth, groups, group, server, active, include_forks, skip_archived,
           out_repo_list, ssh_clone_url, out_raw):
    """
    List GitLab groups and projects, on gitlab.com or your own server.

    \b
    Settings (environment or ~/kospex/kospex.env):
      GITLAB_TOKEN        personal access token with read_api
      GITLAB_URL          default server, e.g. https://gitlab.example.com:8929
      REQUESTS_CA_BUNDLE  CA bundle for an internal certificate

    \b
    Cloning over HTTPS uses git's own credentials, not GITLAB_TOKEN, and needs a
    token with read_repository. -ssh-clone-url uses your SSH key.

    \b
    Examples:
      kgit gitlab -test-auth --server gitlab.example.com
      kgit gitlab --groups
      kgit gitlab --groups --group acme
      kgit gitlab --group acme/platform -active -out-repo-list repos.txt
      kgit clone -filename repos.txt
    """
    project_only = [name for name, value in (
        ("-active", active is not None), ("-include-forks", include_forks),
        ("-skip-archived", skip_archived), ("-out-repo-list", out_repo_list),
        ("-ssh-clone-url", ssh_clone_url)) if value]
    if test_auth and (groups or group or out_raw or project_only):
        raise click.UsageError("Use -test-auth on its own (with --server if needed).")
    if groups and project_only:
        raise click.UsageError(f"{', '.join(project_only)} only apply to projects "
                               "(--group without --groups).")
    if ssh_clone_url and not out_repo_list:
        raise click.UsageError("-ssh-clone-url needs -out-repo-list.")
    if active is not None and active < 1:
        raise click.UsageError("-active needs a positive number of days.")
    if not (test_auth or groups or group):
        click.echo(ctx.get_help())
        return

    try:
        url = server_url(server)
        # Said before any request: the token is about to go there.
        console.print(f"GitLab server: {url}")
        if has_path_prefix(url):
            console.print("This GitLab is under a path: clone URLs will put that prefix "
                          "into the group part of each repo ID.", style="yellow")
        client = KospexGitlabClient(server=url)
        if test_auth:
            _gitlab_auth(client)
        elif groups:
            _gitlab_groups(client, group, out_raw)
        else:
            _gitlab_projects(client, group, active, include_forks, skip_archived,
                             out_repo_list, ssh_clone_url, out_raw)
    except (GitLabError, OSError) as e:
        message = "\n".join(GitLabQueries.printable(part) for part in str(e).split("\n"))
        log.error(f"kgit gitlab: {message}")
        console.print(Text.assemble(("Error:", "bold red"), f" {message}"))
        ctx.exit(1)


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
