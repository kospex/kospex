""" Helper functions for kospex related to SQLite and database operations """
import os
from sqlite_utils import Database
import kospex_utils as KospexUtils

# Definitions of the kospex DB tables

TBL_COMMITS = "commits"
TBL_COMMIT_FILES = "commit_files"
TBL_COMMIT_METADATA = "commit_metadata"
TBL_FILE_METADATA = "file_metadata"
TBL_REPO_HOTSPOTS = "repo_hotspots"
TBL_DEPENDENCY_DATA = "dependency_data"
TBL_URL_CACHE = "url_cache"
TBL_KRUNNER = "krunner"
TBL_OBSERVATIONS = "observations"

KOSPEX_TABLES = [ TBL_COMMITS, TBL_COMMIT_FILES, TBL_COMMIT_METADATA, TBL_FILE_METADATA,
                 TBL_REPO_HOTSPOTS, TBL_DEPENDENCY_DATA, TBL_URL_CACHE, TBL_KRUNNER, TBL_OBSERVATIONS ]

# Table data structure based upon Mergestat sync 'git-commits'
# https://github.com/mergestat/syncs/blob/main/syncs/git-commits/schema.sql
SQL_CREATE_COMMITS = f'''CREATE TABLE [{TBL_COMMITS}] (
    [hash] TEXT,
    [author_email] TEXT,
    [author_name] TEXT,
    [author_when] TEXT,
    [committer_email] TEXT,
    [committer_name] TEXT,
    [committer_when] TEXT,
    [message] TEXT,
    [parents] INTEGER,
    [_git_server] TEXT,
    [_git_owner] TEXT,
    [_git_repo] TEXT,
    [_repo_id] TEXT,
    [_files] INTEGER,       -- number of files changed
    [_cycle_time] INTEGER,   -- time between author and commit in seconds
    PRIMARY KEY(_repo_id,hash)
    )'''

# Table based up Mergestat sync 'git-commit-stats'
# https://github.com/mergestat/syncs/blob/main/syncs/git-commit-stats/schema.sql
SQL_CREATE_COMMIT_FILES = f'''CREATE TABLE [{TBL_COMMIT_FILES}] (
    [hash] TEXT,
    [file_path] TEXT,
    [_ext] TEXT,
    [additions] INTEGER,
    [deletions] INTEGER,
    [committer_when] TEXT,
    [path_change] TEXT,     -- Raw git change path 
    [_git_server] TEXT,
    [_git_owner] TEXT,
    [_git_repo] TEXT,
    [_repo_id] TEXT,
    PRIMARY KEY(hash,file_path,_repo_id)
    )'''

# This table will capture additional metadata about the files in the directory
# Initially we're going to use and external tool 'scc' to get the data
SQL_CREATE_FILE_METADATA = f'''CREATE TABLE IF NOT EXISTS [{TBL_FILE_METADATA}] (
    [Language] TEXT,        -- Language detected
    [Provider] TEXT,        -- location of the file (i.e. like file_path in other tables)
    -- Provider used to be Location
    [Filename] TEXT,        -- filename
    [Lines] INTEGER,        -- Number of lines in the file
    [Code] INTEGER,         -- Number of lines of code
    [Comments] INTEGER,     -- Number of lines of comments
    [Blanks] INTEGER,       -- Number of blank lines
    [Complexity] INTEGER,   -- Cyclomatic complexity
    [Bytes] INTEGER,        -- Number of bytes in the file
    [hash] TEXT,            -- hash of the current commit of the repo
    [tech_type] TEXT,       -- type of technology (e.g. 'python', or 'maven'). 
    [latest] INTEGER,       -- 1 if this is the latest version of the file, 0 otherwise
    [authors] INTEGER,      -- number of authors who've modified this file
    [commits] INTEGER,      -- number of commits that have been made to this file
    [_mtime] INTEGER,       -- last modified time of the file - TODO - may need to remove this
    [committer_when] TEXT,  -- date of last commit
    [_metadata_source] TEXT,-- what tool was used to get the metadata
    [_git_server] TEXT,
    [_git_owner] TEXT,
    [_git_repo] TEXT,
    [_repo_id] TEXT,
    PRIMARY KEY(Provider,hash,_repo_id)
    )'''

