"""
Tests for kospex schema
"""
import kospex_schema as KospexSchema

def test_generate_alter_table():
    """
    Test that we generate the correct differences given and old and a new table structure.
    """

    # Test the function on a close to real example, where we've added a single column
    old_sql_repos = """CREATE TABLE [repos] (
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
        )"""

    new_sql_repos = """CREATE TABLE IF NOT EXISTS [repos] (
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
        [additional_column] TEXT,
        PRIMARY KEY(_repo_id)
        )"""

    commands = KospexSchema.generate_alter_table(old_sql_repos, new_sql_repos,"repos")
    assert len(commands) == 1

    old_sql_config = """CREATE TABLE [kospex_config] (
        [_repo_id] TEXT,
        [details] TEXT,
        PRIMARY KEY(_repo_id)
        )"""

    # This one has a space in the name
    new_sql_config = """CREATE TABLE IF NOT EXISTS [kospex_config] (
        [_repo_id] TEXT,
        [details] TEXT,
        PRIMARY KEY(_repo_id)
        )"""

    commands = KospexSchema.generate_alter_table(old_sql_config, new_sql_config,"kospex_config")
    assert len(commands) == 0

    old_sql_config_1 = """CREATE TABLE [kospex_config] (
        [format] TEXT,           -- format type e.g. JSON, JSONL, CSV, TEXT, INTEGER, YAML
        [key] TEXT,              -- config item key, similar to a key in a dict
        [value] TEXT,            -- config item value, similar to a value in a dict, format specified in 'format'
        [latest] INTEGER,        -- 1 if this is the latest version of the metadata, 0 otherwise
        [created_at] DEFAULT CURRENT_TIMESTAMP,
        [updated_at] DEFAULT CURRENT_TIMESTAMP, [details] TEXT,
        PRIMARY KEY(key,latest)
        )"""

    new_sql_config_1 = """CREATE TABLE [kospex_config] (
            [format] TEXT,           -- format type e.g. JSON, JSONL, CSV, TEXT, INTEGER, YAML
            [key] TEXT,              -- config item key, similar to a key in a dict
            [value] TEXT,            -- config item value, similar to a value in a dict, format specified in 'format'
            [latest] INTEGER,        -- 1 if this is the latest version of the metadata, 0 otherwise
            [created_at] DEFAULT CURRENT_TIMESTAMP,
            [updated_at] DEFAULT CURRENT_TIMESTAMP,
            [details] TEXT,
            PRIMARY KEY(key,latest)
            )"""

    commands = KospexSchema.generate_alter_table(old_sql_config_1, new_sql_config_1,"kospex_config")
    assert len(commands) == 1

