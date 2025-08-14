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
from kospex_bitbucket import KospexBitbucket

# Initialize Kospex environment with logging
KospexUtils.init(create_directories=True, setup_logging=True, verbose=False)
kgit = KospexGit()
kospex = Kospex()
console = Console()

# Get logger using the new centralized logging system
log = KospexUtils.get_kospex_logger('kgit')

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


# @cli.command("pull")
# @click.option('-sync', is_flag=True, default=True, help="Sync the repo to the database (Default)")
# def pull(sync):
#     """
#     Check if we're in a git repo, do a git pull,
#     and sync to the kospex DB.
#     """
#     current = os.getcwd()
#     git_base = KospexUtils.find_git_base(current)
#     if git_base:
#         os.chdir(git_base)
#         os.system("git pull")
#         if sync:
#             print("Syncing to kospex DB ...")
#             kospex.sync_repo(git_base)
#         os.chdir(current)
#     else:
#         print(f"{current} does not appear to be in a git repo")

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
    Interact with the BitBucket API to query repos in a workspace.

    This command requires the following environment variables to be set:
    BITBUCKET_USERNAME and
    BITBUCKET_APP_PASSWORD

    The bitbucket username is in the "account settings" section of your bitbucket account.
    This is NOT your email address.

    """

    bb = KospexBitbucket()
    click.echo()
    if bb.get_env_credentials():
        print("Found bitbucket credentials in the environment.")
    else:
        print("Could not find bitbucket credentials in the environment.")
        print("Please set BITBUCKET_USERNAME and BITBUCKET_APP_PASSWORD.\n")
        exit(1)

    if test_auth:
        if bb.test_auth():
            print("Authentication successful.\n")
        else:
            print("\nAuthentication FAILED!.",
                  "\nCheck your BITBUCKET_USERNAME and BITBUCKET_APP_PASSWORD\n")
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

    if out_repo_list:
        with open(out_repo_list, "w", encoding='utf-8') as file:
            for r in repos:
                file.write(bb.get_https_clone_url(r) + "\n")

    if out_raw:
        with open(out_raw, "w", encoding='utf-8') as raw_file:
            raw_file.write(json.dumps(repos))


if __name__ == '__main__':
    cli()
