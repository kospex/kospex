"""Core functions for running the kospex CLI"""
import os
import os.path
import sys
import subprocess
import csv
from shutil import which
import click
from prettytable import PrettyTable, from_db_cursor
from kospex_git import KospexGit, MissingGitDirectory
import kospex_utils as KospexUtils
import kospex_schema as KospexSchema
import kospex_query as KospexQuery
#from kospex_mergestat import KospexMergeStat

class GitRepo(click.ParamType):
    """ Custom click param type for git repos """
    name = "git repo"

    def convert(self, value, param, ctx):
        """ Convert the value to a git repo """
        if not os.path.isdir(value):
            self.fail(f"{value} is not a directory or does not exist", param, ctx)

        if not KospexUtils.is_git(value):
            self.fail(f"{value} is not a git repo", param, ctx)
        return value

class Kospex:
    """kospex core functionality"""
    def __init__(self):

        self.original_cwd = None
        self.repo_directory = None
        self.git = KospexGit()
        self.kospex_db = KospexSchema.connect_or_create_kospex_db()
        #self.mergestat = KospexMergeStat()
        self.kospex_query = KospexQuery.KospexQuery(kospex_db=self.kospex_db)

    def set_repo_dir(self, directory):
        """ Set the repo directory """
        if not KospexUtils.is_git(directory):
            raise MissingGitDirectory(f"{directory} is not a git repo.")

        fullpath=os.path.abspath(directory)
        self.original_cwd = os.getcwd()
        self.git.set_repo(fullpath)
        self.repo_directory = fullpath
        os.chdir(fullpath)

    def chdir_original(self):
        """ Change back to the original directory """
        os.chdir(self.original_cwd)

    def get_hash(self, **kwargs):
        """ Get a hash, based on search criteria.
         Must pass in either a valid repo with -repo or a repo_id with -repo_id 
         Defaults to commits table and the older hash"""

        repo = kwargs.get('repo', None)
        repo_id = kwargs.get('repo_id', None)

        if repo and repo_id:
            raise ValueError("You can't specify both -repo and -repo_id")

        # If no repo is passed, we'll look to see if we have a repo_id
        # or use the current directory
        if repo:
            self.set_repo_dir(repo)
            repo_id = self.git.get_repo_id()

        if not repo_id:
            raise ValueError("Could not find a repo_id to query with.")

        table = kwargs.get('table', KospexSchema.TBL_COMMITS)
        oldest = kwargs.get('oldest', None)
        newest = kwargs.get('newest', None)

        order_by = None

        if table not in [ KospexSchema.TBL_COMMITS, KospexSchema.TBL_COMMIT_FILES,
                     KospexSchema.TBL_FILE_METADATA, KospexSchema.TBL_REPO_HOTSPOTS ]:
            raise ValueError(f"Unknown table {table}")

        if oldest and newest:
            raise ValueError("You can't specify both -first and -last")

        # Default to getting the oldest hash
        if oldest:
            order_by = "ASC"
        elif newest:
            order_by = "DESC"
        else:
            order_by = "ASC"

        sql_query = f"""SELECT hash, committer_when FROM {table} WHERE _repo_id = ?
            ORDER BY committer_when {order_by} LIMIT 1"""

        # Query return value might not have a 'next' method
        # This code checks for that and returns None if there is no next
        row = next(self.kospex_db.query(sql_query, [ self.git.get_repo_id() ]), None)

        self.chdir_original()

        return row

    #def sync_commit_files(self, directory, last, **kwargs):
    #    """Sync the committed files for the given directory which are
    #      more recent than the last hash"""

    #    self.set_repo_dir(directory)

    #    data_rows = []
    #    results = []

    #    if last:
    #        kwargs['hash']=last['hash']
    #    # results is the raw results from the query
    #    results = self.mergestat.commit_files(**kwargs)

    #    for row in results:
    #        row['_ext'] = KospexUtils.get_extension(row['file_path'])
    #        data_rows.append(self.git.add_git_to_dict(row))

    #    self.kospex_db.table(KospexSchema.TBL_COMMIT_FILES).insert_all(data_rows)

    #    print(f"# commit_files:\t {str(len(data_rows))}")

    #    self.chdir_original()

    #    return len(data_rows)

    def get_latest_commit_datetime(self, repo_id):
        """ Get the latest commit datetime for the given repo_id """
        cursor = self.kospex_db.execute(
                                    'SELECT MAX(committer_when) FROM commits WHERE _repo_id = ?',
                                    (repo_id,))
        latest_datetime = cursor.fetchone()[0]
        return latest_datetime

    #def sync_repo2(self, directory, **kwargs):
    def sync_repo(self, directory, limit=None, from_date=None, to_date=None, no_scc=None):
        """ Sync the commit data (authors, commmitters, files, etc) for the given directory"""
        #def sync_commits(conn, git_dir, limit=None, from_date=None, to_date=None):

        if not no_scc:
            self.file_metadata(directory)
        self.set_repo_dir(directory)
        
        #git_remote = get_git_remote()
        #kgit = KospexGit()
        #kgit.set_remote_url(git_remote)
        #print(kgit.get_repo_id())

        if not from_date:
            latest_datetime = self.get_latest_commit_datetime(self.git.get_repo_id())
            if latest_datetime:
                from_date = latest_datetime

        # Use hash (#) as a delimeter as names can contain spaces
        cmd = ['git', 'log', '--pretty=format:%H#%aI#%cI#%aN#%aE#%cN#%cE', '--numstat']

        if from_date and to_date:
            cmd += ['--since={}'.format(from_date), '--until={}'.format(to_date)]
            print(f'Syncing commits from {from_date} to {to_date}...')
        elif from_date:
            cmd += ['--since={}'.format(from_date)]
            print("Syncing commits from {}...".format(from_date))
        elif limit:
            cmd += ['-n', str(limit)]
            print(f'Syncing {limit} commits...')
        else:
            print('Syncing all commits...')

        result = subprocess.run(cmd, capture_output=True, text=True).stdout.split('\n')

        commits = []
        commit = {}

        for line in result:
            if line:
                if '\t' in line and len(line.split('\t')) == 3:
                    # Check if the line represents file stats
                    additions, deletions, filename = line.split('\t')
                    if 'filenames' in commit:
                        # The following checks for git rename events which change the filename
                        # With a git rename event, the filename will be in the format of
                        # old_filename => new_filename
                        if "=>" in filename:
                            fpath = KospexUtils.parse_git_rename_event(filename)
                            path_change = filename
                        else:
                            fpath = filename
                            path_change = None

                        commit['filenames'].append({
                            'file_path': fpath,
                            'path_change': path_change,
                            'additions': int(additions) if additions != '-' else 0,
                            'deletions': int(deletions) if deletions != '-' else 0
                        })
                elif '#' in line:

                    if commit:  # Save the previous commit
                        commits.append(commit)

                    hash_value, author_datetime, committer_datetime, author_name, author_email, committer_name, committer_email = line.split('#', 6)

                    commit = {
                        'hash': hash_value,
                        'author_when': author_datetime,
                        'committer_when': committer_datetime,
                        'author_name': author_name,
                        'author_email': author_email,
                        'committer_name': committer_name,
                        'committer_email': committer_email,
                        'filenames': []
                    }

                else:
                    commit['filenames'].append({'filename': line, 'additions': 0, 'deletions': 0})

            else:
                if commit:  # Save the last commit
                    commits.append(commit)
                commit = {}

        #cursor = conn.cursor()

        counter = 0
        print("About to insert commits into the database...")

        # Insert the commits to the database
        for commit in commits:
            counter += 1
            # Insert the commit to the database
            files = len(commit['filenames'])
            commit['_files'] = files
            commit = self.git.add_git_to_dict(commit)
            commit_files = commit['filenames']
            del commit['filenames']
            self.kospex_db.table(KospexSchema.TBL_COMMITS).upsert(commit,pk=['_repo_id', 'hash'])

            # Insert the filenames to the database
            for file_info in commit_files:
                file_info = self.git.add_git_to_dict(file_info)
                file_info['hash'] = commit['hash']
                file_info['_ext'] = KospexUtils.get_extension(file_info['file_path'])
                file_info['committer_when'] = commit['committer_when']
                self.kospex_db.table(KospexSchema.TBL_COMMIT_FILES).upsert(file_info,
                                                                pk=['file_path', '_repo_id',
                                                                    'hash'])

            #print(f'Synced commit {commit["hash"]}...')
            # we'll print a + for each commit and a newline every 80 commits
            print('+', end='')
            if (counter % 80) == 0:
                print()
            if (counter % 500) == 0:
                print(f'\nSynced {counter} commits so far ...\n')

        print()
        print(f"Synced {len(commits)} total commits")

        self.chdir_original()

    #def sync_repo(self, directory, **kwargs):
    #    """ Sync the commit data (authors, commmitters, files, etc) for the given directory"""

        # If newer, sync them
    #    newer = False
    #    first_sync = False
    #    # If we have a previous, we need to work backwards from the oldest commit we've sync'ed
    #    previous = kwargs.get('previous', None)
    #    if previous:
    #        last = self.get_hash(repo=directory, oldest=True)
    #        if last:
    #            kwargs['before'] = last['committer_when']
    #    else:
    #        last = self.get_hash(repo=directory, newest=True)
    #        newer = True
    #        if last:
    #            kwargs['after'] = last['committer_when']
    #        else:
    #            first_sync = True

        # Think we need to  save the committer_when from 'commits' for the commit_files table
    #    committer_when = last['committer_when']
        # If we're doing a first sync, we need to get the newest commit from the repo directory
        # Then we need to calculate the default number of days to sync as a date for committer_when

        # If first_sync - default last committer to 15 months
        # Otherwise, for previous, we'll use the oldest committer_when from the commits table
        # Then calculate the previous number of days to sync backwards until we hit it
    #    self.sync_commits(directory, **kwargs)
        # Get the updated last hash and committer_when
    #    updated = self.get_hash(repo=directory, oldest=True)

        # We want to sync all the commit files for the commits we've just synced
    #    params = {}
    #    if newer:
    #        last = self.get_hash(repo=directory, newest=True, table=KospexSchema.TBL_COMMIT_FILES)
    #        if last:
    #            params['after'] = last['committer_when']

    #    if previous:
    #        last = self.get_hash(repo=directory, oldest=True, table=KospexSchema.TBL_COMMIT_FILES)
            # Set a before date to the oldest commit_files table committer_when we've sync'ed
    #        if last:
    #            params['before'] = last.get('committer_when')
            # Set an after date to the oldest commit (from commits table) we've sync'ed
    #        if updated:
    #            params['after'] = updated.get('committer_when')

    #    kwargs['previous'] = None
    #    kwargs['next'] = None
    #    last = None
    #    self.sync_commit_files(directory, last, **params)

    #    self.file_metadata(directory)

    #def sync_commits(self, directory, **kwargs):
    #    """Sync the commits for the given directory to the kospex db,
    #    which are more recent than the last hash"""

    #    self.set_repo_dir(directory)
    #    print("Sync'ing repo directory: " + os.getcwd())

        # results is the raw results from the query
    #    results = self.mergestat.commits(**kwargs)
        # data_rows will be the enriched data we'll insert into the kospex db
    #    data_rows = []

    #    for row in results:
    #        data_rows.append(self.git.add_git_to_dict(row))

    #    self.kospex_db.table(KospexSchema.TBL_COMMITS).insert_all(data_rows)

    #    print("# commits:\t" + str(len(data_rows)))

    #    self.chdir_original()

        # Return the number of rows we've sync'ed
    #    return len(data_rows)

    def get_one(self, query):
        """ helper function to return a single value from a query"""
        return str(self.kospex_db.execute(query).fetchone()[0])

    def author_tech_pretty_table(self, author_techs):
        """Pretty print the author_techs data"""

        table = PrettyTable()
        headers = ["author_email", "repos", "_ext", "commits", "first_commit", 
                   "last_commit", "last_seen", "first_seen", "days_active"]
        table.field_names = headers
        table.align["author_email"] = "l"
        table.align["_ext"] = "l"
        table.align["commits"] = "r"
        for row in author_techs:
            row['_ext'] = (row['_ext'][:18] + '..') if len(row['_ext']) > 20 else row['_ext']
            row['first_commit'] = KospexUtils.extract_db_date(row['first_commit'])
            row['last_commit'] = KospexUtils.extract_db_date(row['last_commit'])
            table.add_row(KospexUtils.get_values_by_keys(row, headers))

        return table

    def summary(self, results_file=None):
        """ Display some basic stats what has been sync'ed from repos to the kospex db."""
        print("\nKospex Summary\nChecking status ...\n")
        print("# Repositories:\t" + self.get_one("SELECT COUNT(DISTINCT(_repo_id)) FROM commits"))
        print("# Authors:\t" + self.get_one("SELECT COUNT(DISTINCT(author_email)) FROM commits"))
        print("# Committers:\t" +
              self.get_one("SELECT COUNT(DISTINCT(committer_email)) FROM commits"))
        print("")

        table = PrettyTable()
        headers = ["repo", "status", "developers", "active", "present", "last_commit",
                             "first_commit", "active_days", "commits" ]

        #table.field_names = ["repo", "status", "last_commit",
        #                     "first_commit", "developers", "commits", "active", "present"]

        table.field_names = headers
        table.align["repo"] = "l"
        table.align["status"] = "l"
        table.align["last_commit"] = "r"
        table.align["first_commit"] = "r"
        table.align["active_days"] = "r"
        table.align["developers"] = "r"
        table.align["commits"] = "r"
        table.align["active"] = "r"
        table.align["present"] = "r"
        table.sortby = "last_commit"

        results = []

        repo_active_devs = self.kospex_query.active_devs()
        active_devs = self.kospex_query.active_developer_set()

        #print("Repository IDs")
        sql = '''SELECT distinct(_repo_id) as repo,
                round((julianday('now') - julianday(max(committer_when))) ,1) as last_commit,
                round((julianday('now') - julianday(min(committer_when))) ,1) as first_commit,
                count(distinct(author_email)) as developers,
                count(_repo_id) as commits
                FROM commits
                GROUP BY 1'''

        for row in self.kospex_db.query(sql):
            all_devs = self.kospex_query.authors_by_repo(row["repo"])
            set_devs = set(all_devs)
            row["status"] = KospexUtils.development_status(row["last_commit"])
            row["active"] = repo_active_devs.get(row["repo"], 0)
            row["present"] = len(set_devs.intersection(active_devs))
            row['active_days'] = round(row["first_commit"] - row["last_commit"])

            table.add_row(KospexUtils.get_values_by_keys(row, headers))

            #table.add_row([row["repo"], row["status"],
            #               row["last_commit"], row["first_commit"],
            #               row["developers"], row["commits"], row["active"], row["present"] ])
            
                           #len(set_devs.intersection(active_devs))])
            #print(row)
            results.append(row)

        if results_file:
            KospexUtils.list_dict_2_csv(results, results_file)
            print("Writing CSV results to file: " + results_file)

        if table.rows:
            print(table)
            print(KospexUtils.count_key_occurrences(results, "status"))
        else:
            print("No repositories found in the kospex DB\n")

    def active_developers(self, **kwargs):
        """
        Query the kospex db for active developers
        """
        days = kwargs.get('days', 90)
        # Make sure days is an integer before we use it in the SQL query to avoid SQL injection
        if not isinstance(days, int):
            raise ValueError(f"The value of days '{days}' is NOT an integer")

        params = []
        sql = '''SELECT distinct(author_email) as author,
        round((julianday('now') - julianday(max(author_when))) ,1) as last_seen,
        COUNT(author_email) as commits
        FROM commits'''
        where = f"WHERE date(author_when) > date('now','-{days} day')"
        group_by = '''GROUP BY author_email ORDER BY commits DESC'''

        row_count = 0
        # Handle if a Git repo is passed in
        repo = kwargs.get('repo', None)
        if repo:
            # Check if we've synced the repo
            # Check if we've got the latest (and warn if not)
            self.set_repo_dir(repo)
            kwargs['repo_id'] = self.git.get_repo_id()

        org_key = kwargs.get('org_key', None)
        if org_key:
            # Get the repo_id for the org_key
            org_bits = org_key.split("~")
            if org_bits and len(org_bits) == 2:
                server = org_bits[0]
                org = org_bits[1]
                #[_git_server] TEXT,
                #[_git_owner] TEXT,
                where = f"WHERE _git_server = ? AND _git_owner = ? AND date(author_when) > date('now','-{days} day')"
                params.append(server)
                params.append(org)
            #kwargs['repo_id'] = self.kospex_query.repo_id_by_org_key(org_key)

        if kwargs.get('repo_id', None):
            where = f"WHERE _repo_id = ? AND date(author_when) > date('now','-{days} day')"
            params.append(kwargs['repo_id'])

        sql_query = f"{sql} {where} {group_by}"

        table = PrettyTable()
        table.field_names = ["username", "last_seen", "commits", "author"]
        table.align["username"] = "l"
        table.align["last_seen"] = "r"
        table.align["commits"] = "r"
        table.align["author"] = "l"

        results = None

        if params:
            results = self.kospex_db.query(sql_query, params)
        else:
            results = self.kospex_db.query(sql_query)

        records = []

        for row in results:
            row["username"] = KospexUtils.extract_github_username(row["author"])
            records.append(row)
            table.add_row([row["username"],row["last_seen"], row["commits"],row["author"]])
            row_count += 1

        if row_count > 0:
            print(table)

        if row_count == 0:
            print("No active developers found in the kospex DB")
        else:
            print("Total active developers: " + str(row_count))

        outfile = kwargs.get("out",None)
        if outfile:
            KospexUtils.list_dict_2_csv(records, outfile,table.field_names)

    def list_repos(self, directory):
        """ Print all the git repos in the specified directory and subdirectories"""
        table = PrettyTable()
        #table.field_names = ["Path", "Full path", "Remote"]
        table.field_names = ["Path", "Remote"]
        table.align["Path"] = "l"
        #table.align["Full path"] = "l"
        table.align["Remote"] = "l"

        results = KospexUtils.find_repos(directory)
        for file in results:
            self.git.set_repo(file)
            #table.add_row([file, os.path.abspath(file), self.git.get_remote_url()])
            table.add_row([file, self.git.get_remote_url()])

        print(table)

    def file_repo_details(self, file):
        """ return a hash of the kospex git details, including hash. """

        repo_base = KospexUtils.find_git_base(file)
        repo_info = None
        #self.set_repo_dir(KospexUtils.find_git_base(file))
        if repo_base:
            self.set_repo_dir(repo_base)
            repo_info = self.git.add_git_to_dict({})
            repo_info['hash'] = self.git.get_current_hash()
            repo_info['file_path'] = file

        return repo_info


    def tech_landscape(self, **kwargs):
        """ Display the tech landscape using file extensions from commits or output from 'scc'. 
        Options:
        -repo_id to limit to a specific repo_id
        -metadata to use the file metadata instead of the committed files
        -commits to use the committed files history instead of the file metadata
        -repo to use a specific repo directory as a reference instead of user passing repo_id
        """
        repo_id = kwargs.get('repo_id', None)
        metadata = kwargs.get('metadata', False)
        repo = kwargs.get('repo', None)

        where_clause = ""
        sync_warnings = []

        if repo_id and repo:
            raise ValueError("Cannot specify both repo_id and repo")

        if repo:
            self.set_repo_dir(repo)
            #if self.needs_sync():
            #    sync_warning = True
            repo_id = self.git.get_repo_id()

        if repo_id:
            if metadata:
                where_clause = "AND _repo_id = ?"
            else:
                where_clause = "WHERE _repo_id = ?"

        commits_sql = f'''SELECT distinct(_ext) as Extension, count(distinct(file_path)) as Files
                FROM commit_files {where_clause}
                GROUP BY 1
                ORDER BY Files DESC'''

        metadata_sql = f'''SELECT Language, count(*) as Files
                FROM file_metadata
                WHERE latest = 1 {where_clause}
                GROUP BY Language
                ORDER BY Files DESC'''

        if metadata:
            sql = metadata_sql
        else:
            sql = commits_sql

        cursor = None
        if repo_id:
            cursor = self.kospex_db.execute(sql, [repo_id])
        else:
            cursor = self.kospex_db.execute(sql)

        table = from_db_cursor(cursor)
        table.align["Language"] = "l"
        table.align["Extension"] = "l"
        table.align["Files"] = "r"
        print(table)

        if sync_warnings:
            print("\nWARNING: some kospex DB tables are out sync with the repo.")
            for warning in sync_warnings:
                print(warning)

    #def repo_summary(self, repo_dir):
    #    """ Display a summary of the repo, for a specific repo directory.
    #        The output includes the file extension, number of commits, % of commits."""
    #    self.set_repo_dir(repo_dir)
    #    results = []
    #    extensions = {}

    #    print(f"\nrepo ID: {str(self.git.get_repo_id())}")

    #    sql = '''select DISTINCT(file_path), count(*) as commits
    #    FROM commits, stats('', commits.hash) GROUP BY 1'''

    #    for row in self.mergestat.cursor().execute(sql):
    #        row['_ext'] = KospexUtils.get_extension(row['file_path'])
    #        results.append(row)
    #        if row['_ext'] in extensions:
    #            extensions[row['_ext']] += 1
    #        else:
    #            extensions[row['_ext']] = 1

    #    table = PrettyTable()
    #    table.field_names = ["Extension", "#", "%"]
    #    table.align["Extension"] = "l"
    #    table.align["#"] = "r"
    #    table.align["%"] = "r"

    #    num_files = len(results)

    #    for ext, count in extensions.items():
    #        percentage = (count / num_files) * 100
    #        table.add_row([ext, count, f"{percentage:0.2f}"])
    #    print(table)
    #    print(f"Total files: {str(num_files)}\n")
    #    self.chdir_original()

    def file_metadata(self, repo_directory):
        """ Get some basic metadata about the files in the repo using 'scc'"""
        self.set_repo_dir(repo_directory)
        git_hash = self.git.get_current_hash()
        repo_id = self.git.get_repo_id()

        # Check to see if we have already got metadata for this repo and hash
        sql = f"""SELECT hash, _repo_id FROM {KospexSchema.TBL_FILE_METADATA}
        WHERE hash = ? and _repo_id = ?"""
        row = self.kospex_db.execute(sql, [git_hash, repo_id]).fetchone()

        if row:
            print(f"Already have metadata for {git_hash} and repo_id {str(repo_id)}")
        else:
            # scc wont' analyse everything, so we need to do a file find for items not analysed
            files = self.git.get_repo_files()
            # This will be a dict of file paths and their metadata

            # Check we've got scc installed
            installed = which('scc')
            if not installed:
                sys.exit("scc is not installed. Please install scc from https://github.com/boyter/scc")
            # Let's grab the file metadata using 'scc'
            metadata = subprocess.run(["scc", "--by-file", "-f", "csv"],
                                      stdout=subprocess.PIPE,
                                      text=True, check=False)
            data_rows = []
            csv_reader = csv.DictReader(metadata.stdout.splitlines())
            for row in csv_reader:
                row['hash'] = git_hash
                # TODO - this doesn't work, a newly cloned repo will have mtime of when it was cloned
                #row['_mtime'] = os.path.getmtime(row['Filename'])
                # Set this entry to the latest. Required for tech landscape queries
                row['latest'] = True
                data_rows.append(self.git.add_git_to_dict(row))

            # Reset "last" flags to false
            reset_last_sql = f"""UPDATE {KospexSchema.TBL_FILE_METADATA} SET LATEST = 0
            WHERE _repo_id = ?"""
            self.kospex_db.execute(reset_last_sql, [repo_id])

            self.kospex_db.table(KospexSchema.TBL_FILE_METADATA).insert_all(data_rows)

            print("Added " + str(len(data_rows)) + " rows to " +
                  KospexSchema.TBL_FILE_METADATA + " for repo_id " + str(self.git.get_repo_id()))

        self.chdir_original()

    def sync_metadata(self, data_directory):
        """ Find all git repos and sync the metadata to the kospex database """
        repos = KospexUtils.find_repos(data_directory)
        print(f"Found {str(len(repos))} repos")
        for repo in repos:
            print(f"Syncing metadata for '{repo}'")
            self.file_metadata(repo)

    def hotspot(self, **kwargs):
        """ Calculate the hotspots for a repo on disk. """
        # Prequesites - This directory has been synced to commits, files and with file_metadata
        # Find the number of commits in each repo and the 1st and last time we've seen it active

        repo_directory = kwargs.get('repo', None)

        # Call the helper and raise an exception if there are any issues
        KospexUtils.validate_params(**kwargs)

        self.set_repo_dir(repo_directory)
        git_hash = self.git.get_current_hash()
        repo_id = self.git.get_repo_id()

        # Get the more recent commit hash of the directory from the kospex DB
        last_sync = self.get_hash(repo_id=repo_id, newest=True)

        if last_sync and git_hash == last_sync['hash']:

            sql = '''SELECT _repo_id, count(*) as commits, min(committer_when) as first_seen,
            count(DISTINCT(author_email)) as authors,
            max(committer_when) as last_seen 
            FROM commits 
            WHERE _repo_id = ?
            GROUP BY 1
            ORDER BY 2
            '''
            row = self.kospex_db.query(sql, [repo_id])
            result = next(row, None)
            print(result)

            metadata = self.get_last_metadata_files(repo_id)
            loc = 0
            files = 0
            for file in metadata:
                loc += file['Lines']
                files += 1
                self.get_file_metadata_hotspot(repo_id, file['Filename'], git_hash)
            # Create an entry in the hotspots table for the repo
            # Add the Lines of code we've seen
            result['loc'] = loc
            result['files'] = files
            result['hash'] = git_hash

            self.kospex_db.table(KospexSchema.TBL_REPO_HOTSPOTS).upsert(
                self.git.add_git_to_dict(result), pk=['_repo_id', 'hash'])

            # TODO - remove this debug code
            print(result)
            metadata = self.get_last_metadata_files(repo_id)
            for file in metadata:
                print(file)

        else:
            print("\nRepo is out of sync with Kospex DB")
            print(f"run 'kospex sync {repo_directory}' to sync the repo with the DB")

        self.chdir_original()

    def get_last_metadata_files(self, repo_id):
        """ Get the last metadata files for a specific repo """
        sql = '''SELECT * FROM file_metadata WHERE _repo_id = ? AND latest = 1'''
        return self.kospex_db.query(sql, [repo_id])

    def get_metadata_files(self, repo_id, git_hash):
        """ Get the metadata files for a specific repo and hash """
        sql = '''SELECT * FROM file_metadata WHERE _repo_id = ? AND hash = ?'''
        return self.kospex_db.query(sql, [repo_id, git_hash])

    def get_file_metadata_hotspot(self, repo_id, filename, git_hash):
        """ Get the file metadata for a specific file and hash """
        # TODO - make this git_hash aware, and query there the commits are <= git_hash commit time
        sql = '''SELECT file_path as Location, COUNT(*) as commits,
        COUNT(DISTINCT(author_email)) as authors, commits._repo_id
        FROM commits, commit_files 
        WHERE commits._repo_id = ? AND 
        commit_files._repo_id = ? AND
        file_path = ? AND
        commits._repo_id = commit_files._repo_id AND
        commits.hash = commit_files.hash
        GROUP BY 1'''
        # Next, upsert the calculated hotspot data into the file_metadata table
        result = next(self.kospex_db.query(sql, [repo_id, repo_id, filename]), None)
        # If we don't have a result, then this file is not managed by git
        # and most likely a local file which has not been committed
        if result:
            result['hash'] = git_hash
            self.kospex_db.table(KospexSchema.TBL_FILE_METADATA).upsert(
                result, pk=['Location', 'hash', '_repo_id'])
            print(f"Syncing {filename}")
        else:
            print(f"{filename} is not managed by Git")

    def get_krunner_directory(self):
        """ Get the directory where the krunner files are stored """
        # TODO - check the init process in KospexUtils .. need to be run on first run
        krunner_path = os.path.expanduser("~/kospex/krunner")
        if not os.path.exists(krunner_path):
            os.makedirs(krunner_path)
        return krunner_path

    def generate_krunner_filename(self, function=None, ext="out"):
        """ Get the path to a krunner file """
        krunner_path = self.get_krunner_directory()
        # TODO - do better path join method and validate no .. etc
        return os.path.join(krunner_path, self.git.get_repo_id() + "."  + function + "." + ext)
    
