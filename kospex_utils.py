""" Helper functions for kospex """
import os
import re
import glob
from datetime import datetime, timezone, timedelta
import subprocess
import shlex
import csv
from dotenv import load_dotenv

KOSPEX_DB_FILENAME="kospex.db"

def is_git(directory):
    """Simple directory check to see if it's a git repo"""
    git_path = f"{directory}/.git/"
    return os.path.exists(git_path)

def init():
    """ Initialize the kospex environment """
    user_kospex_home = os.path.expanduser("~/kospex")
    kospex_home = os.getenv("KOSPEX_HOME",user_kospex_home)
    if kospex_home == user_kospex_home:
        if not os.path.exists(kospex_home):
            os.mkdir(kospex_home)
    load_config(f"{kospex_home}/kospex.env")

    # Set a default kospex code directory
    kospex_code = os.path.expanduser("~/code")


    # Set up some basic around the kospex code where all the repos live
    if os.getenv("KOSPEX_CODE") is None:
        os.environ["KOSPEX_CODE"] = kospex_code

    if not os.path.isdir(os.getenv("KOSPEX_CODE")):
        print(f"WARNING: KOSPEX_CODE directory '{kospex_code} does not exist!")

def get_kospex_db_path():
    """ Get the kospex database """
    default_kospex_db = os.path.expanduser(f"~/kospex/{KOSPEX_DB_FILENAME}")
    return os.getenv("KOSPEX_DB",default_kospex_db)

def load_config(config_file):
    """ Load the config file """
    load_dotenv(dotenv_path=config_file)

def get_extension(filename):
    """
    Return the extension of the given filename, or the filename if it doesn't have an extension
    """
    base_filename = os.path.basename(filename)
    f_path, f_ext = os.path.splitext(base_filename)
    if f_ext:
        return f_ext
    # Otherwise return the filename
    return f_path

def extract_github_username(author_email):
    """
    Extract the github username from the author_email, where the privacy settings are honored.
    E.g. 123489589+gh-username@users.noreply.github.com
    Should produce gh-username
    """
    if author_email:
        return author_email.split("@")[0].split("+")[-1]

def find_repos(directory):
    """ Find all git repos in the directory and subdirectories"""
    repos = []
    results = glob.glob(directory + "/**/.git", recursive=True)
    for file in results:
        git_dir = file.replace("/.git", "")
        repos.append(git_dir)

    return repos

def validate_params(**kwargs):
    """ Validate the cli parameters """
    #if kwargs.get('before') and kwargs.get('after'):
    #    raise ValueError("You can't specify both -before and -after")

    if kwargs.get('previous') and kwargs.get('next'):
        raise ValueError("You can't specify both -previous and -next")

    if kwargs.get('before') and kwargs.get('hash'):
        raise ValueError("You can't specify both -before and -hash")

    if kwargs.get('after') and kwargs.get('hash'):
        raise ValueError("You can't specify both -after and -hash")

    if kwargs.get('repo') and kwargs.get('repo_id'):
        raise ValueError("You can't specify both -repo (directory) and -repo_id")

def days_ago(dt_str: str) -> float:
    """ Convert an ISO datetime string to days ago"""
    # Parse the datetime string
    # TODO check why we need to do this.
    dt_str = dt_str.replace("Z","+00:00")
    #print(dt_str)
    dt = datetime.fromisoformat(dt_str)

    # Current datetime in UTC
    now = datetime.now(timezone.utc)

    # Calculate the difference in days
    delta = now - dt
    days_difference = delta.total_seconds() / (24 * 3600)

    # Return the difference rounded to two decimal places
    return round(days_difference, 2)

def days_ago_iso_date(days):
    """ Convert an integer 'days ago' to an ISO datetime string"""
    days_ago = int(days)
    #start_date = (datetime.utcnow() - timedelta(days=days)).isoformat() + 'Z'
    start_date = (datetime.utcnow() - timedelta(days=days_ago)).isoformat()
    return start_date

