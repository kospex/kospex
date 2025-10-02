""" Use case queries for the kospex DB"""
from ctypes import memmove
from logging import lastResort
import time
import re
from typing import Optional
from collections import defaultdict
from datetime import datetime, timezone, timedelta
import json
from sqlite_utils import Database
import kospex_utils as KospexUtils
from kospex_utils import KospexTimer
import kospex_schema as KospexSchema
import requests
from kospex_observation import Observation
import panopticas as Panopticas


class KospexQuery:
    """kospex database query functionality"""

    def __init__(self, kospex_db=None):
        # Initialize the kospex environment
        KospexUtils.init()
        self.kospex_db = kospex_db or Database(KospexUtils.get_kospex_db_path())

    def get_kospex_db_version(self):
        """
        Return the KOSPEX_DB_VERSION stored in the KOSPEX_CONFIG table
        """
        version_sql = f"SELECT value FROM {KospexSchema.TBL_KOSPEX_CONFIG} where key = ? AND latest = 1"
        data = next(self.kospex_db.query(version_sql, [KospexSchema.KOSPEX_DB_VERSION_KEY]), None)
        version = None
        if data:
            try:
                version = int(data['value'])
            except (ValueError, TypeError):
                version = 0

        return version

    def summary(self, days=None, repo_id=None, org_key=None):
        """
        Provide a summary of the known repositories.
        """
        # Used to hold the date value calculated from days
        from_date = None
        data = {} # Hold the data, should be a list of authors, commiters, total orgs

        kd = KospexData(self.kospex_db)
        kd.from_table(KospexSchema.TBL_COMMITS)
        kd.select_raw("COUNT(DISTINCT(_repo_id)) as repos")
        kd.select_as("count(*)",'commits')
        kd.select_raw("COUNT(DISTINCT(LOWER(author_email))) as authors")
        kd.select_raw("COUNT(DISTINCT(LOWER(committer_email))) as committers")
        kd.select_raw("COUNT(DISTINCT(_git_server)) as servers")

        if days:
            from_date = KospexUtils.days_ago_iso_date(days)
            kd.where("committer_when", ">", from_date)

        if repo_id:
            kd.where('_repo_id', "=", repo_id)

        if org_key:
            kd.where_org_key(org_key)

        results = kd.execute()
        if results:
            data = results[0]

        # TODO - need to add params to orgs
        orgs = self.orgs()
        if results:
            data['orgs'] = len(orgs)

        return data

    def create_memory_kospex_query(self, table_names):
        """
        Return an in memory db kospex_query object
        with the specified tables
        """

        memory_db = Database(memory=True)
        physical_db_path = KospexUtils.get_kospex_db_path()

        # Attach the physical database
        memory_db.execute(f"ATTACH DATABASE '{physical_db_path}' AS disk_db")

        # Copy each table using direct SQL (much faster than row-by-row)
        for table in table_names:
            memory_db.execute(f"CREATE TABLE {table} AS SELECT * FROM disk_db.{table}")

        # Detach the database
        memory_db.execute("DETACH DATABASE disk_db")

        # for table in table_names:
        #     if table in self.kospex_db.table_names():
        #         memory_db[table].insert_all(self.kospex_db[table].rows, alter=True)

        return KospexQuery(kospex_db=memory_db)

    def repo_summary(self, repo_id):
        """ Provide a summary of the known repositories."""
        summary_sql = """SELECT count(distinct(_repo_id)) 'repos', count(*) 'commits',
        count(distinct(author_email)) 'authors', count(distinct(committer_email)) 'committers'
        FROM commits
        WHERE _repo_id = ?
        """
        data = next(self.kospex_db.query(summary_sql, [repo_id]), None)
        return data

    def server_summary(self,id=None):
        """
        Provide a summary of the known servers.
        """
        params = []
        if id:
            params.append(id)
            where_clause = "WHERE _git_server = ?"
        else:
            where_clause = ""

        summary_sql = f"""SELECT _git_server, count(distinct(_repo_id)) 'repos',
        count(distinct(author_email)) 'developers'
        FROM commits
        {where_clause}
        GROUP BY _git_server
        """
        print(summary_sql)
        print(params)
        data = []
        for row in self.kospex_db.query(summary_sql,params):
            data.append(row)
        return data

    def tech_landscape(self, repo_id=None,org_key=None):
        """ Calculate the technology landscape."""

        where_clause = ""
        params = []
        if repo_id:
            where_clause += "AND _repo_id = ? "
            params.append(repo_id)

        # TODO - clean this up and make more generic for use in other queries
        if org_key:
            parts = org_key.split('~')
            if len(parts) != 2:
                raise ValueError("org_key must be of the form <server>~<owner>")
            params.append(parts[0])
            params.append(parts[1])
            where_clause += "AND _git_server = ? AND _git_owner = ?"

        summary_sql = f"""SELECT Language, count(*) 'count', count(distinct(_repo_id)) 'repos'
        FROM file_metadata
        WHERE latest = 1 {where_clause}
        GROUP BY Language
        ORDER BY count DESC
        """

        data = []

        #if repo_id:
        #    for row in self.kospex_db.query(summary_sql, params):
        #        data.append(row)
        #else:
        for row in self.kospex_db.query(summary_sql, params):
            data.append(row)

        return data

    def repos_with_tech(self, tech):
        """ Find repos with the given technology."""

        params = []
        params.append(tech)

        summary_sql = """SELECT _repo_id, _git_server, _git_owner, _git_repo, count(*) 'count'
        FROM file_metadata
        WHERE Language = ? AND latest = 1
        GROUP BY _repo_id
        ORDER BY count DESC
        """
        data = []

        for row in self.kospex_db.query(summary_sql, params):
            data.append(row)

        return data

    def repo_files(self, tech=None, repo_id=None):
        """ Grab files by metadata type for a given repo_id."""

        where_clause = ""

        params = []
        if tech:
            where_clause += "AND Language = ? "
            params.append(tech)

        if repo_id:
            where_clause += "AND _repo_id = ?"
            params.append(repo_id)

        summary_sql = f"""SELECT _repo_id, _git_server, _git_owner, _git_repo,
        Provider, Filename, committer_when, Language, tech_type, hash, Lines, latest
        FROM file_metadata
        WHERE latest = 1 {where_clause}
        """
        data = []

        for row in self.kospex_db.query(summary_sql, params):
            data.append(row)

        return data

    def get_last_commit_file(self,repo_id,file_path):
        """
        Get the last commit for a given file path in a repository.
        """
        sql = """SELECT _repo_id, _git_server, _git_owner, _git_repo,
        file_path, committer_when
        FROM commit_files
        WHERE _repo_id = ? AND file_path = ?
        ORDER BY committer_when DESC LIMIT 1
        """
        params = [repo_id, file_path]
        data = next(self.kospex_db.query(sql, params), None)

        return data

    def get_dependency_files(self,request_id=None):
        """
        Get the dependency for the given scope.
        """

        kd = KospexData(self.kospex_db)
        kd.from_table(KospexSchema.TBL_FILE_METADATA)
        kd.where("latest", "=", 1)
        if request_id:
            if repo_id := request_id.get("repo_id"):
                kd.where("_repo_id","=",repo_id)
            elif org_key := request_id.get("org_key"):
                kd.where_org_key(org_key)
            elif server := request_id.get("server"):
                kd.where("_git_server","=",server)
            else:
                print(f"ERROR: can't identify {request_id}")

        kd.where("tech_type", "LIKE", "%|dependencies|%")

        results = kd.execute()

        return results

    def get_dependencies(self,request_id=None):
        """
        Get the individual dependencies for the given scope.
        """

        kd = KospexData(self.kospex_db)
        kd.from_table(KospexSchema.TBL_DEPENDENCY_DATA)
        #kd.where("latest", "=", 1)

        if request_id:
            if repo_id := request_id.get("repo_id"):
                kd.where("_repo_id","=",repo_id)
            elif org_key := request_id.get("org_key"):
                kd.where_org_key(org_key)
            elif server := request_id.get("server"):
                kd.where("_git_server","=",server)
            else:
                print(f"ERROR: can't identify {request_id}")

        results = kd.execute()

        return results



    def get_orphans(self,id=None):
        """
        Get the orphaned repos for the given scope.
        """

        kd = KospexData(self.kospex_db)
        kd.from_table(KospexSchema.TBL_DEPENDENCY_DATA)
        kd.set_params_by_id(id)

        active_devs = self.developers(days=90)
        active_set = set(map(lambda item: item['author'], active_devs))

        print(active_set)

        repos = self.repos2(id=id)

        window = 365
        now_utc = datetime.now(timezone.utc)
        from_date = now_utc - timedelta(days=window)

        results = []

        # Loop through every repo
        for r in repos:
            # find all the authors in the last 'window' days
            row = {}
            row["_repo_id"] = r["_repo_id"]
            row["_git_repo"] = r["_git_repo"]

            commits = self.commits(
                repo_id=r["_repo_id"],
                after=from_date.strftime("%Y-%m-%dT%H:%M:%S%z"))

            #commits = self.commits(
            #    repo_id=r["_repo_id"] )

            committers = set([c['author_email'] for c in commits])
            row["committers"] = len(committers)
            print("\n")
            print(f"committers: {committers}\n\n")

            intersection_count = len(committers.intersection(active_set))
            row["intersection"] = intersection_count

            print(f"Present: {intersection_count}, Total: {len(committers)} in 12 months.")

            # if all the authors are not in the active_devs_emails list
            # then print the repo status of orphaned

            if intersection_count == 0:
                print("Orphaned")
                row["orphaned"] = True
                row["percentage"] = 0
                #orphaned += 1
            else:
                print("Working knowledge exists")
                row["orphaned"] = False
                row["percentage"] = f"{intersection_count/len(committers)*100:.2f}"
                #row.append(f"{intersection_count/len(committers)*100:.2f}%")
                #working_knowledge += 1

            results.append(row)
            #table.add_row(row)
            #print()

        #results = kd.execute()

        return results

    def repos_by_author(self, author_email):
        """ Find repos for the given author_email."""

        summary_sql = """SELECT _repo_id, count(*) 'commits', MAX(committer_when) 'last_commit', MIN(committer_when) 'first_commit'
        FROM commits
        WHERE LOWER(author_email) = ?
        GROUP BY _repo_id
        ORDER BY commits DESC
        """
        data = []

        for row in self.kospex_db.query(summary_sql, [author_email.lower()]):
            row['last_seen'] = KospexUtils.days_ago(row['last_commit'])
            days_between = KospexUtils.days_between_datetimes(row['first_commit'], row['last_commit'])
            if days_between > 0:
                row['years_active'] = f"{days_between / 365:.3f}"
            else:
                row['days_between'] = 0
            data.append(row)

        return data

    def get_repo_sync_data(self, limit=None):
        """
        Get the sync and last commit data for repositories from the "repos" table.
        """
        kd = KospexData(self.kospex_db)
        kd.from_table(KospexSchema.TBL_COMMITS)
        kd.select("*")
        #kd.set_params_by_id(id)
        if limit:
            kd.limit(limit)

          # [_repo_id] TEXT,
          # [_git_server] TEXT,
          # [_git_owner] TEXT,
          # [_git_repo] TEXT,
          # [created_at] DEFAULT CURRENT_TIMESTAMP,
          # [last_sync] TEXT,  -- date of last kospex sync
          # [last_seen] TEXT,  -- date of last commit, to be updated by the sync
          # [first_seen] TEXT, -- date of first commit, to be updated by the sync
          # [git_remote] TEXT, -- URL of the git repo
          # [file_path] TEXT,  -- path to the repo on the local filesystem
          # PRIMARY KEY(_repo_id)
          #
        results = kd.execute()

        for row in results:
            row['days_ago'] = KospexUtils.days_ago(row['last_commit'])
            row['status'] = KospexUtils.development_status(row['last_commit'])

        return results

    def repos2(self,id=None):
        """
        Provide a summary of the known repositories.
        """

        kd = KospexData(self.kospex_db)
        kd.from_table(KospexSchema.TBL_COMMITS)
        kd.set_params_by_id(id)

        kd.select_as("count(*)", "commits")
        kd.select_as("MIN(committer_when)", "first_commit")
        kd.select_as("MAX(committer_when)", "last_commit")

        # TODO = Fix this COUNT DISTINCT so it works as a "select_as"
        kd.select_raw("COUNT(DISTINCT(author_email)) as authors")
        kd.select_raw("COUNT(DISTINCT(committer_email)) as committers")

        kd.select_git_details()

        kd.group_by("_repo_id")
        kd.order_by("_repo_id")

        results = kd.execute()

        for row in results:
            row['days_ago'] = KospexUtils.days_ago(row['last_commit'])
            row['status'] = KospexUtils.development_status(row['last_commit'])

        return results

    def repos(self,org_key=None,server=None,repo_id=None,id=None):
        """ Provide a summary of the known repositories."""
        params = []
        where = ""

        # TODO - clean this up and make more generic for use in other queries
        if org_key:
            parts = org_key.split('~')
            if len(parts) != 2:
                raise ValueError("org_key must be of the form <server>~<owner>")
            params.append(parts[0])
            params.append(parts[1])
            where = "WHERE _git_server = ? AND _git_owner = ?"
        elif repo_id:
            where = "WHERE _repo_id = ?"
            params.append(repo_id)
        elif server:
            where = "WHERE _git_server = ?"
            params.append(server)

        summary_sql = f"""SELECT _repo_id, _git_server, _git_owner, _git_repo, count(*) 'commits',
        count(distinct(author_email)) 'authors', count(distinct(committer_email)) 'committers',
        MAX(committer_when) 'last_commit'
        FROM commits {where}
        GROUP BY _repo_id
        ORDER BY _repo_id
        """

        data = []
        for row in self.kospex_db.query(summary_sql, params):
            data.append(row)

        for row in data:
            row['days_ago'] = KospexUtils.days_ago(row['last_commit'])
            row["status"] = KospexUtils.development_status(row['last_commit'])


        return data

    def orgs(self):
        """ Provide a summary of the known orgs."""
        summary_sql = """SELECT _git_server, _git_owner, count(*) 'commits',
        COUNT(DISTINCT(_git_repo)) AS repos,
        COUNT(DISTINCT(LOWER(author_email))) 'authors', COUNT(DISTINCT(LOWER(committer_email))) 'committers',
        MAX(committer_when) 'last_commit', _git_server || "~" || _git_owner AS org_key
        FROM commits
        GROUP BY _git_server, _git_owner
        ORDER BY commits DESC
        """
        data = []
        for row in self.kospex_db.query(summary_sql):
            row['org'] = row['_git_owner']
            row['days_ago'] = KospexUtils.days_ago(row['last_commit'])
            data.append(row)

        return data

    def commit(self, repo_id, commit_hash):
        """ Get a specific commit by repo_id and commit hash."""
        summary_sql = """SELECT _repo_id as repo_id, hash, author_when, author_name,
        author_email, committer_when, committer_name, committer_email, _cycle_time, _files as files
        FROM commits
        WHERE _repo_id = ? AND hash = ?
        """
        data = next(self.kospex_db.query(summary_sql, [repo_id, commit_hash]), None)
        return data

    def commit_files(self, repo_id, commit_hash):
        """ Get the files for a specific commit by repo_id and commit hash."""
        summary_sql = """SELECT _repo_id as repo_id, hash, file_path, additions, deletions
        FROM commit_files
        WHERE _repo_id = ? AND hash = ?
        """
        data = []
        for row in self.kospex_db.query(summary_sql, [repo_id, commit_hash]):
            data.append(row)

        return data

    def get_commit_files(self,repo_id=None):
        """
        Get all commit files based on criteria
        """
        kd = KospexData(self.kospex_db)
        kd.from_table(KospexSchema.TBL_COMMIT_FILES)
        if repo_id:
            kd.where("_repo_id", "=", repo_id)

        return kd.execute()

    def get_file_collaborators(self, repo_id, file_path):
        """
        Get the author committer states dependencies for the given repo_id and file_path.
        This is used to find the collaborators for a specific file in a repository.
        """

        kd = KospexData(self.kospex_db)
        kd.from_table(KospexSchema.TBL_COMMITS)
        kd.select("author_email")
        kd.select("author_when")
        kd.select("committer_email")
        kd.select("committer_when")
        kd.select("hash")
        kd.where("_repo_id", "=", repo_id)
        kd.where_commit_filename(repo_id, file_path)
        results = kd.execute()

        return results

    def commits(self, repo_id=None, before=None, after=None, limit=None,
                hash=None, author_email=None, committer_email=None):
        """ Provide a summary of the known repositories."""
        summary_sql = """SELECT _repo_id, hash, author_when, author_name,
        author_email, committer_when, committer_name, committer_email, _files
        FROM commits
        WHERE 1=1
        """
        # TODO - this was generated by CoPilot, need to test it, but looks interesting
        params = []

        if repo_id:
            summary_sql += " AND _repo_id = ?"
            params.append(repo_id)

        if before:
            summary_sql += " AND committer_when < ?"
            params.append(before)

        if after:
            summary_sql += " AND committer_when > ?"
            params.append(after)

        if hash:
            summary_sql += " AND hash = ?"
            params.append(hash)

        if author_email:
            summary_sql += " AND author_email = ?"
            # TODO - space to + replacement is due to Github giving 123+gh-username@users...
            # Need to think of a more elegant solution
            params.append(author_email.replace(' ', '+'))

        if committer_email:
            summary_sql += " AND committer_email = ?"
            # TODO - space to + replacement is due to Github giving 123+gh-username@users...
            # Need to think of a more elegant solution
            params.append(committer_email.replace(' ', '+'))

        summary_sql += " ORDER BY committer_when DESC"

        if limit:
            summary_sql += " LIMIT ?"
            params.append(limit)

        #print(f"SQL: {summary_sql}")

        data = []
        for row in self.kospex_db.query(summary_sql, params):
            data.append(row)

        return data

    def active_devs(self, days=90, org=False, org_key=None):
        """ Look for distinct developers in the last 'days' """
        from_date = KospexUtils.days_ago_iso_date(days)
        repos = {}
        # TODO implement org_key

        summary_sql = """SELECT _repo_id, count(distinct(author_email)) 'devs'
        FROM commits
        WHERE committer_when > ?
        GROUP BY _repo_id
        """

        if org:
            summary_sql = """SELECT _git_server, _git_owner, count(distinct(author_email)) 'devs',
            _git_server || "~" || _git_owner AS org_key
            FROM commits
            WHERE committer_when > ?
            GROUP BY _git_server, _git_owner
            """

        data = self.kospex_db.query(summary_sql, (from_date,))
        for row in data:
            if org:
                repos[row['org_key']] = row['devs']
            else:
                repos[row['_repo_id']] = row['devs']

        return repos

    @KospexUtils.timer()
    def developers(self, org_key=None,repo_id=None,server=None,days=None,to_date=None,from_date=None):
        """

        days means commits in the last 'X' days, based on the committer_when
        after_date means commits after 'X' date, based on the committer_when
        before_date means commits before 'X' date, based on the committer_when

        Return a list of developers (based on author_email) with meta data
            author_email AS author
            first_commit
            last_commit
            days_active
            status

        """

        kd = KospexData(self.kospex_db)
        kd.from_table(KospexSchema.TBL_COMMITS)
        #kd.select_as("DISTINCT(author_email)", "author")
        #kd.select_as("DISTINCT(LOWER(author_email))", "author")
        kd.select_raw("DISTINCT(LOWER(author_email)) as author")
        kd.select_as("MIN(committer_when)", "first_commit")
        kd.select_as("MAX(committer_when)", "last_commit")
        kd.select_as("count(*)",'commits')

        kd.group_by("author")

        # TODO - Think if we want to sanity check
        # There should only be one of repo_id, org_key or server used

        if to_date:
            kd.where("committer_when", "<", to_date)

        if from_date:
            kd.where("committer_when", ">", from_date)

        if days:
            from_date = KospexUtils.days_ago_iso_date(days)
            kd.where("committer_when", ">", from_date)

        if repo_id:
            kd.where("_repo_id", "=", repo_id)

        if org_key:
            kd.where_org_key(org_key)

        if server:
            print(f"Server: {server}")
            kd.where("_git_server", "=", server)

        results =  kd.execute()

        for i in results:

            i["status"] = KospexUtils.development_status(i['last_commit'])
            i['tenure'] = KospexUtils.days_between_datetimes(i['first_commit'],
                i['last_commit'],min_one=True)
            i['years_active'] = round(i['tenure'] / 365,2) if i['tenure'] else 0


        return results

    def active_developer_set(self, days=90):
        """ Look for distinct developers in the last 'days' """
        from_date = KospexUtils.days_ago_iso_date(days)
        devs = set()
        summary_sql = """SELECT distinct(author_email)
        FROM commits
        WHERE committer_when > ?
        """
        data = self.kospex_db.query(summary_sql, (from_date,))
        for row in data:
            devs.add(row['author_email'])

        return set(devs)

    def get_collabs(self,repo_id=None):
        """
        Get the author committer states dependencies for the given repo_id.
        """

        kd = KospexData(self.kospex_db)
        kd.from_table(KospexSchema.TBL_COMMITS)
        #kd.select_raw("DISTINCT(author_email) as author_email")
        #kd.select_raw("DISTINCT(committer_email) as committer_email")
        kd.select("author_email")
        kd.select("committer_email")
        kd.select_as("count(*)",'commits')
        kd.where("_repo_id","=",repo_id)
        kd.group_by("author_email")
        kd.group_by("committer_email")
        results = kd.execute()

        return results

    def get_activity_stats(self,params=None):
        """
        Return stats about all, server, org or repos
            first_commit,
            last_commit,
            days_active,
            years_active,
            authors (distinct author_email),
            committers (distinct committer_email),
            commits

        """
        print()
        kd = KospexData(self.kospex_db)
        kd.from_table(KospexSchema.TBL_COMMITS)
        kd.select_as("count(*)",'commits')
        kd.select_as("MIN(committer_when)", "first_commit")
        kd.select_as("MAX(committer_when)", "last_commit")
        kd.select_raw("COUNT(DISTINCT(_repo_id)) as repos")
        kd.select_raw("COUNT(DISTINCT(LOWER(author_email))) as authors")

        if params:
            kd.set_params_by_id(params)

        kresults = kd.execute()

        # Process the day calculations

        data = {}

        if kresults:
            data = kresults[0]
            data['days_active'] = KospexUtils.days_between_datetimes(
                data['first_commit'], data['last_commit'],min_one=True)
            data['years_active'] = round(data['days_active'] / 365,2)

        return data

    @KospexUtils.timer()
    def authors(self, days=None, org_key=None):
        """
        Provide a summary of authors in the known repositories.
        days: Number of days ago to query from, e.g. 90 is last 90 days
        org_key: Organization key to filter by (e.g. github.com~kospex)
        """

        # Used to hold the date value calculated from days
        from_date = None

        kd = KospexData(self.kospex_db)
        kd.from_table(KospexSchema.TBL_COMMITS)
        kd.select_raw("DISTINCT(LOWER(author_email)) as author_email")
        kd.select_as("count(*)",'commits')
        kd.select_raw("COUNT(DISTINCT(_repo_id)) as repos")
        kd.select_as("MIN(committer_when)", "first_commit")
        kd.select_as("MAX(committer_when)", "last_commit")
        #kd.group_by("author_email",lower=True)
        kd.group_by("author_email")

        if days:
            from_date = KospexUtils.days_ago_iso_date(days)
            kd.where("committer_when", ">", from_date)

        if org_key:
            kd.where_org_key(org_key)

        kresults = kd.execute()
        for row in kresults:
            row['last_seen'] = KospexUtils.days_ago(row['last_commit'])

        return kresults

    def active_devs_by_repo(self, repo_id, days=90):
        """ Look for distinct developers in the last X 'days' """
        from_date = KospexUtils.days_ago_iso_date(days)
        summary_sql = """SELECT distinct(LOWER(author_email)) AS 'author_email', count(*) AS 'commits',
        MAX(committer_when) AS 'last_commit', count(distinct(_repo_id)) AS 'repos'
        FROM commits
        WHERE committer_when > ? AND _repo_id = ?
        GROUP BY LOWER(author_email)
        ORDER BY commits DESC
        """
        results = []
        data = self.kospex_db.query(summary_sql, (from_date, repo_id))
        for row in data:
            row['days_ago'] = KospexUtils.days_ago(row['last_commit'])
            row['last_seen'] = row['days_ago']
            results.append(row)
        #return data[0]['devs']
        return results

    def authors_by_repo(self, repo_id):
        """ Provide a summary of authors in the provided repo."""
        summary_sql = """SELECT LOWER(author_email) as author_email, count(*) 'commits', MIN(author_when) 'first_commit',
        MAX(author_when) 'last_commit'
        FROM commits
        WHERE _repo_id = ?
        GROUP BY LOWER(author_email)
        ORDER BY commits DESC
        """
        data = self.kospex_db.query(summary_sql, [repo_id])
        results = {}

        for row in data:
            if row['last_commit']:
                row['days_ago'] = KospexUtils.days_ago(row['last_commit'])
            #row['days_ago'] = KospexUtils.days_ago(row['last_commit'])
            results[row['author_email']] = row

        return results

    def commit_ranges2(self, repo_id=None, org_key=None):
        """ Get the range of commits for a repo """

        kd = KospexData(self.kospex_db)
        kd.from_table(KospexSchema.TBL_COMMITS)
        kd.select_as("DISTINCT(_repo_id)","repo")
        kd.select_as("MAX(committer_when)", "last_commit")
        kd.select_as("MIN(committer_when)", "first_commit")
        kd.group_by("_repo_id")

        if repo_id:
            kd.where("_repo_id", "=", repo_id)

        if org_key:
            kd.where_org_key(org_key)

        # if org_key:
        #     parts = org_key.split('~')
        #     if len(parts) != 2:
        #         print("org_key must be of the form <server>~<owner>")
        #         return None
        #     kd.where("_git_server", "=", parts[0])
        #     kd.where("_git_owner", "=", parts[1])

        results =  kd.execute()

        ages = {
            'Active': 0,
            'Aging': 0,
            'Stale': 0,
            'Unmaintained': 0
        }

        for i in results:
            i['status'] = KospexUtils.development_status(i['last_commit'])
            i['days_active'] = KospexUtils.days_between_datetimes(
                i['first_commit'], i['last_commit'],min_one=True)
            ages[i['status']] += 1

        return ages

