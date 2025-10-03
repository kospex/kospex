#!/usr/bin/env python3
"""This is the kospex runner tool - run the same command on all repos."""
from itertools import count
import os
import os.path
import subprocess
import sys
import shlex
import glob
import click
from pydantic.fields import Deprecated
from kospex_core import Kospex
from kospex_observation import Observation
import kospex_utils as KospexUtils
from kospex_utils import KospexTimer
import kospex_schema as KospexSchema
from kospex_git import KospexGit
import krunner_utils as KrunnerUtils
import kospex_web as KospexWeb
from rich.console import Console
from rich.table import Table

# Initialize Kospex environment with logging
KospexUtils.init(create_directories=True, setup_logging=True, verbose=False)
kospex = Kospex()
console = Console()

# Get logger using the new centralized logging system
log = KospexUtils.get_kospex_logger('krunner')

@click.group()
def cli():
    """krunner (Kospex Runner) is a utility for running shell commands on multiple git repos.

    For documentation on how commands run `krunner COMMAND --help`.

    See also https://kospex.io/krunner

    """

def get_repos(request_id):
    """
    Help function get repos filtered by request_id (server, org_key or repo_id)
    """
    params = {}
    if request_id:
        params = KospexWeb.get_id_params(request_id)

    return kospex.kospex_query.get_repos(**params)

@cli.command("repos")
@click.option('-file', required=False, type=click.Path(), help="filename of clone urls to check.")
@click.argument('request_id', required=False, type=click.STRING)
def repos(file,request_id):
    """
    List repos in the kospex db.
    Optionally, provide a filter of
    server (e.g. github.com)
    org_key github.com~kospex (SERVER~OWNER)
    or a repo_id
    github.com~kospex~panopticas (SERVER~OWNER~REPO)
    """
    #repos = get_repos(request_id)
    params = KospexWeb.get_id_params(request_id)
    repos = kospex.kospex_query.get_repos(**params)

    table = Table(title="Repositories")
    table.add_column("repo_id", justify="left", style="cyan", no_wrap=True)
    table.add_column("file_path", style="magenta")
    count = 0

    for r in repos:
        table.add_row(r['_repo_id'], r['file_path'])
        count += 1

    console.print(table)
    console.print(f"Total repos: {count}")


@cli.command("file-metadata")
@click.option('-force', is_flag=True, default=False, help="Force a refresh of metadata for repo. (Default: False)")
@click.argument('request_id', required=False, type=click.STRING)
def file_metadata(force, request_id):
    """
    Update the file metadata for the in-scope repos.
    """
    repos = get_repos(request_id)
    # params = KospexWeb.get_id_params(id)
    # repos = kospex.kospex_query.get_repos(**params)
    for r in repos:
        console.log(f"{r['_repo_id']}\t{r['file_path']}")
        repo_id = r['_repo_id']
        current_hash = KospexUtils.get_git_hash(r['file_path'])
        console.log(f"Current hash: {current_hash}")

        # Check to see if we have already got metadata for this repo and hash
        sql = f"""SELECT hash, _repo_id FROM {KospexSchema.TBL_FILE_METADATA}
        WHERE _repo_id = ? AND hash = ?"""
        row = kospex.kospex_db.execute(sql,[repo_id,current_hash]).fetchone()
        print(row)

        if force and row:
            console.log(f"Force update metadata for repo {repo_id} and hash {current_hash}",
                style="dark_orange")
            sql = f"""DELETE FROM {KospexSchema.TBL_FILE_METADATA}
            WHERE _repo_id = ? AND hash = ?"""
            row = kospex.kospex_db.execute(sql,[repo_id,current_hash]).fetchone()

        files = kospex.file_metadata(r['file_path'])
        console.log(f"Metadata collected for # of Files: {len(files)}")