def find_git_base(filename):
    """
    Find the base Git directory for a given file path.

    :filename: The filename for which to find the Git base directory.
    :return: The path to the base Git directory, or None if not found.
    """
    # Get the absolute path of the file
    file_path = os.path.abspath(filename)

    # Start checking from the directory of the file
    directory = os.path.dirname(file_path)

    while os.path.isdir(directory):
        if os.path.isdir(os.path.join(directory, '.git')):
            # Found the .git directory, return the current directory
            return directory
        # Move up one directory
        parent = os.path.dirname(directory)
        if parent == directory:
            # Reached the root directory without finding .git
            return None
        directory = parent


    return None

def development_status(days, active_limit=90, aging_limit=180, stale_limit=365):
    """Get the development status of a repo based on the number of days since the last commit"""
    if days <= active_limit:
        return "Active"
    elif active_limit < days <= aging_limit:
        return "Aging"
    elif aging_limit < days <= stale_limit:
        return "Stale"
    else:
        return "Unmaintained"

def parse_git_rename_event(event_str):
    """ Parse a git rename event and return the new path"""
    # Split the string into segments based on ' => ' within curly braces
    segments = re.split(r'\{([^}]*)\}', event_str)

    # Process each segment
    for i in range(1, len(segments), 2):
        old, new = segments[i].split(' => ')
        segments[i] = new

    # Reassemble the path
    return ''.join(segments)

def get_last_commit_info(filename):
    """ Get the last commit info for a given file"""
    try:
        # Get the last commit for the file
        commit_info = subprocess.check_output(
            shlex.split(f"git log -1 --pretty=format:'%H|%ad|%cd' --date=iso-strict -- {filename}"),
            encoding='utf-8'
        )

        # Split the output to get commit hash, author date, and committer date
        commit_hash, author_date, committer_date = commit_info.strip().split('|', 2)

        return {
            'commit_hash': commit_hash,
            'author_date': author_date,
            'committer_date': committer_date,
            'days_ago': days_ago(author_date),
            'status': development_status(days_ago(author_date))
        }

    except subprocess.CalledProcessError as e:
        print(f"An error occurred: {e}")
        return None
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        return None

def count_key_occurrences(array_of_dicts, key):
    """ Count the number of occurrences of a key in an array of dictionaries"""
    # This dictionary will store the count of occurrences for each value
    value_counts = {}

    for dictionary in array_of_dicts:
        # Check if the key exists in the dictionary
        if key in dictionary:
            value = dictionary[key]
            # Increment the count for this value
            if value in value_counts:
                value_counts[value] += 1
            else:
                value_counts[value] = 1

    return value_counts


def list_dict_2_csv(list_of_dicts, csv_file, headers=None):
    """ Write a list of dictionaries to a csv file"""
    # Get the keys from the first dictionary
    keys = list_of_dicts[0].keys()
    if headers:
        keys = headers

    # Open the csv file for writing
    with open(csv_file, 'w', newline='') as output_file:
        # Create a csv writer object
        dict_writer = csv.DictWriter(output_file, keys)

        # Write the header row
        dict_writer.writeheader()

        # Write the remaining rows
        dict_writer.writerows(list_of_dicts)