# This table will capture detail some hotspot indicators about a repo
SQL_CREATE_REPO_HOTSPOTS = f'''CREATE TABLE [{TBL_REPO_HOTSPOTS}] (
        [commits] INTEGER,  -- number of commits
        [authors] INTEGER,  -- number of authors who've committed
        [files] INTEGER,    -- number of distinct files seen
        [loc] INTEGER,      -- lines of code (total)
        [first_seen] TEXT,  -- date of first commit
        [last_seen] TEXT,   -- date of last commit
        [hash] TEXT,        -- hash of the commit
        [_git_server] TEXT,
        [_git_owner] TEXT,
        [_git_repo] TEXT,
        [_repo_id] TEXT,
        PRIMARY KEY(_repo_id,hash)
        )'''

# We're going to capture additional metadata about the commits
SQL_CREATE_COMMIT_METADATA = f'''CREATE TABLE [{TBL_COMMIT_METADATA}] (
    [hash] TEXT,        -- hash of the commit
    [name] TEXT,        -- name of the metadata (e.g. 'directory_size')
    [value] TEXT,       -- data point (e.g. '12345' or "High")
    [source] TEXT,      -- what tool was used to get the metadata
    [_git_server] TEXT,
    [_git_owner] TEXT,
    [_git_repo] TEXT,
    [_repo_id] TEXT
    )'''

# We're going to capture additional data about the dependencies in a file
#SQL_PK_DEPENDENCY_DATA = '''PRIMARY KEY(_repo_id,hash,file_path,package_type,package_name,package_version)'''
SQL_CREATE_DEPENDENCY_DATA = f'''CREATE TABLE IF NOT EXISTS [{TBL_DEPENDENCY_DATA}] (
    [hash] TEXT,                    -- hash of the commit
    [file_path] TEXT,               -- file path in the repo
    [package_type] TEXT,            -- type of package (e.g. 'PyPi', or 'maven')
    [package_name] TEXT,            -- name of the package
    [package_version] TEXT,         -- version of the package
    [package_use] TEXT,             -- free form, most likely direct, development, testing
    [published_at] TEXT,            -- date the package was published 
    [advisories] INTEGER,           -- # of security security advisories
    [versions_behind] INTEGER,      -- number of versions behind
    [source_repo] TEXT,             -- URL of the source repo for the package
    [default] TEXT,                 -- If this is the default version to be installed of the package
    [source] TEXT,                  -- what tool was used to get the metadata
    [latest] INTEGER,               -- 1 if this is the entry for this file, package etc, 0 otherwise
    [created_at] DEFAULT CURRENT_TIMESTAMP,
    [_git_server] TEXT,
    [_git_owner] TEXT,
    [_git_repo] TEXT,
    [_repo_id] TEXT,
    PRIMARY KEY(_repo_id,hash,file_path,package_type,package_name,package_version)
    )'''
    # TODO - Double check the primary key, this feels execessive
    # Potentially the file_path, hash and package_name would be enough

SQL_CREATE_KRUNNER = f'''CREATE TABLE  IF NOT EXISTS [{TBL_KRUNNER}] (
    [hash] TEXT,        -- hash of the commit
    [file_path] TEXT,   -- file path in the repo
    [format] TEXT,      -- format type e.g. JSON, JSONL, CSV, LINE
    [data] TEXT,        -- Data / output from the command
    [source] TEXT,      -- what tool/function was used to get the metadata
    [command] TEXT,     -- command ran to get the data (optional)
    [created_at] DEFAULT CURRENT_TIMESTAMP,
    [_git_server] TEXT,
    [_git_owner] TEXT,
    [_git_repo] TEXT,
    [_repo_id] TEXT,
    PRIMARY KEY(_repo_id,hash,file_path)
    )'''



#SQL_CREATE_URL_CACHE = f'''CREATE TABLE IF NOT EXISTS [{TBL_URL_CACHE}] (
#    [url] TEXT, -- URL to cache
#    content TEXT,
#    timestamp REAL,
#    [hash] TEXT,                    -- hash of the commit
#    [created_at] DEFAULT CURRENT_TIMESTAMP,
#    [_git_server] TEXT,
#    [_git_owner] TEXT,
#    [_git_repo] TEXT,
#    [_repo_id] TEXT,
#    PRIMARY KEY(_repo_id,hash,file_path,package_type,package_name,package_version)
#    )'''