#        return results

    def commit_ranges(self, repo_id=None, org_key=None):
        """ Get the range of commits for a repo """
        where_clause = ""

        # this boolean lets us know if we have set a where yet
        where = False
        params = []

        if repo_id:
            where_clause = "WHERE _repo_id = ?"
            where = True
            params.append(repo_id)

        if org_key:
            parts = org_key.split('~')
            if len(parts) != 2:
                print("org_key must be of the form <server>~<owner>")
                return None
            params.append(parts[0])
            params.append(parts[1])

            if where:
                where_clause += " AND _git_server = ? AND _git_owner = ?"
            else:
                where = True
                where_clause = "WHERE _git_server = ? AND _git_owner = ?"

            #where_clause += "AND _git_server = ? AND _git_owner = ?"

        sql = f"""
        WITH DateCategories AS (
        SELECT
            CASE
                WHEN julianday(committer_when) >= julianday('now') - 90 THEN 'active'
                WHEN julianday(committer_when) >= julianday('now') - 180 AND
                julianday(committer_when) < julianday('now') - 90 THEN 'aging'
                WHEN julianday(committer_when) >= julianday('now') - 365 AND
                julianday(committer_when) < julianday('now') - 180 THEN 'stale'
                ELSE 'unmaintained'
            END AS date_category
        FROM commits
        {where_clause}
        )

        SELECT date_category, COUNT(*) as row_count
        FROM DateCategories
        GROUP BY date_category;"""

        ages = {
            'active': 0,
            'aging': 0,
            'stale': 0,
            'unmaintained': 0
        }

        data = self.kospex_db.query(sql, params)
        for row in data:
            ages[row['date_category']] = row['row_count']
        return ages

    def author_summary(self, repo_id):
        """ Provide a summary of authors for repositories."""
        summary_sql = """SELECT author_email, count(*) 'commits', MIN(author_when) 'first_commit',
        MAX(author_when) 'last_commit'
        FROM commits
        WHERE _repo_id = ?
        GROUP BY author_email
        ORDER BY commits DESC
        """
        data = self.kospex_db.query(summary_sql, [repo_id])
        results = []

        for row in data:
            if row['last_commit']:
                row['days_ago'] = KospexUtils.days_ago(row['last_commit'])
            #row['days_ago'] = KospexUtils.days_ago(row['last_commit'])
            results.append(row)

        return results

    def email_domains(self, repo_id=None):
        """ Provide a summary of email domains for repositories."""
        where_clause = ""
        params = []
        if repo_id:
            where_clause = " WHERE _repo_id = ? "
            params.append(repo_id)

        summary_sql = f"""SELECT substr(author_email, instr(author_email, '@') + 1) as domain,
        COUNT(DISTINCT author_email) 'addresses'
        FROM commits
        {where_clause}
        GROUP BY domain
        ORDER BY addresses DESC
        """
        data = self.kospex_db.query(summary_sql, params)
        results = []

        for row in data:
            results.append(row)

        return results

    def file_metadata(self, repo_id):
        """ Provide a summary of file metadata for repositories."""
        params = []
        params.append(repo_id)

        sql = """SELECT Provider, filename, Lines, Complexity, Language
        FROM file_metadata
        WHERE _repo_id = ? AND latest = 1
        """
        metadata = {}
        data = self.kospex_db.query(sql, params)
        for row in data:
            metadata[row['Provider']] = row

        return metadata

    def hotspots(self, repo_id):
        """ Provide hotspot analysis for a repo."""
        params = []
        params.append(repo_id)

        # We're going to need a few lookups
        # Potentially we could do this in a nasty join, but it's easier to read this way

        # We need the Lines of code per file as a proxy for complexity (scc gives us complexity too)
        # We'll get this as a dict so we can look up the file_path which will be Location in scc
        metadata = self.file_metadata(repo_id)

        # we need the number of distinct authors per file as well as the number of commits per file
        sql = """SELECT DISTINCT(file_path) as file_path, count(*) 'commits',
        COUNT(DISTINCT(c.author_email)) 'authors'
        FROM commit_files cf, commits c
        WHERE cf._repo_id = ? AND cf._repo_id = c._repo_id AND cf.hash = c.hash
        GROUP BY file_path
        ORDER BY commits DESC
        """
        data = self.kospex_db.query(sql, params)
        results = []

        for row in data:
            meta = metadata.get(row['file_path'], {})
            if meta:
                print(row['file_path'])
                row['Lines'] = meta.get('Lines',0)
                row['Complexity'] = meta.get('Complexity',0)
                row['Language'] = meta.get('Language',0)
                print(meta)
            else:
                print("No meta for ", row['file_path'])
            #row['lines'] = metadata[row['file_path']]['Lines']
            results.append(row)

        return results

    def url_request(self, url, cache=3600, timeout=10, headers=None):
        """ Make a request to a URL, and use the cached version is less than [cache] seconds"""
        # Set default cache to 1 hour (60mins * 60secs)

        # Check the cache first
        cache_sql = f'''SELECT content, timestamp FROM {KospexSchema.TBL_URL_CACHE} WHERE url = ?'''
        data = self.kospex_db.query(cache_sql, (url,))
        result = next(data, None)

        if result and (time.time() - result['timestamp']) < cache:
            # Cache is valid
            content = result['content']
        else:
            # Fetch new content and update the cache
            try:
                response = requests.get(url, timeout=timeout)
                response.raise_for_status()  # Raise an exception for HTTP errors
                content = response.text

                # Insert or update the cache
                url_cache = {'url': url, 'content': content, 'timestamp': int(time.time())}
                self.kospex_db.table(KospexSchema.TBL_URL_CACHE).upsert(url_cache, pk=['url'])

            except requests.RequestException as e:
                print(f"Error fetching URL content: {e}")
                content = None

        return content

    def get_repo_ids(self):
        """return a list of repo_ids"""

        sql = """SELECT DISTINCT(_repo_id)
        FROM commits c
        """
        data = self.kospex_db.query(sql, [])
        results = []
        for row in data:
            results.append(row['_repo_id'])

        return results

    def author_tech(self, author_email=None, repo_id=None):
        """ Return the tech stack for an author """

        results = []
        params = []

        kd = KospexData(self.kospex_db)
        kd.from_table("commit_files", "commits")

        kd.select_as("DISTINCT(author_email)", "author_email")
        kd.select("_ext")
        kd.select_as("count(*)", "commits")
        kd.select_as("MAX(author_when)", "last_commit")
        kd.select_as("MIN(author_when)", "first_commit")
        # TODO - fix parsing of multiple SQL functions
        kd.select_raw("COUNT(DISTINCT(commits._repo_id)) as repos")

        kd.where_join("commits", "hash", "commit_files", "hash")
        kd.where_join("commits", "_repo_id", "commit_files", "_repo_id")
        #kd.where_join("commit_files", "file_path", "file_metadata", "Provider")
        #kd.where_join("commit_files", "_repo_id", "file_metadata", "_repo_id")
        #kd.where_join("commit_files", "hash", "file_metadata", "hash")

        kd.group_by("author_email","_ext")
        kd.order_by("commits", "DESC")

        author_where = ""
        if author_email:
            params.append(author_email)
            author_where = "AND c.author_email = ?"
            kd.where("author_email", "=", author_email)

        repo_where = ""
        if repo_id:
            params.append(repo_id)
            repo_where = "AND c._repo_id = ?"
            kd.where("commits._repo_id", "=", repo_id)

        # We're going to need a few lookups
        # Potentially we could do this in a nasty join, but it's easier to read this way

        # we need the number of distinct authors per file as well as the number of commits per file
        sql = f"""SELECT DISTINCT(author_email) as author_email, _ext, count(*) 'commits',
        MAX(author_when) 'last_commit', MIN(author_when) 'first_commit',
        COUNT(DISTINCT(c._repo_id)) 'repos'
        FROM commit_files cf, commits c
        WHERE cf._repo_id = c._repo_id
        AND cf.hash = c.hash {author_where} {repo_where}
        GROUP BY author_email, _ext
        ORDER BY commits DESC
        """
        #print(sql)

        #data = self.kospex_db.query(sql, params)
        data = self.kospex_db.query(kd.generate_sql(), kd.params)
        for row in data:
            row['last_seen'] = KospexUtils.days_ago(row['last_commit'])
            row['first_seen'] = KospexUtils.days_ago(row['first_commit'])
            row['days_active'] = int(row.get('first_seen',0)) - int(row.get('last_seen',0))
            #row['years_active'] = round(row['days_active'] / 365,2)
            row['years_active'] = f"{row['days_active'] / 365:.3f}"
            results.append(row)

        return results

    # def summarise_dev_commits(self, commits):
    #     """
    #     Summarize commits by author_email and language.

    #     Args:
    #         commits: List of dicts with commit information

    #     Returns:
    #         List of dicts with summarized information
    #     """
    #     # Group by (author_email, language)
    #     grouped = defaultdict(lambda: {
    #         'commits': [],
    #         'repo_ids': set()
    #     })

    #     for commit in commits:
    #         key = (commit['author_email'], commit['language'])
    #         grouped[key]['commits'].append(commit['committer_when'])
    #         if '_repo_id' in commit:
    #             grouped[key]['repo_ids'].add(commit['_repo_id'])

    #     # Build summary list
    #     summary = []
    #     for (author_email, language), data in grouped.items():
    #         # Sort commits to get first and last
    #         commit_dates = sorted(data['commits'])

    #         summary.append({
    #             'author_email': author_email,
    #             'first_commit': commit_dates[0],
    #             'last_commit': commit_dates[-1],
    #             'language': language,
    #             'commits': len(commit_dates),
    #             'repos': len(data['repo_ids'])
    #         })

    #     # Sort by author_email, then language for consistent output
    #     summary.sort(key=lambda x: (x['author_email'], x['language']))

    #     return summary

    def summarise_dev_commits(self, commits):
        """
           Summarize commits by author_email and language.

           Args:
               commits: List of dicts with commit information

           Returns:
               List of dicts with summarized information
           """
        # Group by (author_email, language)
        grouped = defaultdict(lambda: {
            'commits': [],
            'repo_ids': set(),
            'file_paths': set()
        })

        for commit in commits:
            key = (commit['author_email'], commit['language'])
            grouped[key]['commits'].append(commit['committer_when'])
            if '_repo_id' in commit:
                grouped[key]['repo_ids'].add(commit['_repo_id'])
            if 'file_path' in commit:
                grouped[key]['file_paths'].add(commit['file_path'])

        # Build summary list
        summary = []
        for (author_email, language), data in grouped.items():
            # Sort commits to get first and last
            commit_dates = sorted(data['commits'])

            years_active = 0

            last_seen = KospexUtils.days_ago(commit_dates[-1])
            first_seen = KospexUtils.days_ago(commit_dates[0])
            if last_seen is not None and first_seen is not None:
                days_active = first_seen - last_seen
            else:
                days_active = None

            if days_active:
                years_active = f"{days_active / 365:.3f}"

            first_commit = commit_dates[0]
            last_commit = commit_dates[-1]
            if "T" in first_commit:
                first_commit = first_commit.split('T')[0]
            if "T" in last_commit:
                last_commit = last_commit.split('T')[0]

            summary.append({
                'author_email': author_email,
                'first_commit': first_commit,
                'last_commit': last_commit,
                'language': language,
                'commits': len(commit_dates),
                'repos': len(data['repo_ids']),
                'files': len(data['file_paths']),
                'years_active': years_active
            })

        # Sort by author_email, then language for consistent output
        summary.sort(key=lambda x: (x['author_email'], x['language']))

        return summary

    @KospexUtils.timer()
    def summarize_by_language(self, commits):
        """
        Summarize commits by language only (across all authors).

        Args:
            commits: List of dicts with commit information

        Returns:
            List of dicts with summarized information by language
        """
        # Group by language only
        grouped = defaultdict(lambda: {
            'commits': [],
            'repo_ids': set(),
            'file_paths': set()
        })

        for commit in commits:
            language = commit['language']
            grouped[language]['commits'].append(commit['committer_when'])
            if '_repo_id' in commit:
                grouped[language]['repo_ids'].add(commit['_repo_id'])
            if 'file_path' in commit:
                grouped[language]['file_paths'].add(commit['file_path'])

        # Build summary list
        summary = []
        for language, data in grouped.items():
            # Sort commits to get first and last
            commit_dates = sorted(data['commits'])
            years_active = 0

            last_seen = KospexUtils.days_ago(commit_dates[-1])
            first_seen = KospexUtils.days_ago(commit_dates[0])
            if last_seen is not None and first_seen is not None:
                days_active = first_seen - last_seen
            else:
                days_active = None

            if days_active:
                years_active = f"{days_active / 365:.3f}"

            first_commit = commit_dates[0]
            last_commit = commit_dates[-1]
            if "T" in first_commit:
                first_commit = first_commit.split('T')[0]
            if "T" in last_commit:
                last_commit = last_commit.split('T')[0]


            summary.append({
                'language': language,
                'first_commit': first_commit,
                'last_commit': last_commit,
                'commits': len(commit_dates),
                'repos': len(data['repo_ids']),
                'files': len(data['file_paths']),
                'years_active': years_active
            })

        # Sort by language for consistent output
        summary.sort(key=lambda x: x['language'])

        return summary


    def get_file_authors(self, file_name=None, repo_id=None):
        """
        Return authors for a given file_name and repo_id
        """
        results = []
        kd = KospexData(self.kospex_db)
        kd.from_table("commit_files", "commits")

        kd.select_as("DISTINCT(author_email)", "author_email")
        kd.select("file_path")
        kd.select_as("count(*)", "commits")
        kd.select_as("MAX(commit_files.committer_when)", "committer_when")
        kd.where("commits._repo_id","=",repo_id)
        kd.where("file_path","=",file_name)

        kd.where_join("commits", "hash", "commit_files", "hash")

        kd.group_by("author_email","file_path")
        kd.order_by("committer_when", "DESC")

        data = self.kospex_db.query(kd.generate_sql(), kd.params)
        for row in data:
            row['_repo_id'] = repo_id
            results.append(row)

        return results

    def developer_tech(self, author_email=None, repo_id=None, developers=None):
        """
        Return the tech stack for an author or repo
        This function is part of
        https://github.com/kospex/kospex/issues/63
        """

        results = []
        params = []

        kd = KospexData(self.kospex_db)
        kd.from_table("commit_files", "commits")

        #kd.select_raw("DISTINCT(LOWER(author_email)) as author_email")
        kd.select_raw("LOWER(author_email) as author_email")
        #kd.select_as("DISTINCT(author_email)", "author_email")
        kd.select("_ext", "file_path", "commits.committer_when", "commits._repo_id")
        #kd.select_as("count(*)", "commits")

        # We need to do the following on the language when we've extracted it
        #kd.select_as("MAX(author_when)", "last_commit")
        #kd.select_as("MIN(author_when)", "first_commit")

        # TODO - fix parsing of multiple SQL functions
        #kd.select_raw("COUNT(DISTINCT(commits._repo_id)) as repos")

        kd.where_join("commits", "hash", "commit_files", "hash")
        #kd.where_join("commits", "_repo_id", "commit_files", "_repo_id")
        #kd.where_join("commit_files", "file_path", "file_metadata", "Provider")
        #kd.where_join("commit_files", "_repo_id", "file_metadata", "_repo_id")
        #kd.where_join("commit_files", "hash", "file_metadata", "hash")

        #kd.group_by("author_email")
        #kd.group_by("author_email","_ext")
        #kd.order_by("commits", "DESC")

        if author_email:
            params.append(author_email)
            kd.where("author_email", "=", author_email)

        if repo_id:
            params.append(repo_id)
            kd.where("commits._repo_id", "=", repo_id)

        # We're going to need a few lookups
        # Potentially we could do this in a nasty join, but it's easier to read this way

        data = self.kospex_db.query(kd.generate_sql(), kd.params)
        for row in data:
        #     row['last_seen'] = KospexUtils.days_ago(row['last_commit'])
        #     row['first_seen'] = KospexUtils.days_ago(row['first_commit'])
        #     row['days_active'] = int(row.get('first_seen',0)) - int(row.get('last_seen',0))
        #     row['years_active'] = f"{row['days_active'] / 365:.3f}"
            row['language'] = Panopticas.get_language(row['file_path'], skip_shebang=True)
            results.append(row)

        # summarise the results
        #summary = self.summarise_dev_commits2(results)
        summary = []
        if developers or author_email:
            summary = self.summarise_dev_commits(results)
        else:
            summary = self.summarize_by_language(results)

        #return results
        return summary


    def tech_commits(self, author_email=None, repo_id=None):
        """" Return a KospexData object with tables joined"""
        kd = KospexData()
        kd.from_table("commits", "commit_files")
        kd.select("_ext")
        kd.select_as("COUNT(*)", "commits")
        # TODO - handle author_email and repo_id in the where clause
        if author_email:
            kd.where("author_email", "=", author_email)
        kd.where_join("commits", "_repo_id", "commit_files", "_repo_id")
        kd.where_join("commits", "hash", "commit_files", "hash")
        kd.group_by("_ext")
        kd.order_by("commits", "DESC")
        return kd

    def observations_summary(self, repo_id=None, observation_key=None):
        """ Return a summary of # observations per repo """
        sql = """SELECT _repo_id, count(*) as observations
        FROM observations
        GROUP BY _repo_id
        ORDER BY observations DESC
        """
        kd = KospexData(kospex_db=self.kospex_db)
        kd.from_table(KospexSchema.TBL_OBSERVATIONS)
        kd.select("_repo_id")
        kd.select_as("COUNT(*)", "observations")
        kd.group_by("_repo_id")
        kd.order_by("observations", "DESC")

        if repo_id:
            kd.where("_repo_id", "=", repo_id)
            kd.select("observation_key")
            kd.group_by("observation_key")

        if observation_key:
            kd.where("observation_key", "=", observation_key)
            kd.select("file_path")
            kd.group_by("file_path")

        data = kd.execute()
        print(data)
        return data

        #return kd.execute()

    def get_repos(self,**kwargs):
        """
        Return a list of repos.
        Parameters:
            server: git server
            email: author email

        The parameters are "ands" and not "ors"
        which means that if both are provided, the query will return
        repos that match both criteria.
        """
        kd = KospexData(kospex_db=self.kospex_db)
        kd.from_table(KospexSchema.TBL_REPOS)
        kd.select("*")

        kd.set_params_by_id(kwargs)

        if server := kwargs.get("server"):
            kd.where("_git_server", "=", server)

        repos = kd.execute()

        if email := kwargs.get("email"):
            # We have to do a query on the commits table
            # to get the repos for a given email
            kd_email = KospexData(kospex_db=self.kospex_db)
            kd_email.from_table(KospexSchema.TBL_COMMITS)
            kd_email.select_as("DISTINCT(_repo_id)", "_repo_id")
            kd_email.where("author_email", "=", email)
            author_repos = kd_email.execute()
            author_set = set([r['_repo_id'] for r in author_repos])
            author_results = []
            for repo in repos:
                if repo['_repo_id'] in author_set:
                    author_results.append(repo)
            repos = author_results

        for repo in repos:
            repo['years_active'] = "Unknown"
            #print(f"Repo ID: {repo['_repo_id']}, First Seen: {repo['first_seen']}, Last Seen: {repo['last_seen']}")
            if repo['first_seen'] and repo['last_seen']:
                if "\n" in repo['first_seen']:
                    #print("First seen date is invalid")
                    dates = repo['first_seen'].split("\n")
                    repo['first_seen'] = dates[0]

                if "\n" in repo['last_seen']:
                    #print("Last seen date is invalid")
                    dates = repo['last_seen'].split("\n")
                    repo['last_seen'] = dates[0]

                days = KospexUtils.days_between_datetimes(repo['first_seen'], repo['last_seen'])
                #print(f"Repo ID: {repo['_repo_id']}, Days Active: {days}")

                repo['days_active'] = days
                if days > 0:
                    repo['years_active'] = f"{days / 365:.2f}"
                else:
                    repo['years_active'] = 0
                ##self.get_repo_last_sync(repo['_repo_id'])

        return repos

    def get_repos_last_sync(self, older_than=None, newer_than=None):
        """
        Get repos that were last synced before or after a given date.
        """
        kd = KospexData(kospex_db=self.kospex_db)
        kd.from_table(KospexSchema.TBL_REPOS)
        kd.select("*")
        if older_than:
            kd.where("last_sync", "<", older_than)
        if newer_than:
            kd.where("last_sync", ">", newer_than)
        if older_than and newer_than:
            raise ValueError("Cannot specify both older_than and newer_than")

        return kd.execute()

    def get_observations(self, repo_id=None, observation_key=None):
        """ Return a list of observations for a repo_id and observation_key """
        kd = KospexData(kospex_db=self.kospex_db)
        kd.from_table(KospexSchema.TBL_OBSERVATIONS)
        kd.select("*")
        if repo_id:
            kd.where("_repo_id", "=", repo_id)
        if observation_key:
            kd.where("observation_key", "=", observation_key)
        kd.where("latest", "=", 1)

        return kd.execute()

    def get_single_observation(self, repo_id=None, observation_key=None, hash=None,
    file_path = None, uuid=None):
        """ Return a single observation for a repo_id and observation_key """
        kd = KospexData(kospex_db=self.kospex_db)
        kd.from_table(KospexSchema.TBL_OBSERVATIONS)
        kd.select("*")
        if repo_id:
            kd.where("_repo_id", "=", repo_id)
        if observation_key:
            kd.where("observation_key", "=", observation_key)
        if hash:
            kd.where("hash", "=", hash)
        if file_path:
            kd.where("file_path", "=", file_path)
        if uuid:
            kd.where("uuid", "=", uuid)

        kd.order_by("created_at", "desc")
        kd.limit(1)

        return kd.execute()

    def add_observation(self, observation):
        """
        Add an observations to the database
        observation is a dict with a minimum of keys _repo_id, hash, file_path, observation_key
        The other fields are decribed in the Observation class
        which is based on kospex_schema.py table definition in TBL_OBSERVATIONS
        We'll need to set older observations to be latest = 0
        New observations should be latest = 1
        """

        # Reset "last" flags to false
        reset_last_sql = f"""UPDATE {KospexSchema.TBL_OBSERVATIONS} SET LATEST = 0
        WHERE _repo_id = ? AND hash = ? AND file_path = ? AND observation_key = ? and latest = 1"""
        self.kospex_db.execute(reset_last_sql, [ observation['_repo_id'], observation['hash'],
                                observation['file_path'], observation['observation_key']])

        self.kospex_db.table(
            KospexSchema.TBL_OBSERVATIONS).insert(
                observation,pk=["_repo_id","hash","file_path","observation_key", "latest"])
        #self.kospex_db.table(KospexSchema.TBL_OBSERVATIONS).insert_all(observations)

