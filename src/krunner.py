#!/usr/bin/env python3
"""This is the kospex runner tool - run the same command on all repos."""
import os
import os.path
import subprocess
import sys
import shlex
import glob
import click
from kospex_core import Kospex
import kospex_utils as KospexUtils
import krunner_utils as KrunnerUtils

KospexUtils.init()
kospex = Kospex()

@click.group()
def cli():
    """krunner (Kospex Runner) is a utility for running shell commands on multiple git repos.

    For documentation on how commands run `krunner COMMAND --help`.

    See also https://kospex.io/krunner
    
    """

@cli.command("find-docker")
@click.option('-out', type=click.STRING, help="filename to write CSV results to.")
@click.option('-images', is_flag=True, default=False, help="Extract image names")
@click.argument('directory', required=False, type=click.Path(exists=True))
def find_docker(directory,out,images):
    """
    Find docker related files in the given directory.
    """
    if not directory:
        directory = "." # default to current directory

    print("\nSearch directory: " + os.path.abspath(directory))

    print("\nDockerfiles and docker-compose* files found ...\n")

    dirs = KospexUtils.find_repos(directory)
    repo_data = KospexUtils.get_all_last_commit_info(dirs)
    # only access repo key if repo key is present
    repos = [data.get('repo') for data in repo_data if 'repo' in data]
    unique_repos = list(set(repos))
    stats_dict = {key: KospexUtils.init_repo_stats() for key in unique_repos}

    repo_status= {data.get('repo'): data.get("status") for data in repo_data if 'status' in data }

    files = KrunnerUtils.find_dockerfiles_in_repos(dirs)
    records = KospexUtils.get_git_metadata(files)

    for r in records:
        repo = r.get('repo')
        if repo:
            stats_dict[repo] = KospexUtils.add_status(stats_dict[repo], r.get('status'))
            r['repo_status'] = repo_status.get(repo)

    if images:
        records = KrunnerUtils.get_docker_images(records)
        images_list = []
        for r in records:
            img = r.get('base_image')
            images_list.append(KospexUtils.parse_docker_image(img))
        registries = KrunnerUtils.count_dict_keyvalues(images_list, 'registry')

    table = KospexUtils.get_dependency_files_table(records, images=images)
    print(table)

    if out:
        KospexUtils.list_dict_2_csv(records,out)

    print("\nMaintenance stats for Docker file types\n")
    status_table = KospexUtils.get_repo_stats_table(stats=stats_dict)
    print(status_table)

    if images:
        print("\n# of Docker images by registry")
        print(registries)
        print(KospexUtils.get_keyvalue_table(registries))
        print()


@cli.command("find-repos")
@click.argument('directory', required=False, type=click.Path(exists=True))
def find_repos(directory):
    """
    Find all git repositories found in the given directory.
    If no directory is passed, the current directory is used.
    """
    if not directory:
        #directory = os.getcwd()
        directory = "." # default to current directory

    print("\nDirectory: " + os.path.abspath(directory))
    kospex.list_repos(directory)

@cli.command("trufflehog")
@click.argument('directory', type=click.Path(exists=True))
def trufflehog_scan(directory):
    """Run trufflehog on all git repositories found in the given directory."""
    print("\nDirectory: " + os.path.abspath(directory))
    dirs = KospexUtils.find_repos(directory)
    cwd = os.getcwd()
    for d in dirs:
        print("\nRepo: " + d)
        kospex.set_repo_dir(d)
        fname = kospex.generate_krunner_filename(function="TRUFFLEHOG",ext="json")
        command = f"trufflehog filesystem -j . > {fname}"
        if not os.path.exists(fname):
            print(command)
            os.system(command)
        else:
            print(f"Skipping, file {fname} exists")
        os.chdir(cwd)

