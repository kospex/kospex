""" Helper functions for kospex related to SQLite and database operations """
import os
from sqlite_utils import Database
import kospex_utils as KospexUtils

# Definitions of the kospex DB tables
TBL_BRANCHES = "branches"
TBL_BRANCH_HISTORY = "branch_history"
TBL_COMMITS = "commits"
TBL_COMMIT_FILES = "commit_files"
TBL_COMMIT_METADATA = "commit_metadata"
TBL_FILE_METADATA = "file_metadata"
TBL_REPO_HOTSPOTS = "repo_hotspots"
TBL_DEPENDENCY_DATA = "dependency_data"
TBL_URL_CACHE = "url_cache"
TBL_KRUNNER = "krunner"
TBL_OBSERVATIONS = "observations"
TBL_REPOS = "repos"
# Experimental tables
TBL_KOSPEX_META = "kospex_meta"
TBL_GROUPS = "kospex_groups"
TBL_KOSPEX_CONFIG = "kospex_config"
# Not yet implemented
TBL_MAILMAP = "mailmaps"



KOSPEX_TABLES = [ TBL_COMMITS, TBL_COMMIT_FILES, TBL_COMMIT_METADATA, TBL_FILE_METADATA,
                TBL_REPO_HOTSPOTS, TBL_DEPENDENCY_DATA, TBL_URL_CACHE, TBL_KRUNNER,
                TBL_OBSERVATIONS, TBL_REPOS, TBL_KOSPEX_META, TBL_GROUPS, TBL_KOSPEX_CONFIG ]

# The following are tables with a repo_id
REPO_TABLES = [ TBL_COMMITS, TBL_COMMIT_FILES, TBL_COMMIT_METADATA, TBL_FILE_METADATA,
                TBL_REPO_HOTSPOTS, TBL_DEPENDENCY_DATA, TBL_KRUNNER, TBL_OBSERVATIONS, TBL_REPOS,
                TBL_GROUPS, TBL_BRANCHES, TBL_BRANCH_HISTORY ]

# Mapping of table name to create statement is below the create statement definitions in:
# DB_CREATE_STATEMENTS

# KOSPEX_DB_VERSION will be updated every time we updated the schema
KOSPEX_DB_VERSION=2
KOSPEX_DB_VERSION_KEY = "KOSPEX_DB_VERSION_KEY"
# Version 1, we're drawing a line in the sand as of 2025-02-16