@cli.command("branches")
@click.option('-save', is_flag=True, default=False, help="Save to kospex DB. (Default: False)")
@click.option('-csv', is_flag=True, default=False, help="Save to CSV file. (Default: False)")
@click.option('-verbose', is_flag=True, default=False, help="Verbose output. (Default: False)")
@click.argument('request_id', required=False, type=click.STRING)
def branches(save,csv,verbose,request_id):
    """
    Update the current branches for the in-scope repos.
    """
    repos = get_repos(request_id)
    results = []

    for r in repos:
        kgit = KospexGit()
        kgit.set_repo(r['file_path'])
        obs = kgit.new_observation("BRANCHES","REPO")

        branches = KospexGit.get_branches(r['file_path'])
        obs.raw = branches
        obs.format = "INTEGER"
        obs.data = len(branches)

        entry = {
            'repo_id': r['_repo_id'],
            'branches': obs.data,
            'file_path': r['file_path'],
        }
        results.append(entry)

        console.log(f"{r['_repo_id']}\t{r['file_path']}")
        if verbose:
            console.log(f"# Branches: {len(branches)}")
            console.log(branches)
            console.log("Observation:")
            console.log(obs)

        if save:
            existing_obs = kospex.kospex_query.get_single_observation(r['_repo_id'], obs.observation_key,
                obs.hash, obs.file_path)
            if existing_obs:
                console.log(f"Existing repo size exists for this hash and repo_id {r['_repo_id']}",
                    style="dark_orange")
                console.log(f"Existing observation UUID: {existing_obs}")
            else:
                kospex.kospex_query.add_observation(obs.to_dict())

    if csv:
        filename = 'branches.csv'
        console.log(f"Writing {len(results)} repo sizes to {filename}")
        KrunnerUtils.write_dict_to_csv(filename, results)


@cli.command("repo-size")
@click.option('-save', is_flag=True, default=False, help="Save to kospex DB. (Default: False)")
@click.option('-csv', is_flag=True, default=False, help="Save to CSV file. (Default: False)")
@click.option('-verbose', is_flag=True, default=False, help="Verbose output. (Default: False)")
@click.argument('request_id', required=False, type=click.STRING)
def repo_size(save,csv,request_id,verbose):
    """
    View the current repo size for the in-scope repos.
    Calculate total, .git directory and working directory sizes.
    Write the results to the observations table with -save
    """
    repos = get_repos(request_id)
    results = []

    for r in repos:
        console.log(f"\nCalculating {r['_repo_id']} ...\n{r['file_path']}")

        kgit = KospexGit()
        kgit.set_repo(r['file_path'])
        obs = kgit.new_observation("REPO_SIZE","REPO")
        sizes = KospexGit.get_repo_size(r['file_path'])

        obs.raw = sizes
        obs.format = "INTEGER"
        obs.data = sizes['total']

        entry = {
            'repo_id': r['_repo_id'],
            'file_path': r['file_path'],
            'total_size': sizes['total'],
            'git_size': sizes['git'],
            'workspace_size': sizes['workspace']
        }
        results.append(entry)

        if verbose:
            console.log(sizes)
            console.log(obs.to_json())

        if save:
            existing_obs = kospex.kospex_query.get_single_observation(r['_repo_id'], obs.observation_key,
                obs.hash, obs.file_path)
            if existing_obs:
                console.log(f"Existing repo size exists for this hash and repo_id {r['_repo_id']}",
                    style="dark_orange")
            else:
                kospex.kospex_query.add_observation(obs.to_dict())

    table = Table(title="Repo Size")
    table.add_column("Repo ID", justify="left", style="cyan", no_wrap=True)
    table.add_column("total (Mb)", style="magenta",justify="right")
    table.add_column("total (kb)", style="magenta",justify="right")
    table.add_column(".git (kb)", style="magenta",justify="right")
    table.add_column("workspace (kb)", style="magenta",justify="right")

    for r in results:
        table.add_row( r['repo_id'], str( round( r['total_size'] / 1024, 3)),
            str(r['total_size']), str(r['git_size']), str(r['workspace_size']) )

    console.print(table)

    if csv:
        filename = 'repo-sizes.csv'
        console.log(f"Writing {len(results)} repo sizes to {filename}")
        KrunnerUtils.write_dict_to_csv(filename, results)