@cli.command("grep")
@click.option('-keyword', type=click.STRING, help="String to search for.")
@click.argument('directory', type=click.Path(exists=True))
def grep(keyword, directory):
    """Run a 'grep' on all git repositories found in the given directory."""
    print("\nDirectory: " + os.path.abspath(directory))
    dirs = KospexUtils.find_repos(directory)
    cwd = os.getcwd()
    print("# repos: " + str(len(dirs)))
    for d in dirs:
        print("\nRepo: " + d)
        os.chdir(d)
        os.system(f"grep -Rn {keyword} *")
        os.chdir(cwd)

@cli.command("todo")
@click.argument('directory', type=click.Path(exists=True))
def todo(directory):
    """Search for the keyword TODO in files in git repos."""
    print("\nDirectory: " + os.path.abspath(directory))
    dirs = KospexUtils.find_repos(directory)
    cwd = os.getcwd()
    print("# repos: " + str(len(dirs)))
    for d in dirs:
        print("\nRepo: " + d)
        #os.chdir(d)
        kospex.set_repo_dir(d)
        details = kospex.git.add_git_to_dict({})
        details['hash'] = kospex.git.current_hash
        details['observation_key'] = "GREP_TODO"
        details['observation_type'] = "FILE"
        #_repo_id,hash,file_path,observation_key
        #cmd = "grep -Rn TODO *"
        #os.system(cmd)
        #os.system("pwd")
        cmd = ['grep', '-Rn', 'TODO *']
        #result = subprocess.run(cmd, capture_output=True, text=True).stdout.split('\n')
        result  = []
        result = subprocess.run(cmd, capture_output=True, text=True).stdout.split('\n')
        for raw in result:
            obs = details.copy()
            #print(i)
            params = KrunnerUtils.extract_grep_parameters(raw)
            # We may get a None here for warnings like binary files
            if params:
                filename, line, finding = params
                #print(filename)
                obs['file_path'] = filename
                obs['line_number'] = line
                obs['data'] = finding
                obs['raw'] = raw
                print(obs)
                kospex.kospex_query.add_observation(obs)
        os.chdir(cwd)

@cli.command("git-pull")
@click.option('-sync', is_flag=True, default=True, help="Sync to kospex DB. (Default: True)")
@click.argument('directory', required=False, type=click.Path(exists=True))
def git_pull(directory,sync):
    """Run a 'git pull' on all git repositories found in the given directory."""
    if not directory:
        #directory = os.getcwd()
        directory = "." # default to current directory
    print("\nDirectory: " + os.path.abspath(directory))

    dirs = KospexUtils.find_repos(directory)
    cwd = os.getcwd()
    print("# repos: " + str(len(dirs)))
    for d in dirs:
        print("\nRepo: " + d)
        os.chdir(d)
        os.system("git pull")
        os.chdir(cwd)
        if sync:
            print("Syncing to kospex DB ...")
            kospex.sync_repo(d)

@cli.command("gitleaks")
@click.argument('directory', type=click.Path(exists=True))
def gitleaks_scan(directory):
    """Run a 'gitleaks detect' on all git repositories found in the given directory."""
    print("\nDirectory: " + os.path.abspath(directory))
    dirs = KospexUtils.find_repos(directory)
    print(f"About to run gitleaks on {len(dirs)} repos\nThis may take a while ...")
    cwd = os.getcwd()
    for d in dirs:
        print("\nRepo: " + d)
        kospex.set_repo_dir(d)
        #print(kospex.git.get_repo_id())
        fname = kospex.generate_krunner_filename(function="GITLEAKS",ext="json")
        if not os.path.exists(fname):
            os.system(f"gitleaks detect -r {fname}")
        else:
            print(f"Skipping, file {fname} exists")
        os.chdir(cwd)
        print("\n")