# TODO - this is copilot generated code, needs refactoring to a kdata object
#    def get_observations_summary(self, repo_id=None, observation_key=None):
#        """ Return a list of observations for a repo_id and observation_key """
#        sql = f"""SELECT observation_key, count(*) as count FROM {KospexSchema.TBL_OBSERVATIONS} WHERE _repo_id = ? GROUP BY observation_key"""
#        params = [repo_id]
#        data = self.kospex_db.query(sql, params)
#        results = []
#        for row in data:
#            results.append(row)
#        return results


    def get_observations_summary(self, repo_id=None, observation_key=None):
        """ Return a list of observations for a repo_id and observation_key """
        sql = f"""SELECT observation_key, count(*) as count FROM {KospexSchema.TBL_OBSERVATIONS} WHERE _repo_id = ? GROUP BY observation_key"""
        params = [repo_id]
        data = self.kospex_db.query(sql, params)
        results = []
        for row in data:
            results.append(row)
        return results


    def git_servers(self):
        """ Return a list of git servers """
        sql = f"""SELECT DISTINCT(server) FROM {KospexSchema.TBL_REPOS}"""
        data = self.kospex_db.query(sql)
        results = []
        for row in data:
            results.append(row['server'])
        return results

    def commit_stats(self, days=None, repo_id=None):
        """ Return stats about commits. """
        kd = KospexData(kospex_db=self.kospex_db)
        kd.from_table(KospexSchema.TBL_COMMITS)
        kd.select_as("DISTINCT(author_email)", "author")
        kd.select_as("MIN(author_when)",'first_commit')
        kd.select_as("MAX(author_when)", 'last_commit')
        kd.select_as("COUNT(*)", "commits")
        kd.group_by("author")
        kd.order_by("commits", "DESC")

        if repo_id:
            kd.where("_repo_id", "=", repo_id)

        if days:
            from_date = KospexUtils.days_ago_iso_date(days)
            kd.where("committer_when", ">", from_date)

        return kd.execute()

    def key_person(self, days=None, repo_id=None,top=None):
        """
        Return a high level key person based on commits
        Top is the number of "top" authors to return
        Days is the number of preceding days to consider "active"for the key person default is 90 days
        """

        active = self.commit_stats(repo_id=repo_id,days=90)
        active_dict = {d["author"]: d for d in active}
        key = "commits"
        total_active_commits = sum(item[key] for item in active if key in item)

        authors = self.commit_stats(repo_id=repo_id)
        key = "commits"
        total_commits = sum(item[key] for item in authors if key in item)

        for a in authors:
            a['active_commits'] = active_dict.get(a['author'],{}).get('commits',0)
            a['% commits'] = f"{a['commits'] / total_commits * 100:.1f}%"

            # TODO - We only want to do this if we're printing the table, modify if we're dumping to CSV
            a['last_commit'] = KospexUtils.extract_db_date(a['last_commit'])
            a['first_commit'] = KospexUtils.extract_db_date(a['first_commit'])

            if total_active_commits > 0:
                a['% active'] = f"{a['active_commits'] / total_active_commits * 100:.1f}%"
            else:
                a['% active'] = "0%"

            days = KospexUtils.days_between_datetimes(a['first_commit'], a['last_commit'],min_one=True)
            tenure = f"{days / 354:.3f}"

            a['tenure'] = tenure

        authors_dict = {d["author"]: d for d in authors}

        if top is None:
            # If we haven't set a top X
            # Then we'll return all authors and their active commits
            return authors

        else:

            top_list = authors[:top]
            top_authors_dict = {d["author"]: d for d in authors[:top]}

            for a in active[:top]:
                a_dict = top_authors_dict.get(a['author'])
                if not a_dict:
                    a_dict = authors_dict[a['author']]
                    top_list.append(a_dict)

            return top_list

    def groups(self, params=None):
        """ Return a list of groups """
        kd = KospexData(kospex_db=self.kospex_db)
        kd.from_table(KospexSchema.TBL_GROUPS)

        if not params:
            kd.select_as("DISTINCT(group_name)", "name")
        elif params.get("delete"):
            kd.delete()
            kd.where("group_name", "=", params.get("name"))
        elif params.get("show"):
            kd.select("*")
            kd.where("group_name", "=", params.get("name"))
        else:
            exit("Not implemented")

        return kd.execute()

    def get_repo_by_id(self, repo_id):
        """ Return a repo by id """
        sql = f"""SELECT * FROM {KospexSchema.TBL_REPOS} WHERE _repo_id = ?"""
        data = self.kospex_db.query(sql, [repo_id])
        return next(data, None)

    def get_repo_id_lookup(self):
        """
        Return a repo_id lookup
        """
        sql = f"""SELECT * FROM {KospexSchema.TBL_REPOS}"""
        data = self.kospex_db.query(sql)
        results = {}
        for row in data:
            #print(row)
            results[row['_repo_id']] = row
        return results

    # TODO - refactor to one repos query using KospexData
    #def repos(self,  **kwargs):
    #    """ Return a list of repos """
    #    kd = KospexData(kospex_db=self.kospex_db)
    #    kd.from_table(KospexSchema.TBL_REPOS)
    #    kd.select("*")
    #    return kd.execute()

    def get_graph_info(self, org_key=None, author_email=None,
        repo_id=None, git_server=None, by_repo=None):
        """
        Return a list of repos and developers for graphing
        If by_repo is True, group by repo and NOT the author
        """
        params = locals()

        org = None
        server = None

        if org_key:
            org = org_key.split('~')[1]
            server = org_key.split('~')[0]

        kd = KospexData(kospex_db=self.kospex_db)
        kd.from_table(KospexSchema.TBL_COMMITS)
        if by_repo:
            kd.select("_repo_id")
            kd.select_as("LOWER(author_email)", "author")
        else:
            #kd.select_as("DISTINCT(author_email)", "author")
            kd.select_raw("DISTINCT(LOWER(author_email)) as author")
            kd.select("_repo_id")

        kd.select_as("COUNT(*)", "commits")
        kd.select_as("MAX(committer_when)", "last_commit")
        #kd.select_as("_git_repo", "repo")
        kd.select("_git_repo")

        if org_key:
            kd.where("_git_owner", "=", org)
            kd.where("_git_server", "=", server)

        if git_server:
            kd.where("_git_server", "=", git_server)

        if author_email:
            kd.where("author_email", "=", author_email)

        if repo_id:
            kd.where("_repo_id", "=", repo_id)

        if by_repo:
            kd.group_by("_repo_id")
        else:
            kd.group_by("author","_repo_id")

        return kd.execute()

    def get_repo_files_graph_info(self, repo_id):
        """
        Return a list of repos and developers for graphing
        """

        kd = KospexData(kospex_db=self.kospex_db)
        kd.from_table(KospexSchema.TBL_COMMITS)
        kd.from_table(KospexSchema.TBL_COMMIT_FILES)
        kd.select_as("DISTINCT(author_email)", "author")
        kd.select("file_path")
        kd.select_as("COUNT(*)", "commits")
        kd.where("commits._repo_id", "=", repo_id)
        kd.where_join(KospexSchema.TBL_COMMITS, "_repo_id", KospexSchema.TBL_COMMIT_FILES, "_repo_id")
        kd.where_join(KospexSchema.TBL_COMMITS, "hash", KospexSchema.TBL_COMMIT_FILES, "hash")
        kd.group_by("author","file_path")

        return kd.execute()

    def get_metadata_files(self, filename=None, tag=None,repo_id=None):
        """
        Return a list of active metadata files by filename
        and not the full path
        """

        kd = KospexData(kospex_db=self.kospex_db)
        kd.from_table(KospexSchema.TBL_FILE_METADATA)
        kd.select("*")
        kd.where("latest", "=", 1)

        if filename:
            kd.where("Filename", "=", filename)

        if tag:
            kd.where_tag(tag)

        print("Executing query:", kd.generate_sql(),kd.params)

        return kd.execute()

    # Commenting out, looks like this was a half attempt to refactor some of the graph API
    # def create_nodes(self, raw_data):
    #     """
    #     Process the details from get_graph_info
    #     return a data structure of
    #     data = {
    #         "nodes": [],
    #         "links": []
    #     }
    #     suitable for forced directed and bubble graphs
    #     """

    #     dev_lookup = {}
    #     repo_lookup = {}
    #     file_lookup = {}
    #     links = []
    #     nodes = []

    #     for element in raw_data:

    #         last_commit = element.get("last_commit")
    #         status = KospexUtils.development_status(KospexUtils.days_ago(last_commit))

    #         group_numbers = {}
    #         group_numbers['Active'] = 1
    #         group_numbers['Aging'] = 2
    #         group_numbers['Stale'] = 3
    #         group_numbers['Unmaintained'] = 4

    #         group = 1
    #         if org_key:
    #             # we only have 1 group, and that's developers
    #             group = 1
    #             # in graph, group is used to link between
    #         else:
    #             group = group_numbers.get(status,4)

    #         b64_email = KospexUtils.encode_base64(element.get('author'))

    #         #b64_bytes = base64.b64encode(element['author'].encode("utf-8"))
    #         #b64_email = b64_bytes.decode('utf-8')

    #         print(b64_email)

    #         if element['author'] not in dev_lookup:
    #             dev_lookup[element['author']] = { "id": element['author'],
    #                                              "id_b64": b64_email,
    #                                              "group": group,
    #                                              "label": KospexUtils.extract_github_username(element['author']),
    #                                              "info": element['author'],
    #                                              "commits": element.get("commits"),
    #                                              "status_group": group_numbers.get(status,4),
    #                                              "status": status,
    #                                              "last_commit": last_commit,
    #                                              "repos": 1 }
    #         else:
    #             dev_lookup[element['author']]['repos'] += 1


    #         if repo_id and not focus:
    #             # We're handling files not repos
    #             file_path = element.get('file_path')
    #             if element.get('file_path') not in file_lookup:
    #                 file_lookup[element['file_path']] = { "id": element['file_path'],
    #                                                 "group": 2,
    #                                                 "label": basename(element['file_path']),
    #                                                 "info": element['file_path'] }

    #         elif element['_repo_id'] not in repo_lookup:
    #             repo_lookup[element['_repo_id']] = { "id": element['_repo_id'],
    #                                                 "group": 2,
    #                                                 "commits": element.get("commits",0),
    #                                                 "status_group": group_numbers.get(status,4),
    #                                                 "status": status,
    #                                                 "last_commit": last_commit,
    #                                                 "label": element['_git_repo'],
    #                                                 "info": element['_repo_id'] }

    #         link_key = "_repo_id"

    #         if repo_id:
    #             link_key = "file_path"

    #         links.append({"source": element['author'],
    #                       "target": element.get(link_key),
    #                       "commits": element['commits']})

    #     for element in dev_lookup:
    #         nodes.append(dev_lookup[element])

    #     for element in repo_lookup:
    #         nodes.append(repo_lookup[element])

    #     for element in file_lookup:
    #         nodes.append(file_lookup[element])

    #     data = {
    #             "nodes": [
    #                 { "id": "Dev1", "group": 1, "info": "Developer 1 info" },
    #                 { "id": "Dev2", "group": 1, "info": "Developer 2 info" },
    #                 { "id": "Repo1", "group": 2, "info": "Repository 1 info" },
    #                 { "id": "Repo2", "group": 2, "info": "Repository 2 info" },
    #                 { "id": "Repo3", "group": 2, "info": "Repository 3 info" }
    #             ],
    #             "links": [
    #                 { "source": "Dev1", "target": "Repo1", "commits": 50 },
    #                 { "source": "Dev1", "target": "Repo2", "commits": 30 },
    #                 { "source": "Dev2", "target": "Repo1", "commits": 20 },
    #                 { "source": "Dev2", "target": "Repo3", "commits": 40 },
    #                 { "source": "Dev3", "target": "Repo2", "commits": 60 },
    #                 { "source": "Dev3", "target": "Repo3", "commits": 10 }
    #             ]
    #         }

    #     data["nodes"] = nodes
    #     data["links"] = links

    #     return data