@cli.command("developer-tech")
@click.option('-csv', is_flag=True, default=False, help="Save to CSV file. (Default: False)")
@click.option('-verbose', is_flag=True, default=False, help="Verbose output. (Default: False)")
@click.option('-developers', is_flag=True, default=False, help="Show all developer technologies")
@click.argument('dev', required=False, type=click.STRING)
def developer_tech(csv,verbose,developers,dev):
    """
    Show technology for a given developer
    """
    results = []

    if verbose:
        console.log(f"Developer: {dev}")

    if dev:
        console.log(f"Showing technology for developer {dev}")
    else:
        console.log("Showing technology for all developers")

    results = []

    # This will be the memory kospex query object
    memory_kq = None

    with KospexTimer("Loading data to in memory database") as load_memory:
        memory_kq = kospex.kospex_query.create_memory_kospex_query(["commits", "commit_files"])
    console.log(f"Loaded tables to memory db {load_memory}")

    with KospexTimer("creating indexes") as index_timer:
        memory_kq.kospex_db["commits"].create_index(['hash'])
        memory_kq.kospex_db["commit_files"].create_index(['hash'])
    console.log(f"{index_timer}")

    with KospexTimer("Assessing developer tech in memory") as memory_timer:
        results = memory_kq.developer_tech(author_email=dev,developers=developers)
    console.log(f"{memory_timer}")

    table = Table(title="Technology")
    table.add_column("Tech", justify="left", style="cyan", no_wrap=True)
    table.add_column("commits", style="magenta",justify="right")
    table.add_column("repos", style="magenta",justify="right")
    table.add_column("Unique files", style="magenta",justify="right")
    table.add_column("Years active", style="magenta",justify="right")
    table.add_column("Last commit", style="bright_black",justify="right")


    display_results = results

    if developers:
        # Run the whole tech stack query again to get the display of tech for the organisation
        all_results = memory_kq.developer_tech()
        display_results = all_results

    for r in display_results:
        table.add_row(r['language'], str(r['commits']),
            str(r['repos']),str(r['files']),str(r['years_active']),str(r['last_commit']))

    console.print(table)

    tech_number = len(results)
    if developers:
        tech_number = len(display_results)

    console.log(f"Technologies found: {tech_number}")

    if developers and not csv:
        console.log("\nWarning: No results for invididual developers shown", style="red")
        console.log("use -csv to export\n", style="red")

    if csv:
         filename = 'dev-tech.csv'
         console.log(f"Writing {dev} technology to {filename}")
         KrunnerUtils.write_dict_to_csv(filename, results)

@cli.command("dependencies")
@click.option('-csv', is_flag=True, default=False, help="Save to CSV file. (Default: False)")
def dependencies(csv):
    """
    Show a list of all dependency files
    use -csv to write the results to the csv file dependencies-list.csv
    """
    dependencies = []
    results = []
    # This will be the memory kospex query object
    memory_kq = None

    console.log("Loading data to in memory database ...")
    with KospexTimer("Loading data to in memory database") as load_memory:
        memory_kq = kospex.kospex_query.create_memory_kospex_query(
            ["commit_files", "file_metadata", "repos"])
    console.log(f"Loaded tables to memory db {load_memory}")

    console.log("Creating indexes ...")
    with KospexTimer("creating indexes") as index_timer:
        #memory_kq.kospex_db["commits"].create_index(['hash'])
        #memory_kq.kospex_db["commit_files"].create_index(['hash'])
        memory_kq.kospex_db["commit_files"].create_index(['committer_when'])
    console.log(f"{index_timer}")

    with KospexTimer("Grabbing all dependencies from memory DB") as memory_timer:
        dependencies = memory_kq.get_dependency_files()
        #results = memory_kq.developer_tech(author_email=dev,developers=developers)
    console.log(f"{memory_timer}")

    console.log(f"Finding dependencies ... {len(dependencies)}")
    counter = 0
    for d in dependencies:
        counter += 1
        console.log(f"Processing dependency {counter}/{len(dependencies)}")
        console.log(f"repo_id: {d['_repo_id']}")
        console.log(f"{d['Provider']}\n")

        details = memory_kq.get_last_commit_file(d['_repo_id'], d['Provider'])
        repo = memory_kq.get_repo_by_id(d['_repo_id'])
        if repo:
            d['repo_status'] = KospexUtils.development_status(repo['last_seen'])
            d['last_repo_commit'] = repo.get('last_seen',"Unknown")
        else:
            d['repo_status'] = "Unknown"
            d['last_repo_commit'] = "Unknown"
        if details:
            if not d['committer_when']:
                d['committer_when'] = details['committer_when']
        if d['committer_when']:
            d['status'] = KospexUtils.development_status(d['committer_when'])
        else:
            d['status'] = "Unknown"

        dep = {
            "status": d['status'],
            "file_path": d['Provider'],
            "committer_when": d['committer_when'],
            "repos_status": d['repo_status'],
            "last_repo_commit": d['last_repo_commit'],
            "repo_id": d['_repo_id'],
            "tags": d['tech_type']
        }

        results.append(dep)

    table = Table(title="Dependencies")
    table.add_column("Repo ID", justify="left", style="bright_black", no_wrap=True)
    table.add_column("File Path", style="magenta",justify="left")
    table.add_column("Last Commit", style="bright_black",justify="right")
    table.add_column("Status", style="magenta", justify="right")

    for d in results:
        table.add_row(d['repo_id'], str(d['file_path']),
            d.get('committer_when',"Unknown"), d['status'])

    console.print(table)

    tech_number = len(dependencies)

    console.log(f"# Dependencies found: {tech_number}")

    if not csv:
        console.log("\nWarning: No results written to CSV", style="red")
        console.log("use -csv to export\n", style="red")

    if csv:
         filename = 'dependencies-list.csv'
         console.log(f"Writing dependencies to {filename}")
         KrunnerUtils.write_dict_to_csv(filename, results)

