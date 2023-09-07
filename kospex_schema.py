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

# Table based upon Mergestat sync 'git-commits'
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
    [_cycle_time] INTEGER   -- time between author and commit in seconds
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
    [_git_server] TEXT,
    [_git_owner] TEXT,
    [_git_repo] TEXT,
    [_repo_id] TEXT
    )'''

# This table will capture additional metadata about the files in the directory
# Initially we're going to use and external tool 'scc' to get the data
SQL_CREATE_FILE_METADATA = f'''CREATE TABLE [{TBL_FILE_METADATA}] (
    [Language] TEXT,        -- Language detected
    [Location] TEXT,        -- location of the file (i.e. like file_path in other tables)
    [Filename] TEXT,        -- filename
    [Lines] INTEGER,        -- Number of lines in the file
    [Code] INTEGER,         -- Number of lines of code
    [Comments] INTEGER,     -- Number of lines of comments
    [Blanks] INTEGER,       -- Number of blank lines
    [Complexity] INTEGER,   -- Cyclomatic complexity
    [Bytes] INTEGER,        -- Number of bytes in the file
    [hash] TEXT,            -- hash of the commit
    [tech_type] TEXT,       -- type of technology (e.g. 'python', or 'maven'). 
    [latest] INTEGER,       -- 1 if this is the latest version of the file, 0 otherwise
    [authors] INTEGER,      -- number of authors who've modified this file
    [commits] INTEGER,      -- number of commits that have been made to this file
    [_mtime] INTEGER,       -- last modified time of the file
    [_git_server] TEXT,
    [_git_owner] TEXT,
    [_git_repo] TEXT,
    [_repo_id] TEXT,
    PRIMARY KEY(Location,hash,_repo_id)
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
        kospex_db.execute(SQL_CREATE_FILE_METADATA)
        kospex_db.execute(SQL_CREATE_REPO_HOTSPOTS)

    return kospex_db
