#!/usr/bin/env python3
"""This is the kospex git CLI helper tool."""
import time
import os
import json
import click
from prettytable import PrettyTable
from kospex_core import GitRepo, Kospex
import kospex_utils as KospexUtils
from kospex_git import KospexGit
from kospex_github import KospexGithub
from kospex_bitbucket import KospexBitbucket

KospexUtils.init()
kgit = KospexGit()

@click.group()
def cli():
    """kgit (Kospex Git) is a utility for doing git things with kospex use cases.

    For documentation on how commands run `kgit COMMAND --help`.
    
    """

@cli.command("status")
@click.argument('repo',  type=GitRepo())
# pylint: disable=unused-argument
def status(repo):
    """Date and commit metadata for the given repo."""
    print(f"\nRepo status for path: {repo}")
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

@cli.command("clone")
@click.option('-sync', is_flag=True, default=True, help="Sync the repo to the database (Default)")
@click.option('-repo',  type=click.STRING, help="HTTPS Git clone URL")
@click.option('-filename',  type=click.STRING, help="File with HTTP git clone URLs")
def clone(repo, sync, filename):
    """Clone the given repo into our KOSPEX_CODE directory."""
    # We're going to shell out to git to do the clone
    kospex = Kospex()

    if repo and filename:
        exit("You can't specify both a repo and a filename. Please choose one.")

    if repo:
        repo_path = kgit.clone_repo(repo)
        if sync and repo_path:
            print("Syncing repo: " + repo_path)
            kospex.sync_repo(repo_path)

    elif filename:
        with open(filename, "r", encoding='utf-8') as file:
            for line in file:
                repo = line.strip()
                if repo.startswith("#"):
                    print(f"\n\nSkipping commented line: {repo}\n\n")
                else:
                    repo_path = kgit.clone_repo(repo)
                    if not repo_path:
                        print(f"\n\nERROR with {repo}\n\n")

                    if sync and repo_path:
                        print("Syncing: " + repo)
                        kospex = Kospex()
                        kospex.sync_repo(repo_path)


@cli.command("github")
@click.option('-org', type=click.STRING)
@click.option('-user',  type=click.STRING)
@click.option('-no-auth', is_flag=True, help="Access the Github API unauthenticated.")
@click.option('-list-repos', is_flag=True, type=click.STRING)
@click.option('-sync', is_flag=True)
@click.option('-test-auth', is_flag=True, default=False, help="Test GITHUB_AUTH_TOKEN can authenticate.")
@click.option('-out-repo-list', type=click.Path(), help="File to write clone URLs to.")
def github(org, user, no_auth, list_repos, sync, test_auth, out_repo_list):
    """
    Interact with the GitHub API.

    For authenticated access, you must set the GITHUB_AUTH_TOKEN environment variable.
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
        exit(0)

    if not org and not user:
        print("You must specify either an organization or a user.")
        exit(1)

    gh.get_env_credentials()

    auth = False
    if not no_auth:
        gh.get_env_credentials()
        #gh.set_access_token(os.environ.get("GITHUB_PAT"))
        auth = True

    owner = org or user
    account_type = gh.get_account_type(owner)
    kospex = Kospex()

    if not account_type:
        print(f"Could not find account type for {owner}.")
        print(f"Most likely {owner} does not exist or has a typo.")
        exit(1)

    if org:
        repos = gh.get_org_repos(org)
    elif user:
        repos = gh.get_user_repos(user)
    else:
        print("You must specify either an organization or a user.")

    details = []

    print(f"\nFinding repos for: {owner} ({account_type})\n")

    if repos:
        for repo in repos:
            record = {}
            record['name'] = repo.get('name')
            record['fork'] = repo.get('fork')
            record['updated_at'] = repo.get('updated_at')
            record['pushed_at'] = repo.get('pushed_at')
            #if record.get("fork"):
            #    full_repo = gh.get_repo(owner=")
            #    record['parent'] = repo.get('parent').get('full_name')
            record['private'] = repo.get('private')
            record['clone_url'] = repo.get('clone_url')
            print(f"Found repo: {repo['name']}")
            details.append(record)

            if sync:
                clone_url = repo.get('clone_url')
                repo_path = kgit.clone_repo(clone_url)
                print(f"Syncing repo: {clone_url} in directorty {repo_path}")
                kospex.sync_repo(repo_path)

    table = PrettyTable()
    table.field_names = ["Name", "fork", "private", "owner", "clone_url", "pushed_at", "status"]
    table.align["Name"] = "l"
    table.align["clone_url"] = "l"
    table.align["status"] = "l"

    for detail in details:
        #print(detail)
        days_ago = KospexUtils.days_ago(detail.get("pushed_at"))
        status = "Unknown"
        if days_ago:
            status = KospexUtils.development_status(days_ago)
        table.add_row([detail.get("name"), detail.get("fork"),
                       detail.get("private"), owner, detail.get("clone_url"), 
                       detail.get("pushed_at"), status])

    print(table)

    # Write out the repo list to a file
    if out_repo_list:
        with open(out_repo_list, "w", encoding='utf-8') as file:
            for detail in details:
                file.write(detail['clone_url'] + "\n")

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