SQL_CREATE_URL_CACHE = f'''CREATE TABLE IF NOT EXISTS [{TBL_URL_CACHE}] (
    [url] TEXT, -- URL to cache
    [content] TEXT,
    [timestamp] REAL,
    PRIMARY KEY(url)
    )'''

#SQL_CREATE_OBSERVATIONS = f'''CREATE TABLE IF NOT EXISTS [{TBL_OBSERVATIONS}] (
#    [file_path] TEXT,      -- file path in the repo
#    [data] TEXT,           -- Data / output from the command, or line details
#    [line_number] INTEGER, -- line number of the observation, if applicable
#    [observed_at] TEXT,    -- date and time the observation was made
#    [source] TEXT,         -- what tool/function was used to get the metadata,
#    [hash] TEXT,
#    [_git_server] TEXT,
#    [_git_owner] TEXT,
#    [_git_repo] TEXT,
#    [_repo_id] TEXT,
#    PRIMARY KEY(file_path,_repo_id,hash)
#    )'''

SQL_CREATE_OBSERVATIONS = f'''CREATE TABLE  IF NOT EXISTS [{TBL_OBSERVATIONS}] (
    [hash] TEXT,             -- hash of the commit
    [file_path] TEXT,        -- file path in the repo
    [format] TEXT,           -- format type e.g. JSON, JSONL, CSV, LINE
    [data] TEXT,             -- cleaned data / output from the command
    [raw] TEXT,              -- Raw data / output from the command
    [source] TEXT,           -- what tool/function was used to get the metadata
    [observation_key] TEXT,  -- unique identified for the observation e.g. SEMGREP, GREP_TODO 
    [observation_type] TEXT, -- should be one of the REPO, FILE 
    [line_number] INTEGER,   -- line number in the file (optional)
    [command] TEXT,          -- command ran to get the data (optional)
    [latest] INTEGER,        -- 1 if this is the latest version of the file, 0 otherwise
    [created_at] DEFAULT CURRENT_TIMESTAMP,
    [_git_server] TEXT,
    [_git_owner] TEXT,
    [_git_repo] TEXT,
    [_repo_id] TEXT,
    PRIMARY KEY(_repo_id,hash,file_path,observation_key)
    )'''

# Functions for SQLite stuff

def dict_factory(cursor, row):
    """
    Used for returning a dictionary from a sqlite query, instead of an array
    """
    col_names = [col[0] for col in cursor.description]
    return dict(zip(col_names, row))

def connect_or_create_kospex_db():
    """ Connect to the kospex DB or create it if it doesn't exist """ 
    new_db = False
    db_path = KospexUtils.get_kospex_db_path()

    if not os.path.isfile(db_path):
        new_db = True

    kospex_db = Database(db_path)

    if new_db:
        print("Creating new kospex DB")
        kospex_db.execute(SQL_CREATE_COMMITS)
        kospex_db.execute(SQL_CREATE_COMMIT_FILES)
        kospex_db.execute(SQL_CREATE_COMMIT_METADATA)
        kospex_db.execute(SQL_CREATE_REPO_HOTSPOTS)

    # The following tables only are created if they don't exist
    kospex_db.execute(SQL_CREATE_FILE_METADATA)
    kospex_db.execute(SQL_CREATE_DEPENDENCY_DATA)
    kospex_db.execute(SQL_CREATE_URL_CACHE)
    kospex_db.execute(SQL_CREATE_KRUNNER)
    kospex_db.execute(SQL_CREATE_OBSERVATIONS)
    # TODO - look at moving all table creates to "create if not exits"

    return kospex_db

def drop_table(table):
    """ Drop a table from the DB """
    db = connect_or_create_kospex_db()
    if table in KOSPEX_TABLES:
        db.execute(f"DROP TABLE IF EXISTS [{table}]")
    else:
        print(f"Invalid table '{table}', was not in KOSPEX_TABLES")
