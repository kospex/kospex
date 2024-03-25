"""Core mergestat functions and queries for running the kospex CLI"""
import sqlite3
import os
import os.path
import datetime
from kospex_git import KospexGit, MissingGitDirectory
import kospex_utils as KospexUtils
import kospex_schema as KospexSchema

#
# WARNING note: This code works, but is no longer used in kospex
# It is kept here for reference and possible future use
#

class KospexMergeStat:
    """ Utility class for MergeStat """
    def __init__(self, repo_directory=None):

        self.original_cwd = None
        self.git = KospexGit()

        if repo_directory:
            if not KospexUtils.is_git(repo_directory):
                raise MissingGitDirectory(f"{repo_directory} is not a git repo.")

        if repo_directory:
            self.git.set_repo(repo_directory)

        mergestat_path = os.environ.get('MERGESTAT_PATH', "/usr/local/bin")

        # Default number of days to sync on an initial run
        # Set to ~1.25 years to allow for comparing last 90 days to 1 year ago
        self.initial_days_synced = 458

        # We need to set up mergestat as a sqlite3 extension
        self.mergestat = sqlite3.connect(":memory:")
        self.mergestat.enable_load_extension(True)
        self.mergestat.execute(f"select load_extension('{mergestat_path}/libmergestat')")
        self.mergestat.enable_load_extension(False)
        # By default, we will use the dict_factory to return a dict instead of a tuple
        self.mergestat.row_factory = KospexSchema.dict_factory
        #self.mergestat.row_factory = sqlite3.Row

    def set_repo(self, directory):
        """ Set the repo to be used for queries """
        if not KospexUtils.is_git(directory):
            raise MissingGitDirectory(f"{directory} is not a git repo.")

        self.git.set_repo(directory)
        self.original_cwd = os.getcwd()
        os.chdir(directory)

    def cursor(self):
        """ Return the cursor for the mergestat object """
        return self.mergestat.cursor()

    def set_initial_days_synced(self, days):
        """ Set the number of days to sync on an initial run """
        self.initial_days_synced = days


    def query_local_repo(self, sql_query, directory=None):
        """ use mergestat to query the local git repo (directory) for the given sql_query"""
        if directory:
            self.set_repo(directory)
        else:
            self.set_repo(os.getcwd())

        for row in self.mergestat.cursor().execute(sql_query):
            print(self.git.add_git_to_dict(row))

    def get_commit_by_hash(self, git_hash):
        """ Get a commit by hash """
        sql_query = f"SELECT * FROM commits WHERE hash = '{git_hash}'"
        return self.mergestat.cursor().execute(sql_query).fetchone()

    def execute_with_where(self, sql_query, **kwargs):
        """
        Without args get all the commits for the last initial_days_synced days
        With -before to get all the commits before a date
        With -hash to get all the commits after a hash
        With -previous to get the previous N commits before a date
        with -before and -previous to get the previous N commits before a date
        with -next to get the next N commits after a date
        """
        params = []
        where_clause = ""
        order_by_clause = "ORDER BY committer_when DESC"
        limit_clause = ""

        # How many 'previous' commits to return (and sync)
        # either based on a before date set by -before or the date of a commit hash set by -hash
        previous = kwargs.get('previous', None)
        before = kwargs.get('before', None)
        next_commits = kwargs.get('next', None)
        after = kwargs.get('after', None)

        # Call the helper and raise an exception if there are any issues
        KospexUtils.validate_params(**kwargs)

        if not after and not previous:
            # If we don't have an after date, let's use the default initial_days_synced
            after = str(datetime.datetime.today()
                         - datetime.timedelta(days=self.initial_days_synced))
            # the not previous handles the case where there is no sync yet, but we want the
            # previous 'x' commits from the latest commit. Most likely because the repo is huge

        if after and before:
            where_clause = "WHERE committer_when > ? AND committer_when < ?"
            params.append(after)
            params.append(before)
        elif before:
            where_clause = "WHERE committer_when < ?"
            params.append(before)
        elif after:
            where_clause = "WHERE committer_when > ?"
            params.append(after)

        if previous:
            limit_clause = "LIMIT ?"
            params.append(previous)

        if next_commits:
            limit_clause = "LIMIT ?"
            params.append(next_commits)

        query = f"{sql_query}\n{where_clause}\n{order_by_clause}\n{limit_clause}"

        return self.mergestat.cursor().execute(query, params)

    def commits(self, **kwargs):
        """ Query the commits and return a cursor
        See method execute_with_where for details on the kwargs
        """
        commits_sql = '''SELECT hash, author_email, author_name, author_when,
            committer_email, committer_name, committer_when, message, parents,
            ROUND((JULIANDAY(committer_when) - JULIANDAY(author_when)) * 86400) AS _cycle_time
            FROM commits
            '''
        # _cycle_time is the time in seconds between authoring and committing

        return self.execute_with_where(commits_sql, **kwargs)

    def commit_files(self, **kwargs):
        """ Query the commit_files and return a cursor
        See method execute_with_where for details on the kwargs
        """

        commit_files_sql = '''SELECT hash, file_path, additions, deletions, committer_when
            FROM commits, stats('', commits.hash)
            '''
        # Note:  _ext we'll generate later based on the file_path

        return self.execute_with_where(commit_files_sql, **kwargs)
