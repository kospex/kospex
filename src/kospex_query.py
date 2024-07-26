""" Use case queries for the kospex DB"""
import time
import re
from sqlite_utils import Database
import kospex_utils as KospexUtils
import kospex_schema as KospexSchema
import requests

class KospexQuery:
    """kospex database query functionality"""

    def __init__(self, kospex_db=None):
        # Initialize the kospex environment
        KospexUtils.init()
        self.kospex_db = kospex_db or Database(KospexUtils.get_kospex_db_path())

        #if kospex_db:
        #    self.kospex_db = kospex_db
        #else:
        #    self.kospex_db = Database(KospexUtils.get_kospex_db_path())

    def summary(self, days=None, repo_id=None):
        """
        Provide a summary of the known repositories.
        """

        summary_sql = """SELECT count(distinct(_repo_id)) AS 'repos', count(*) 'commits',
        count(distinct(author_email)) 'authors', count(distinct(committer_email)) 'committers',
        count(distinct(_git_server)) AS servers
        FROM commits"""
        params = []

        if days or repo_id:
            summary_sql += " WHERE 1=1"

        if days:
            from_date = KospexUtils.days_ago_iso_date(days)
            summary_sql += " AND committer_when > ?"
            params.append(from_date)

        if repo_id:
            summary_sql += " AND _repo_id = ?"
            params.append(repo_id)

        orgs = self.orgs()
        data = next(self.kospex_db.query(summary_sql, params), None)
        data['orgs'] = len(orgs)
        return data

    def repo_summary(self, repo_id):
        """ Provide a summary of the known repositories."""
        summary_sql = """SELECT count(distinct(_repo_id)) 'repos', count(*) 'commits',
        count(distinct(author_email)) 'authors', count(distinct(committer_email)) 'committers'
        FROM commits
        WHERE _repo_id = ?
        """
        data = next(self.kospex_db.query(summary_sql, [repo_id]), None)
        return data

    def server_summary(self):
        """ Provide a summary of the known servers."""
        summary_sql = """SELECT _git_server, count(distinct(_repo_id)) 'repos',
        count(distinct(author_email)) 'developers'
        FROM commits
        GROUP BY _git_server
        """
        data = []
        for row in self.kospex_db.query(summary_sql):
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
        Provider, Filename, committer_when, Language
        FROM file_metadata
        WHERE latest = 1 {where_clause}
        """
        data = []

        for row in self.kospex_db.query(summary_sql, params):
            data.append(row)

        return data


    def repos_by_author(self, author_email):
        """ Find repos for the given author_email."""

        summary_sql = """SELECT _repo_id, count(*) 'commits', MAX(committer_when) 'last_commit'
        FROM commits
        WHERE author_email = ?
        GROUP BY _repo_id
        ORDER BY commits DESC
        """
        data = []

        for row in self.kospex_db.query(summary_sql, [author_email]):
            row['last_seen'] = KospexUtils.days_ago(row['last_commit'])
            data.append(row)

        return data

    def repos(self,org_key=None):
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

        return data

    def orgs(self):
        """ Provide a summary of the known orgs."""
        summary_sql = """SELECT _git_server, _git_owner, count(*) 'commits',
        COUNT(DISTINCT(_git_repo)) AS repos,
        COUNT(DISTINCT(author_email)) 'authors', COUNT(DISTINCT(committer_email)) 'committers',
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

    def authors(self, days=None):
        """ Provide a summary of authors in the known repositories."""

        params = [] # parameters for the SQL query
        from_date = None

        if days:
            from_date = KospexUtils.days_ago_iso_date(days)

        authors = [] # list of authors from the sql query
        where_clause = ""

        if from_date:
            where_clause = "WHERE committer_when > ?"
            params.append(from_date)

        summary_sql = f"""SELECT distinct(author_email), count(*) 'commits',
        count(distinct(_repo_id)) 'repos', MAX(committer_when) 'last_commit'
        FROM commits
        {where_clause}
        GROUP BY author_email"""

        #summary_sql = """SELECT _repo_id, count(distinct(author_email)) 'devs'
        #FROM commits
        #WHERE committer_when > ?
        #GROUP BY _repo_id
        #"""
        data = self.kospex_db.query(summary_sql, params)
        for row in data:
            row['last_seen'] = KospexUtils.days_ago(row['last_commit'])
            authors.append(row)

        return authors

    def active_devs_by_repo(self, repo_id, days=90):
        """ Look for distinct developers in the last X 'days' """
        from_date = KospexUtils.days_ago_iso_date(days)
        summary_sql = """SELECT distinct(author_email) AS 'author_email', count(*) AS 'commits',
        MAX(committer_when) AS 'last_commit', count(distinct(_repo_id)) AS 'repos'
        FROM commits
        WHERE committer_when > ? AND _repo_id = ?
        GROUP BY author_email
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
        summary_sql = """SELECT author_email, count(*) 'commits', MIN(author_when) 'first_commit',
        MAX(author_when) 'last_commit'
        FROM commits
        WHERE _repo_id = ?
        GROUP BY author_email
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
        kd.group_by("_repo_id")

        if repo_id:
            kd.where("_repo_id", "=", repo_id)

        if org_key:
            parts = org_key.split('~')
            if len(parts) != 2:
                print("org_key must be of the form <server>~<owner>")
                return None
            kd.where("_git_server", "=", parts[0])
            kd.where("_git_owner", "=", parts[1])

        results =  kd.execute()

        ages = {
            'Active': 0,
            'Aging': 0,
            'Stale': 0,
            'Unmaintained': 0
        }

        for i in results:
            i['status'] = KospexUtils.development_status(i['last_commit'])
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

            print(where_clause)
            print(parts)

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

        print(sql)

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

        sql = """SELECT Location, filename, Lines, Complexity, Language
        FROM file_metadata
        WHERE _repo_id = ? AND latest = 1
        """
        metadata = {}
        data = self.kospex_db.query(sql, params)
        for row in data:
            metadata[row['Location']] = row
        print(metadata)
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

    def url_request(self, url, cache=3600, timeout=10):
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

        print(kd.generate_sql())


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
            results.append(row)

        return results

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

    def get_observations(self, repo_id=None, observation_key=None):
        """ Return a list of observations for a repo_id and observation_key """
        kd = KospexData(kospex_db=self.kospex_db)
        kd.from_table(KospexSchema.TBL_OBSERVATIONS)
        kd.select("*")
        if repo_id:
            kd.where("_repo_id", "=", repo_id)
        if observation_key:
            kd.where("observation_key", "=", observation_key)

        return kd.execute()

    def add_observation(self, observation):
        """ Add a list of observations to the database """
        self.kospex_db.table(
            KospexSchema.TBL_OBSERVATIONS).upsert(
                observation,pk=["_repo_id","hash","file_path","observation_key"])
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


class KospexData:
    """ Data wrangling DSL like functions for Kospex """

    def __init__(self, kospex_db=None):
        self.kospex_db = kospex_db
        self.params = []
        self.from_tables = []
        self.delete_statement = False
        self.select_columns = []
        self.where_clause = []
        self.group_by_columns = []
        self.order_by_columns = []

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
        return None

    def allowed_sql_function(self, function_name):
        """ Check if a function is allowed """
        return function_name.upper() in ("DISTINCT", "COUNT", "SUM", "MIN", "MAX", "AVG")

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

    def group_by(self, *columns):
        """ Add a column to the query """
        for col in columns:
            if self.is_valid_sql_name(col):
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