@cli.command("secrets-hotspots")
@click.option('-git_server', type=click.STRING, help="Git server to limit results to.")
@click.option('-org_key', type=click.STRING, help="Org key [server~org] to limit results to.")
def secrets_hotspots(git_server,org_key):
    """Identify secrets hotspots from tools that have been run."""

    user_kospex_home = os.path.expanduser("~/kospex")
    heatmap = {}
    print(user_kospex_home)
    krunner_path = f"{user_kospex_home}/krunner"

    globber = f"{user_kospex_home}/krunner/*GITLEAKS*.json"
    print(globber)
    results = glob.glob(globber)
    for r in results:

        if details := kospex.extract_krunner_file_details(r, krunner_home=krunner_path):
            if details['repo_id'] not in heatmap:
                heatmap[details['repo_id']] = {}

            gitleaks = KrunnerUtils.load_gitleaks(r)

            if gitleaks:
                heatmap[details['repo_id']]['gitleaks'] = len(gitleaks)
            else:
                heatmap[details['repo_id']]['gitleaks'] = 0

    trufflehog_globber = f"{user_kospex_home}/krunner/*TRUFFLEHOG*.json"
    results = glob.glob(trufflehog_globber)
    for r in results:

        if details := kospex.extract_krunner_file_details(r, krunner_home=krunner_path):
            if details['repo_id'] not in heatmap:
                print("New repo_id in heatmap")
                heatmap[details['repo_id']] = {}
            trufflehog = KrunnerUtils.load_trufflehog(r)
            if trufflehog:
                heatmap[details['repo_id']]['trufflehog'] = len(trufflehog)
            else:
                heatmap[details['repo_id']]['trufflehog'] = 0


    print(heatmap)
    table = KrunnerUtils.get_secrets_heatmap_table(heatmap)
    print(table)

@cli.command("semgrep")
@click.argument('directory', type=click.Path(exists=True))
def semgrep(directory):
    """
    Run a 'semgrep scan' on all git repositories found in the given directory.
    """
    print("\nDirectory: " + os.path.abspath(directory))
    dirs = KospexUtils.find_repos(directory)
    cwd = os.getcwd()
    print("# repos: " + str(len(dirs)))
    for d in dirs:
        print("\nRepo: " + d)
        #os.chdir(d)
        kospex.set_repo_dir(d)
        print(kospex.git.get_repo_id())
        #fname = kospex.generate_krunner_filename(function="SEMGREP",ext="txt")
        fname = kospex.generate_krunner_filename(function="SEMGREP",ext="json")
        if not os.path.exists(fname):
            #os.system(f"gitleaks detect -r {fname}")
            os.system(f"semgrep scan --json -o {fname}")
        else:
            print(f"Skipping, file {fname} exists")
        # we should output as json, but for inital test, we'll use txt
        #os.system(f"semgrep scan > {fname}")
        #os.system(f"semgrep scan --json -o {fname}")

        os.chdir(cwd)

@cli.command("find-urls")
def find_urls():
    """ Find all URLs in files in the current directory"""
    # Define the URL pattern for grep
    # This is a basic URL pattern to capture http, https and ftp

    url_pattern = '(ftp|https?)://[a-zA-Z0-9.-]+(:[0-9]+)?(/[a-zA-Z0-9._/?&=-]*)?'

    # Construct the grep command
    # -r for recursive, -E for extended regular expressions,
    # and -o to only output the matching part of the file
    # -n for the line number
    #grep_command = ['grep', '-rEon', url_pattern, "*"]

    grep_command = f'grep -rEon {url_pattern}'
    urls = None
    try:
        urls = subprocess.check_output(
                shlex.split(grep_command),
                encoding='utf-8'
            )
    except subprocess.CalledProcessError as e:
        print(e.stderr)
        print(f"Error executing grep: {e}", file=sys.stderr)

    lines = urls.split("\n")
    for l in lines:
        parts = KrunnerUtils.extract_grep_parameters(l)
        if parts:
            l.strip()
            filename, line_num, url = parts
            print(filename + " " + line_num + " " + url)

@cli.command("find-js-src")
def find_js_src():
    """ Find javascript refs in HTML files in the current directory"""
    # Define the URL pattern for grep
    # This is a basic URL pattern to capture http, https and ftp
    #directory = os.getcwd()
    directory = "."
    print("\nDirectory: " + os.path.abspath(directory))

    for root, _, files in os.walk(directory):

        for file in files:
            if file.endswith(".html") or file.endswith(".htm"):
                KrunnerUtils.find_js_libraries(os.path.join(root, file))


if __name__ == '__main__':
    cli()