class KospexData:
    """
    Data wrangling DSL like functions for Kospex database schema
    """

    def __init__(self, kospex_db=None):
        self.kospex_db = kospex_db
        self.params = []
        self.from_tables = []
        self.delete_statement = False
        self.select_columns = []
        self.where_clause = []
        self.group_by_columns = []
        self.order_by_columns = []
        self.limit_clause = None

    def get_bind_parameters(self):
        """ Return the bind parameters for the query """
        return self.params

    def where_join(self, table, column, join_table, join_column):
        """ Join two tables on a column """
        # Check the table are valid in our schema
        for t in (table, join_table):
            if t not in KospexSchema.KOSPEX_TABLES:
                raise ValueError(f"Table '{t}' not in KospexSchema.KOSPEX_TABLES")

        # Check the columns are valid SQL names
        for col in (column, join_column):
            if not self.is_valid_sql_name(col):
                raise ValueError(f"Column '{col}' is not a valid SQL name")

        # Add the join to the query
        self.where_clause.append(f"{table}.{column} = {join_table}.{join_column}")


    def where(self, column, operator, value):
        """ Add a where clause to the query """
        ops = [ "=", "<>", ">", "<", ">=", "<=", "LIKE", "NOT LIKE" ]
        if operator not in ops:
            raise ValueError(f"Operator '{column}' is not a valid operator in\n{', '.join(ops)}")

        if not self.is_valid_sql_name(column):
            raise ValueError(f"Column '{column}' is not a valid SQL name")

        self.where_clause.append(f"{column} {operator} ?")
        self.params.append(value)

    def limit(self, limit: Optional[int] = None):
        """ Add a limit clause to the query """
        if limit is not None:
            self.limit_clause = f"LIMIT {limit}"

    def where_dependency(self):
        """ HACK to add the tech_type  """
        #self.where_clause.append("tech_type LIKE '%dependency%'")
        self.where_clause.append("tech_type LIKE '%|dependencies|%'")

    def where_tag(self, tag):
        """
        Add a where clause to the query for tag, also called tech type
        """
        # We have to check this is a strict format of only allowing
        # letters and a dash, otherwise raise a ValueError
        if not re.match(r'^[a-zA-Z-]+$', tag):
            raise ValueError(f"Tag '{tag}' is not a valid format")

        self.where_clause.append(f"tech_type LIKE '%|{tag}|%'")

    def where_org_key(self,org_key):
        """ Use parse the org_key and set the required where fields
            _git_server  and _git_owner"""

        if "~" in org_key:
            parts = org_key.split('~')
            if len(parts) != 2:
                raise ValueError("org_key must be of the form <server>~<owner>")
            self.where("_git_server", "=", parts[0])
            self.where("_git_owner", "=", parts[1])

    def where_commit_filename(self, repo_id=None, file_path=None):
        """
        Add a where clause to the query for commit_files table
        """
        # The basic logic is a join between commits and commit_files
        # HASH in ( SELECT hash FROM commit_files WHERE _repo_id = ? AND file_path = ? )
        # so we need to do a subselect where the hash is in the commit_files table
        # and the file_path matches the file_path parameter in the commit_files table
        raw_sql = """
        hash IN ( SELECT hash FROM commit_files WHERE _repo_id = ? AND file_path = ? )
        """
        self.where_clause.append(raw_sql)
        self.params.append(repo_id)
        self.params.append(file_path)

    def from_table(self, *tables):
        """ Add a table to the query """
        for table in tables:
            if table not in KospexSchema.KOSPEX_TABLES:
                raise ValueError(f"Table '{table}' not in KospexSchema.KOSPEX_TABLES")
            else:
                self.from_tables.append(table)

    def valid_table_prefix_select(self, col):
        """ Check if a column name has a table prefix """
        if "." in col:
            parts = col.split(".")

            if len(parts) != 2:
                #raise ValueError(f"Invalid column name: {col}")
                return False

            if parts[0] not in KospexSchema.KOSPEX_TABLES:
                #raise ValueError(f"Table '{parts[0]}' not in KospexSchema.KOSPEX_TABLES")
                return False

            if not self.is_valid_sql_name(parts[1]):
                #raise ValueError(f"Column '{parts[1]}' is not a valid SQL name")
                return False

            # If we've got here, we're good to add this select column
            #self.select_columns.append(col)
            return True
        else:
            return False

    def delete(self):
        """ Set the query to delete data """
        self.delete_statement = True
        if self.select_columns:
            raise ValueError("Cannot delete and select columns in the same query")

    def select(self, *columns):
        """ Add a column to the query """
        if self.delete_statement:
            raise ValueError("Cannot delete and select columns in the same query")

        for col in columns:
            if self.is_valid_sql_name(col):
                self.select_columns.append(col)
            else:
                raise ValueError(f"Column '{col}' is not a valid SQL name")

    def select_raw(self, raw_column):
        """ HACK - add a raw column to the query """
        self.select_columns.append(raw_column)

    def extract_select_function_parts(self,sql_string):
        """ Regular expression pattern to match SQL function and its argument """
        pattern = r"^(\w+)\(([^)]+)\)$"

        # Find matches using the regular expression
        match = re.match(pattern, sql_string)
        if match:
            # Extract function name and argument
            function_name, argument = match.groups()
            return function_name, argument

        # Return None if no match is found
        #return None
        # this may not match, and possibly this is just a column rename
        return sql_string, None

    def allowed_sql_function(self, function_name):
        """ Check if a function is allowed """
        return function_name.upper() in ("DISTINCT", "COUNT", "SUM", "MIN", "MAX", "AVG", "LOWER")

    def select_as(self, column_query, alias):
        """ Add a column, including aggregate functions to the query as an alias """
        # TODO - handing only one function at the moment
        function_name, argument = self.extract_select_function_parts(column_query)

        if function_name and argument and self.is_valid_sql_name(alias):
            if self.allowed_sql_function(function_name) and self.is_valid_sql_name(argument):
                self.select_columns.append(f"{function_name}({argument}) AS {alias}")
        # Handle a straight column rename
        elif self.is_valid_sql_name(column_query) and self.is_valid_sql_name(alias):
            self.select_columns.append(f"{column_query} AS {alias}")
        else:
            raise ValueError(f"Invalid column query: '{column_query}' and alias: '{alias}'")

    def select_git_details(self):
        """
        Select all the git metadata _repo_id, _git_server, _git_owner, _git_repo
        """
        self.select("_repo_id")
        self.select("_git_server")
        self.select("_git_owner")
        self.select("_git_repo")


    def has_parentheses(self, string):
        """
        Check if a string has a pair of parentheses.

        :param string: A string to check for parentheses.
        :return: True if the string contains a pair of parentheses, False otherwise.
        """
        # Check for the presence of both opening and closing parentheses
        return '(' in string and ')' in string

    def parse_expression_with_regex_exact(self,expression):
        """
        Parse an expression using regular expressions and return the values of the functions,
        including the exact match of the arguments with parentheses.

        :param expression: A string representing the function expression.
        :return: A dictionary with the function names as keys and their arguments as values.
        """

        # Regular expression pattern to capture functions and their arguments, including parentheses
        #pattern = r'(\w+)\(((?:[^()]|\([^)]*\))*)\)'
        pattern = r'^([^()]*)\(((?:[^()]|\([^)]*\))*)\)'

        # Find the first match in the expression (assuming there's only one main function to parse)
        match = re.search(pattern, expression)

        # Convert match to a dictionary
        if match:
            parsed_function = {'function': match.group(1), 'value': match.group(2)}
        else:
            parsed_function = {}

        return parsed_function

    def validate_nested_expressions(self, s):
        """
        Validate that a string with parentheses uses allowed keywords only.
        """
        # Check for the presence of both opening and closing parentheses
        if not self.has_parentheses(s):
            return False
        sql = s

        while self.has_parentheses(sql):

            #res = parse_expression_with_regex_exact(sql)
            result = self.parse_expression_with_regex_exact(sql)
            function = result.get("function")
            value = result.get("value")

            # Check if the function name is allowed
            if function and not self.allowed_sql_function(function):
                #print(f"Invalid function: {function}")
                return False

            #if self.has_parentheses(res["value"]):
            if self.has_parentheses(value):
                #print(f"valid nested expression {value}")
                sql = value
            else:
                #print(f"Checking if {value} is a valid sql name")
                if self.is_valid_sql_name(value):
                    break

        return True

    def group_by(self, *columns, lower=None):
        """ Add a column to the query """
        for col in columns:
            if self.is_valid_sql_name(col):
                if lower:
                    self.group_by_columns.append(f"LOWER({col})")
                else:
                    self.group_by_columns.append(col)

    def order_by(self, column, direction="DESC"):
        """ Add a column to the query """
        direction = direction.upper()
        if direction not in ("ASC", "DESC"):
            raise ValueError(f"Invalid direction: '{direction}' must be ASC or DESC")
        if self.is_valid_sql_name(column):
            self.order_by_columns.append(f"{column} {direction}")

    def is_valid_sql_name(self,name):
        """ Check if a name is a valid SQL name for table or column names"""

        # List of simplified SQL reserved keywords
        reserved_keywords = {"SELECT", "UNION", "FROM", "WHERE", "DROP",
                             "INSERT", "UPDATE", "DELETE", "TABLE", "COLUMN"}

        # Check if the name is a wildcard
        if name == "*":
            return True

        if self.valid_table_prefix_select(name):
            tbl, col = name.split(".")
            name = col

        # Check if the name is a reserved keyword
        if name.upper() in reserved_keywords:
            #raise ValueError(f"Invalid name: '{name}' is a reserved SQL keyword.")
            # TODO - log this instead of print
            print(f"Invalid name: '{name}' is a reserved SQL keyword.")
            return False

        # Regular expression for valid SQL table/column names
        # Starts with a letter or underscore, followed by letters, digits, or underscores
        if not re.match(r'^[A-Za-z_][A-Za-z0-9_]*$', name):
            # TODO .. check for valid table prefix and return an error for that
            # We can get this error for a NOT_EXIST_TBL.col
            # TODO - log this instead of print
            #raise ValueError(f"Invalid name: '{name}' does not match SQL naming conventions.")
            print(f"Invalid name: '{name}' does not match SQL naming conventions.")
            return False

        # Optional: Check for length limits (default: 64 characters)
        if len(name) > 64:
            #raise ValueError(f"Invalid name: '{name}' exceeds the maximum allowed length.")
            # TODO - log this instead of print
            print(f"Invalid name: '{name}' exceeds the maximum allowed length.")
            return False

        return True

    def set_params_by_id(self,id_params):
        """
        Set common where clauses from params hash:
        repo_id
        org_key
        server
        """

        if repo_id := id_params.get("repo_id"):
            self.where("_repo_id","=",repo_id)
        elif org_key := id_params.get("org_key"):
            self.where_org_key(org_key)
        elif server := id_params.get("server"):
            self.where("_git_server","=",server)
        else:
            print(f"ERROR: can't identify {id_params}")





    def group_name_where_subselect(self, group_name):
        """ Add a subselect where clause to the query """

        #if not self.is_valid_sql_name(column):
        #    raise ValueError(f"Column '{column}' is not a valid SQL name")

        subselect_where = f""" _repo_id IN ( SELECT _repo_id FROM {KospexSchema.TBL_GROUPS} WHERE group_name = ? )"""

        self.where_clause.append(subselect_where)
        self.params.append(group_name)

    def generate_sql(self,line=None):
        """ Generate the SQL for the query """
        # The line will be used to add an extra carriage return if we want to print things.
        line_end = " "
        if line:
            line_end = "\n"

        sql = ""

        if self.delete_statement:
            sql = "DELETE "
        else:
            sql = "SELECT "
            if self.select_columns:
                sql += ", ".join(self.select_columns)
            else:
                sql += "*"
            sql += line_end

        sql += "FROM "
        sql += ", ".join(self.from_tables)
        sql += line_end

        if self.where_clause:
            sql += "WHERE "
            sql += " AND ".join(self.where_clause)
            sql += line_end

        if self.group_by_columns:
            sql += "GROUP BY "
            sql += ", ".join(self.group_by_columns)
            sql += line_end

        if self.order_by_columns:
            sql += "ORDER BY "
            sql += ", ".join(self.order_by_columns)

        if self.limit_clause:
            sql += " " + self.limit_clause
            sql += line_end

        return sql

    def execute(self):
        """ Execute the query """
        if not self.kospex_db:
            raise ValueError("No KospexDB object set")

        results = []
        data = []

        if self.delete_statement:
            res = self.kospex_db.execute(self.generate_sql(), self.params)
            if res:
                results = res.rowcount
            # TODO check why we need to commit here
            # Didn't delete rows unless we did this
            self.kospex_db.conn.commit()
        else:
            data = self.kospex_db.query(self.generate_sql(), self.params)

        # we'll only get data on a read
        # deletes won't return anything
        if data:
            for row in data:
                results.append(row)

        return results