@cli.command("devs-by-tag")
@click.option('-csv', is_flag=True, default=False, help="Save to CSV file. (Default: False)")
@click.option('-tag', required=False, type=click.STRING)
@click.option('-filename', required=False, type=click.STRING)
def devs_by_tag(csv, tag, filename):
    """
    Find all files (last modified only) which have the tag,
    and the developers that have modified them.
    use -csv to write the results to the csv file dependencies-list.csv
    """
    files = []
    results = []
    # This will be the memory kospex query object
    memory_kq = None

    if not tag and not filename:
        console.log("\nWarning: One of tag or filename are required", style="red")
        exit(1)

    console.log("Loading data to in memory database ...")
    with KospexTimer("Loading data to in memory database") as load_memory:
        memory_kq = kospex.kospex_query.create_memory_kospex_query(
            ["commits", "commit_files", "file_metadata"])
    console.log(f"Loaded tables to memory db {load_memory}")

    console.log("Creating indexes ...")
    with KospexTimer("creating indexes") as index_timer:
        memory_kq.kospex_db["commits"].create_index(['hash'])
        memory_kq.kospex_db["commit_files"].create_index(['hash', 'committer_when'])
        #memory_kq.kospex_db["commit_files"].create_index(['committer_when'])
    console.log(f"{index_timer}")

    with KospexTimer("Grabbing all files with the tag {tag} from memory DB") as memory_timer:
        print(f"tag: {tag}, filename: {filename}")
        #results = memory_kq.get_developers_by_tag(tag=tag,filename=filename)
        files = memory_kq.get_metadata_files(tag=tag, filename=filename)
    console.log(f"{memory_timer}")

    console.log(f"Processing files ... {len(files)}")
    counter = 0
    for item in files:
        counter += 1
        console.log(f"Processing file {counter}/{len(files)}")
        console.log(f"repo_id: {item['_repo_id']}")
        console.log(f"{item['Provider']}\n")
        authors = memory_kq.get_file_authors(file_name=item['Provider'], repo_id=item['_repo_id'])
        for author in authors:
            results.append(author)
        #console.log(item)
        #console.log(authors)

    table = Table(title="Authors")
    table.add_column("Author", style="bright_black",justify="left")
    table.add_column("File Path", style="magenta",justify="left")
    table.add_column("Last Commit", style="bright_black",justify="right")
    table.add_column("Commits", style="blue", justify="right")
    table.add_column("Repo ID", justify="left", style="bright_black", no_wrap=True)

    for d in results:
         table.add_row(d['author_email'], str(d['file_path']),
             d.get('committer_when',"Unknown"), str(d['commits']), d['_repo_id'])

    console.print(table)

    tech_number = len(results)

    console.log(f"# authors and files found: {tech_number}")

    if not csv:
        console.log("\nWarning: No results written to CSV", style="red")
        console.log("use -csv to export\n", style="red")

    if csv:
         filename = 'devs-by-tag.csv'
         console.log(f"Writing dependencies to {filename}")
         KrunnerUtils.write_dict_to_csv(filename, results)