def get_directory_size(directory):
    """
    Get the size of a directory in kilobytes.

    :param directory: Path to the directory
    :return: Size of the directory in kilobytes
    """
    # Running the 'du' command with '-s' (summarize) and '-k' (kilobytes) options
    result = subprocess.run(['du', '-sk', directory], 
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    if result.stderr:
        raise OSError(f"Error in du command: {result.stderr}")

    # The output is in the format "size\t directory\n", we split by tab and get the first element
    size_kb = int(result.stdout.split()[0])
    return size_kb

#git rev-parse HEAD

def get_git_hash(directory):
    """ Get the git hash for a given directory using the 'git rev-parse HEAD' command"""
    return run_git_command(directory,['rev-parse', 'HEAD'])

def get_git_remote_url(directory):
    """ Get the git remote url for a given directory using 
    the 'git remote get-url origin' command"""
    return run_git_command(directory,['remote', 'get-url', 'origin'])

def run_git_command(directory, args):
    """ Generic function to run a git command in a given directory"""
    return subprocess.check_output(['git', '-C', directory] + args).decode().strip()

def get_git_stats(directory, last_days=30):
    """ Return some basicgit stats for a given directory"""
    if not os.path.isdir(os.path.join(directory, '.git')):
        raise ValueError("The specified directory is not a Git repository.")

    #def run_git_command(args):
    #    return subprocess.check_output(['git', '-C', directory] + args).decode().strip()

    # Get first and last commit dates
    #  git log --pretty=format:"%ci" --max-parents=0 HEAD
    #first_commit_date = run_git_command(['log', '--reverse', '--format=%ci', '-1'])
    first_commit_date = run_git_command(directory,['log', '--pretty=format:%ci', '--max-parents=0', 'HEAD'])

    last_commit_date = run_git_command(directory,['log', '--format=%ci', '-1'])

    # Count total number of commits
    total_commits = int(run_git_command(directory,['rev-list', '--count', 'HEAD']))
    # Calculate the total size of the directory and the .git directory

    # Get Git remote URL
    git_remote_url = run_git_command(directory,['remote', 'get-url', 'origin'])

    # Get the long hash
    current_hash = get_git_hash(directory)#,['rev-parse', 'HEAD'])

    # Get Git remote URL
    #git_remote_url = run_git_command(directory,['remote', 'get-url', 'origin'])
    git_remote_url = get_git_remote_url(directory)

    total_size = get_directory_size(directory)

    git_dir_size = get_directory_size(os.path.join(directory, '.git'))

    # Size of the repo without .git data
    repo_size_without_git = total_size - git_dir_size

    # Count total and unique authors (based on their email address)
    authors = run_git_command(directory,['log', '--format=%aE'])
    unique_authors = set(authors.splitlines())
    total_authors = len(unique_authors)

    # Count unique authors in the last X days
    since_date = (datetime.now() - timedelta(days=last_days)).strftime('%Y-%m-%d')
    recent_authors = run_git_command(directory,['log', '--since', since_date, '--format=%aN'])
    unique_recent_authors = len(set(recent_authors.splitlines()))

    return {
        'first_commit': first_commit_date,
        'last_commit': last_commit_date,
        'total_commits': total_commits,
        'hash': current_hash, 
        'remote_url': git_remote_url,
        'total_size': total_size,
        'git_dir_size': git_dir_size,
        'repo_size_without_git': repo_size_without_git,
        'total_authors': total_authors,
        'unique_recent_authors': unique_recent_authors
    }

def parse_sql_create_columns(sql):
    ''' A function to return a dict of column names and types from a SQL CREATE statement
    Regular expression to find column names and types
    It looks for patterns like [column_name] data_type, '''
    pattern = r'\[(.+?)\]\s+(\w+)'

    # Find all matches in the SQL statement
    matches = re.findall(pattern, sql)

    # Create a dictionary with column names and types
    column_types = {column: datatype for column, datatype in matches}

    return column_types

def parse_sql_primary_keys(sql):
    ''' A function to return a list of primary keys from a SQL CREATE statement'''
    # Regular expression to find the primary key definition
    # It looks for the pattern PRIMARY KEY(column1, column2, ...)
    pattern = r'PRIMARY KEY\((.+?)\)'

    # Find the primary key definition in the SQL statement
    match = re.search(pattern, sql)

    # Extract the column names from the primary key definition
    # Splitting by comma to get individual columns
    primary_keys = match.group(1).split(',') if match else []

    # Removing potential whitespace and brackets around column names
    primary_keys = [key.strip().strip('[]') for key in primary_keys]

    return primary_keys

def extract_git_url(url):
    """ Extract the Git URL from a string"""
    # Function to extrac the web Git URL
    # from examples like this:
    # "git+https://example.com/apollographql/react-apollo.git"
    # "git+ssh://git@example.com/palantir/blueprint.git"
    # "git+ssh://git@github.com/palantir/blueprint.git"

    if url is None:
        return None

    # Remove .git at the end
    if url.endswith('.git'):
        url = url[:-4]

    # Remove git+ at the beginning
    if url.startswith('git+'):
        url = url[4:]

    # Change SSH URLs to HTTPS URLs (for any domain)
    ssh_pattern = r'ssh://git@(.*?)/(.+)'
    match = re.match(ssh_pattern, url)
    if match:
        domain = match.group(1)
        repo = match.group(2)
        url = f'https://{domain}/{repo}'

    if url.startswith("git:"):
        url = url.replace("git:", "https:")

    return url