# Table data structure inspired by Mergestat sync 'git-commits'
# https://github.com/mergestat/syncs/blob/main/syncs/git-commits/schema.sql
SQL_CREATE_COMMITS = f'''CREATE TABLE IF NOT EXISTS [{TBL_COMMITS}] (
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

# Table inspired by Mergestat sync 'git-commit-stats'
# https://github.com/mergestat/syncs/blob/main/syncs/git-commit-stats/schema.sql
SQL_CREATE_COMMIT_FILES = f'''CREATE TABLE IF NOT EXISTS [{TBL_COMMIT_FILES}] (
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
    -- Provider used to be Location, this is based on the scc output
    [Filename] TEXT,        -- filename
    [Lines] INTEGER,        -- Number of lines in the file
    [Code] INTEGER,         -- Number of lines of code
    [Comments] INTEGER,     -- Number of lines of comments
    [Blanks] INTEGER,       -- Number of blank lines
    [Complexity] INTEGER,   -- Cyclomatic complexity
    [Bytes] INTEGER,        -- Number of bytes in the file
    [hash] TEXT,            -- hash of the current commit of the repo
    [tech_type] TEXT,       -- type of technology (e.g. 'python', or 'maven').
    [latest] INTEGER,       -- 1 if this is the latest version of the file metadata, 0 otherwise
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

# We're going to store the unique repo details separately
SQL_CREATE_REPOS = f'''CREATE TABLE IF NOT EXISTS [{TBL_REPOS}] (
    [_repo_id] TEXT,
    [_git_server] TEXT,
    [_git_owner] TEXT,
    [_git_repo] TEXT,
    [created_at] DEFAULT CURRENT_TIMESTAMP,
    [last_sync] TEXT,  -- date of last kospex sync
    [last_seen] TEXT,  -- date of last commit, to be updated by the sync
    [first_seen] TEXT, -- date of first commit, to be updated by the sync
    [git_remote] TEXT, -- URL of the git repo
    [file_path] TEXT,  -- path to the repo on the local filesystem
    PRIMARY KEY(_repo_id)
    )'''

# This table will capture detail some hotspot indicators about a repo
SQL_CREATE_REPO_HOTSPOTS = f'''CREATE TABLE IF NOT EXISTS [{TBL_REPO_HOTSPOTS}] (
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

# TODO - revist parsing of scc output, and required schema changes
# As of version 3.3.3, the output is:
# Language,Provider,Filename,Lines,Code,Comments,Blanks,Complexity,Bytes,ULOC
# A new ULOC parameter was added, which is not currently in our schema

# We're going to capture additional metadata about the commits
SQL_CREATE_COMMIT_METADATA = f'''CREATE TABLE IF NOT EXISTS [{TBL_COMMIT_METADATA}] (
    [hash] TEXT,        -- hash of the commit
    [name] TEXT,        -- name of the metadata (e.g. 'directory_size')
    [value] TEXT,       -- data point (e.g. '12345' or "High")
    [source] TEXT,      -- what tool was used to get the metadata
    [_git_server] TEXT,
    [_git_owner] TEXT,
    [_git_repo] TEXT,
    [_repo_id] TEXT
    )'''

# This table will store the current branch data for a repository
SQL_CREATE_BRANCHES = f'''CREATE TABLE IF NOT EXISTS [{TBL_BRANCHES}] (
    [count] INTEGER,
    [branch_data] TEXT, -- JSON array of branch names
    [hash] TEXT,        -- hash of the commit
    [_repo_id] TEXT,
    [_git_server] TEXT,
    [_git_owner] TEXT,
    [_git_repo] TEXT,
    [created_at] DEFAULT CURRENT_TIMESTAMP,
    [last_sync] TEXT,  -- date of last kospex sync
    [last_seen] TEXT,  -- date of last commit, to be updated by the sync
    PRIMARY KEY(_repo_id)
    )'''

# This table will store the HISTORIC branch data for a repository
SQL_CREATE_BRANCH_HISTORY = f'''CREATE TABLE IF NOT EXISTS [{TBL_BRANCH_HISTORY}] (
    [count] INTEGER,
    [branch_data] TEXT, -- JSON array of branch names
    [hash] TEXT,        -- hash of the commit
    [_repo_id] TEXT,
    [_git_server] TEXT,
    [_git_owner] TEXT,
    [_git_repo] TEXT,
    [created_at] DEFAULT CURRENT_TIMESTAMP,
    [last_sync] TEXT,  -- date of last kospex sync
    [last_seen] TEXT,  -- date of last commit, to be updated by the sync
    PRIMARY KEY(_repo_id, hash)
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
    [uuid] TEXT,            -- unique identifier for the observation
    [hash] TEXT,             -- hash of the commit
    [file_path] TEXT,        -- file path in the repo (if applicable, can be repo level)
    [format] TEXT,           -- format type e.g. JSON, JSONL, CSV, LINE
    [data] TEXT,             -- cleaned data / output from the command
    [raw] TEXT,              -- Raw data / output from the command
    [source] TEXT,           -- what tool/function was used to get the metadata
    [observation_key] TEXT,  -- unique identified for the observation e.g. SEMGREP, GREP_TODO
    [observation_type] TEXT, -- should be one of the REPO, FILE
    [line_number] INTEGER,   -- line number in the file (optional)
    [command] TEXT,          -- command ran to get the data (optional)
    [latest] INTEGER,        -- 1 if this is the latest version of the observation, 0 otherwise
    [created_at] DEFAULT CURRENT_TIMESTAMP,
    [_git_server] TEXT,
    [_git_owner] TEXT,
    [_git_repo] TEXT,
    [_repo_id] TEXT,
    PRIMARY KEY(_repo_id,hash,file_path,observation_key,latest)
    )'''

# TODO - This table has not been set up properly or created yet
# Unsure if we still need this ... 2025-02-16
SQL_CREATE_KOSPEX_META = f'''CREATE TABLE  IF NOT EXISTS [{TBL_KOSPEX_META}] (
    [format] TEXT,           -- format type e.g. JSON, JSONL, CSV, LINE
    [latest] INTEGER,        -- 1 if this is the latest version of the metadata, 0 otherwise
    [created_at] DEFAULT CURRENT_TIMESTAMP,
    [_git_server] TEXT,
    [_git_owner] TEXT,
    [_git_repo] TEXT,
    [_repo_id] TEXT,
    PRIMARY KEY(_repo_id,hash,file_path)
    )'''

#SQL_CREATE_KOSPEX_CONFIG = f'''CREATE TABLE IF NOT EXISTS [{TBL_KOSPEX_CONFIG}] (
SQL_CREATE_KOSPEX_CONFIG = f'''CREATE TABLE IF NOT EXISTS [{TBL_KOSPEX_CONFIG}] (
    [format] TEXT,           -- format type e.g. JSON, JSONL, CSV, TEXT, INTEGER, YAML
    [key] TEXT,              -- config item key, similar to a key in a dict
    [value] TEXT,            -- config item value, similar to a value in a dict, format specified in 'format'
    [latest] INTEGER,        -- 1 if this is the latest version of the metadata, 0 otherwise
    [created_at] DEFAULT CURRENT_TIMESTAMP,
    [updated_at] DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(key,latest)
    )'''


# Used for querying based on groups
SQL_CREATE_GROUPS = f'''CREATE TABLE  IF NOT EXISTS [{TBL_GROUPS}] (
    [group_name] TEXT,  -- Name of the group
    [git_url] TEXT,     -- repo_url (if applicable) optional (either repo_url or email)
    [email] TEXT,       -- email (if applicable) optional (either repo_url or email),
    [data] TEXT,        -- type of data (e.g. 'git_url', 'email', other)
    [data_type] TEXT,   -- type of data (e.g. 'git_url', 'email', other)
    [created_at] DEFAULT CURRENT_TIMESTAMP,
    [_repo_id] TEXT,    -- normalised _repo_id from git_url
    PRIMARY KEY(group_name,_repo_id,email)
    )'''

SQL_CREATE_MAILMAP = f'''CREATE TABLE  IF NOT EXISTS [{TBL_MAILMAP}] (
    [file_path] TEXT,      -- file path in the repo (if applicable, can be repo level)
    [proper_name] TEXT,    -- the "correct" name
    [email] TEXT,
    [committer_name] TEXT, -- Name we are correcting
    [committer_email] TEXT,-- Email we are changing from to email
    [created_at] DEFAULT CURRENT_TIMESTAMP,
    [_git_server] TEXT,
    [_git_owner] TEXT,
    [_git_repo] TEXT,
    [_repo_id] TEXT,       -- normalised _repo_id from git_url (optional)
    PRIMARY KEY(_repo_id,email,committer_email)
    )'''

DB_CREATE_STATEMENTS = {
    TBL_COMMITS: SQL_CREATE_COMMITS,
    TBL_COMMIT_FILES: SQL_CREATE_COMMIT_FILES,
    TBL_COMMIT_METADATA: SQL_CREATE_COMMIT_METADATA,
    TBL_FILE_METADATA: SQL_CREATE_FILE_METADATA,
    TBL_REPO_HOTSPOTS: SQL_CREATE_REPO_HOTSPOTS,
    TBL_DEPENDENCY_DATA: SQL_CREATE_DEPENDENCY_DATA,
    TBL_URL_CACHE: SQL_CREATE_URL_CACHE,
    TBL_KRUNNER: SQL_CREATE_KRUNNER,
    TBL_OBSERVATIONS: SQL_CREATE_OBSERVATIONS,
    TBL_REPOS: SQL_CREATE_REPOS,
    TBL_KOSPEX_META: SQL_CREATE_KOSPEX_META,
    TBL_GROUPS: SQL_CREATE_GROUPS,
    TBL_KOSPEX_CONFIG: SQL_CREATE_KOSPEX_CONFIG,
    TBL_BRANCHES: SQL_CREATE_BRANCHES,
    TBL_BRANCH_HISTORY: SQL_CREATE_BRANCH_HISTORY,
}

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
    kospex_db.execute(SQL_CREATE_REPOS)
    kospex_db.execute(SQL_CREATE_GROUPS)
    kospex_db.execute(SQL_CREATE_KOSPEX_CONFIG)

    kospex_db.execute(SQL_CREATE_BRANCHES)
    kospex_db.execute(SQL_CREATE_BRANCH_HISTORY)

    # TODO - look at moving all table creates to "create if not exits"

    if new_db:
        # Set the database version
        kospex_db.execute(
            f"INSERT INTO {TBL_KOSPEX_CONFIG} (key, value, format, latest) VALUES (?, ?, ?, ?)",
            [KOSPEX_DB_VERSION_KEY, str(KOSPEX_DB_VERSION), 'INTEGER', 1]
        )

    return kospex_db

def drop_table(table):
    """ Drop a table from the DB """
    db = connect_or_create_kospex_db()
    if table in KOSPEX_TABLES:
        db.execute(f"DROP TABLE IF EXISTS [{table}]")
        print(f"Dropped table '{table}', if it existed")
    else:
        print(f"Invalid table '{table}', was not in KOSPEX_TABLES")


def array_to_db_tags(tags):
    """
    Take an array like ['pip', 'Python', 'dependencies']
    and return a string like |pip|Python|dependencies|
    """
    if tags is None:
        return None

    return "|" + "|".join(tags) + "|"


def db_tags_to_array(db_tags):
    """
    Take a tags string like |pip|Python|dependencies|
    and return an array like ['pip', 'Python', 'dependencies']
    """
    if db_tags is None:
        return None

    grouped = db_tags.strip('|')
    tags = grouped.split('|')
    return tags

def metadata_rows_from_repo_files(files):
    """
    This function takes a dict (file_path) to dict (file details)
    and returns a list of dicts suitable for an upsert
    into the TBL_FILE_METADATA table
    """

    rows = []

    keys_to_keep = ['Provider', 'Filename', 'hash', 'Latest', 'Language', 'committer_when', 'tech_type',
        '_git_server',
        '_git_owner',
        '_git_repo',
        '_repo_id']

    for inner_dict in files.values():
        if 'Location' in inner_dict:
            inner_dict['Provider'] = inner_dict.pop('Location')
        inner_dict['Latest'] = 1
        inner_dict['tech_type'] = array_to_db_tags(inner_dict.get('tech_type'))

    for inner_dict in files.values():
        filtered_dict = {}
        for key in keys_to_keep:
            if key in inner_dict:  # Only include the key if it exists
                filtered_dict[key] = inner_dict[key]
                rows.append(filtered_dict)
        #print(filtered_dict)

    return rows

    # Claude.ai helped out with this one below
    # Seems to work, and generates an alter table per column added
    # TODO .. make sure all the old table columns are in the new table as sanity checking

def generate_alter_table(old_create_sql, new_create_sql,tbl_name):

    def extract_columns(create_sql):
        start = create_sql.find('(') + 1
        end = create_sql.rindex(')')
        columns_section = create_sql[start:end]

        # Split into lines and process each line
        columns = []
        for line in columns_section.split('\n'):
            line = line.strip()
            if not line or line.startswith('PRIMARY KEY'):
                continue

            # Remove any inline comments
            if '--' in line:
                line = line.split('--')[0].strip()

            # Remove trailing comma if present
            if line.endswith(','):
                line = line[:-1].strip()

            # Skip empty lines after processing
            if not line:
                continue

            # Get column name from square brackets
            if '[' in line:
                col_name = line[line.find('[')+1:line.find(']')]
                columns.append((col_name, line))

        # TODO - Add as debug log
        #print(f"Processed columns: {columns}")
        return columns

    old_cols = extract_columns(old_create_sql)
    new_cols = extract_columns(new_create_sql)

    old_names = [name for name, _ in old_cols]
    new_names = [name for name, _ in new_cols]

    # TODO - Add as debug log
    #print(f"\nOld column names: {old_names}")
    #print(f"New column names: {new_names}")

    added_cols = [(name, def_) for name, def_ in new_cols if name not in old_names]

    # TODO - Add as debug log
    #print(f"Added columns: {added_cols}")

    alter_commands = []
    for _, col_def in added_cols:
        alter_commands.append(f"ALTER TABLE [{tbl_name}] ADD COLUMN {col_def}\n")

    return alter_commands

def validate_square_brackets2(create_sql):
    # Get the columns section
    start = create_sql.find('(') + 1
    end = create_sql.rindex(')')
    columns_section = create_sql[start:end]

    # Check each line
    invalid_columns = []

    for line in columns_section.split('\n'):
        line = line.strip()
        # Skip empty lines, comments, or PRIMARY KEY
        if not line or line.startswith('--') or line.startswith('PRIMARY KEY'):
            continue

        # Remove comments
        if '--' in line:
            line = line.split('--')[0].strip()

        # Split line by commas to handle multiple columns on one line
        # but be careful not to split on commas inside parentheses (for types like VARCHAR(255))
        columns_in_line = []
        current_column = ""
        paren_level = 0

        for char in line:
            if char == '(':
                paren_level += 1
                current_column += char
            elif char == ')':
                paren_level -= 1
                current_column += char
            elif char == ',' and paren_level == 0:
                # End of a column definition
                if current_column.strip():
                    columns_in_line.append(current_column.strip())
                current_column = ""
            else:
                current_column += char

        # Add the last column if there is one
        if current_column.strip():
            columns_in_line.append(current_column.strip())

        # Check each column declaration
        for col_decl in columns_in_line:
            # Check if the column declaration starts with a square bracket
            if not col_decl.strip().startswith('['):
                invalid_columns.append(col_decl.strip())

    if invalid_columns:
        print("The following column declarations are missing square brackets:")
        for col in invalid_columns:
            print(f"  {col}")
        return False
    return True

def validate_square_brackets(create_sql):
    # Get the columns section
    start = create_sql.find('(') + 1
    end = create_sql.rindex(')')
    columns_section = create_sql[start:end]

    # Check each line
    invalid_lines = []
    for line in columns_section.split('\n'):
        line = line.strip()
        # Skip empty lines, comments, or PRIMARY KEY
        if not line or line.startswith('--') or line.startswith('PRIMARY KEY'):
            continue
        # Remove comments and trailing comma
        if '--' in line:
            line = line.split('--')[0].strip()
        if line.endswith(','):
            line = line[:-1].strip()
        # Skip if empty after cleaning
        if not line:
            continue

        # Check if the line starts with a square bracket
        if not line.startswith('['):
            invalid_lines.append(line)

    if invalid_lines:
        print("The following lines are missing square brackets:")
        for line in invalid_lines:
            print(f"  {line}")
        return False
    return True