@cli.command("tenure")
@click.option('-days', is_flag=False, default=90, help="Days since last commit = Left (Default: 90)")
@click.option('-seen', is_flag=False, default=365, help="Seen in the last X days (Default: 365)")
@click.option('-csv', is_flag=True, default=False, help="Save to CSV file. (Default: False)")
def tenure(days, seen, csv):
    """
    Show the tenure of developers
    """

    results = []

    # This will be the memory kospex query object
    memory_kq = None

    console.log("Loading data to in memory database ...")
    with KospexTimer("Loading data to in memory database") as load_memory:
        memory_kq = kospex.kospex_query.create_memory_kospex_query(["commits"])
    console.log(f"Loaded tables to memory db {load_memory}")

    console.log("Creating indexes ...")
    with KospexTimer("creating indexes") as index_timer:
        memory_kq.kospex_db["commits"].create_index(['committer_when'])
    console.log(f"{index_timer}")

    console.log("Creating indexes ...")
    with KospexTimer("Calculating tenure") as memory_timer:
        results = memory_kq.authors()
    console.log(f"{memory_timer}")

    # email, first_commit, last_commit, years_active, repos

    table = Table(title="Authors")
    table.add_column("Developer", style="bright_black",justify="left")
    table.add_column("First Commit", style="magenta",justify="left")
    table.add_column("Last Commit", style="bright_black",justify="right")
    table.add_column("Years Active", style="blue", justify="right")
    table.add_column("# Repos", justify="right", style="bright_black")
    table.add_column("# Commits", justify="right", style="bright_black")

    tenure_groups = {
        "< 1 year": 0,
        "1-2 years": 0,
        "2-3 years": 0,
        "3-4 years": 0,
        "4-5 years": 0,
        "5+ years": 0
    }

    leavers = 0

    developer_leavers = []

    for dev in results:

        # If last_seen > days they've left
        # and we've seen them in the seen timeframe (default we've seen them in the last year)
        #
        if dev['last_seen'] > days and seen > dev['last_seen']:
            leavers += 1
            days_active = KospexUtils.days_between_datetimes(dev['first_commit'], dev['last_commit'])
            years_active = round(days_active / 365,3)
            dev['years_active'] = years_active

            if years_active < 1:
                tenure_groups["< 1 year"] += 1
            elif years_active < 2:
                tenure_groups["1-2 years"] += 1
            elif years_active < 3:
                tenure_groups["2-3 years"] += 1
            elif years_active < 4:
                tenure_groups["3-4 years"] += 1
            elif years_active < 5:
                tenure_groups["4-5 years"] += 1
            else:
                tenure_groups["5+ years"] += 1

            table.add_row(dev['author_email'], dev['first_commit'], dev['last_commit'],
                str(years_active), str(dev['repos']), str(dev['commits']))

            developer_leavers.append(dev)

    console.print(table)

    tenure_percentages = KospexUtils.convert_to_percentage(tenure_groups)

    stat_table = Table(title="Tenure of Leavers Summary")
    stat_table.add_column("Tenure", style="bright_black",justify="left")
    stat_table.add_column("Number", style="bright_black",justify="right")
    stat_table.add_column("Percentage", style="bright_black",justify="right")
    for k in tenure_percentages.keys():
        stat_table.add_row(k, str(tenure_groups[k]), str(tenure_percentages[k])+"%")

    console.print()
    console.print(stat_table)
    console.print(f"\nTotal number of developers seen: {len(results)}")
    console.print(f"Number of Leavers: {leavers}\n")

    if not csv:
        console.print("\nWarning: No results written to CSV", style="red")
        console.print("use -csv to export\n", style="red")

    if csv:
          filename = 'developer-tenure-leavers.csv'
          console.log(f"Writing dependencies to {filename}")
          KrunnerUtils.write_dict_to_csv(filename, developer_leavers)


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
@click.option('--only-verified', is_flag=True, default=False, help="use --only-verified. (Default: False)")
@click.argument('directory', type=click.Path(exists=True))
def trufflehog_scan(only_verified,directory):
    """Run trufflehog on all git repositories found in the given directory."""
    print("\nDirectory: " + os.path.abspath(directory))
    dirs = KospexUtils.find_repos(directory)
    cwd = os.getcwd()
    for d in dirs:
        print("\nRepo: " + d)
        kospex.set_repo_dir(d)
        fname = kospex.generate_krunner_filename(function="TRUFFLEHOG",ext="json")
        verif = ""
        if only_verified:
            verif = "--only-verified"
        command = f"trufflehog filesystem -j {verif} . 2&> {fname}"
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
