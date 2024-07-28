""" Helper functions for kospex """
import os
import re
import subprocess
import shlex
import csv
from datetime import datetime, timezone, timedelta
from dateutil import parser
from prettytable import PrettyTable
from dotenv import load_dotenv

KOSPEX_DB_FILENAME="kospex.db"

KOSPEX_DEFAULT_ENV = """
#KOSPEX_CODE=ENTER_YOUR_CODE_DIRECTORY
"""

KOSPEX_CONFIG_ITEMS = [
    "KOSPEX_CODE",
    "KOSPEX_LOGS",
    "KOSPEX_DB",
    "KOSPEX_HOME",
    "KOSPEX_DB",
    "KOSPEX_CONFIG"
]

def is_git(directory):
    """Simple directory check to see if it's a git repo"""
    git_path = f"{directory}/.git/"
    return os.path.exists(git_path)

def init():
    """ Initialize the kospex environment """
    user_kospex_home = os.path.expanduser("~/kospex")
    kospex_home = os.getenv("KOSPEX_HOME",user_kospex_home)

    # TODO check if this equality misses something, or needs removing
    if kospex_home == user_kospex_home:
        if not os.path.exists(kospex_home):
            os.mkdir(kospex_home)
            with open(f"{kospex_home}/kospex.env","w") as env_file:
                env_file.write(KOSPEX_DEFAULT_ENV)

    if not os.getenv("KOSPEX_HOME"):
        os.environ["KOSPEX_HOME"] = kospex_home

    default_kospex_db = f"{kospex_home}/{KOSPEX_DB_FILENAME}"
    os.environ["KOSPEX_DB"] = default_kospex_db

    # Check if the KOSPEX_CONFIG is set, if not set it to the default
    os.environ["KOSPEX_CONFIG"] = f"{kospex_home}/kospex.env"
    load_config(f"{kospex_home}/kospex.env")

    # Set a default kospex code directory
    kospex_code = os.path.expanduser("~/code")

    # Set up some basic around the kospex code where all the repos live
    if os.getenv("KOSPEX_CODE") is None:
        os.environ["KOSPEX_CODE"] = kospex_code

    if not os.path.isdir(os.getenv("KOSPEX_CODE")):
        print(f"WARNING: KOSPEX_CODE directory '{kospex_code} does not exist!")

    kospex_logs = os.getenv("KOSPEX_LOGS",f"{kospex_home}/logs")
    # Set up the logging directory
    if os.getenv("KOSPEX_LOGS") is None:
        os.environ["KOSPEX_LOGS"] = f"{kospex_home}/logs"

    # Create the logs directory if it doesn't exist
    if not os.path.exists(kospex_logs):
        print(f"Creating logs directory: {kospex_logs}")
        os.mkdir(kospex_logs)

def get_all_config():
    """ Get the kospex config """
    details = {}
    for item in KOSPEX_CONFIG_ITEMS:
        details[item] = os.getenv(item)
    return details

def get_kospex_config():
    """ Get the kospex config """
    return os.getenv("KOSPEX_CONFIG",os.path.expanduser("~/kospex/kospex.env"))

def get_kospex_code_path():
    """ Get the kospex code directory """
    return os.getenv("KOSPEX_CODE",os.path.expanduser("~/code"))

def get_kospex_db_path():
    """ Get the kospex database """

    kospex_path = os.path.expanduser("~/kospex/")
    kospex_home = os.getenv("KOSPEX_HOME",kospex_path)

    kospex_home = kospex_home.rstrip("/") # Removing trailing slash if it's there

    # TODO - Check if we need to create the directory, as we're doing it in init
    if not os.path.exists(kospex_home):
        os.makedirs(kospex_home)

    default_kospex_db = f"{kospex_home}/{KOSPEX_DB_FILENAME}"



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

    #results = glob.glob(directory + "/**/.git", recursive=True)
    #for file in results:
    #    git_dir = file.replace("/.git", "")
    #    repos.append(git_dir)

    for root, dirs, files in os.walk(directory):
        if '.git' in dirs:
            #repos.append(os.path.join(root, '.git'))
            repos.append(root)

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

def date_days_ago(given_date, num_days):
    """
    This function takes a date and a number of days as input and returns the date 
    going back the specified number of days.
    """

    # Convert the given date to a datetime object
    start_date = datetime.fromisoformat(given_date)

    # Calculate the number of days to go back
    days_to_go_back = timedelta(days=num_days)

    # Subtract the number of days from the start date to get the new date
    new_date = start_date - days_to_go_back

    # Convert the new date back to a string in ISO format
    return new_date.isoformat()

def generate_date_ranges(to_date, days_apart, previous_days):
    """
    This function calulcalates a set of dates,
    with the to_date and the the days_apart
    being the number of days between a from and to date (e.g 7 days for a week)
    and the previous_days being the number of days to go back from the to_date
    Eg. to_date = "2024-01-07", days_apart = 5, previous_days = 365
    would return the following date ranges:
    [
        {
            'from_date': '2023-01-02T00:00:00',
            'to_date': '2023-01-07T00:00:00',
        }
        {
            'from_date': '2024-01-02T00:00:00',
            'to_date': '2024-01-07T00:00:00',
        },

    ]
    So we have the current year and the previous year
    """
    # TODO - Think about how to handle timezone, these functions are ignorant of that
    # Parse the to_date to a datetime object
    current_to_date = datetime.fromisoformat(to_date)
    # Calculate the start date for the first range
    from_date = current_to_date - timedelta(days=days_apart)

    date_format = '%Y-%m-%dT%H:%M:%S%z'

    # List to hold the date ranges
    date_ranges = []

    older_to_date = current_to_date - timedelta(days=previous_days)
    older_from_date = older_to_date - timedelta(days=days_apart)

    recent_range = {
            'from_date': from_date.strftime(date_format),
            'to_date': current_to_date.strftime(date_format),
        }

    previous_range = {
            'from_date': older_from_date.strftime(date_format),
            'to_date': older_to_date.strftime(date_format),
    }

    # Add the oldest date range first
    date_ranges.append(previous_range)
    # Add the most recent date range
    date_ranges.append(recent_range)

    return date_ranges

def days_between_datetimes(datetime1: str, datetime2: str, min_one=None) -> float:
    """
    Calculate the difference in days between two ISO 8601 formatted datetime strings.
    If min_one is set to True, the function will return at least one day
      if the difference is less than one day.
    """
    # Parse the ISO 8601 formatted datetime strings
    d1 = parser.parse(datetime1)
    d2 = parser.parse(datetime2)

    # Calculate the absolute difference in days
    try:
        difference = abs((d2 - d1).total_seconds() / 86400)  # Convert seconds to days
    except ValueError:
        print(f"Error calculating the difference between {datetime1} and {datetime2}")
        return None

    # Return the difference rounded to one decimal place
    days = round(difference, 1)
    if min_one:
        return max(days, 1)
    return days

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

    if days is None:
        return "Unknown"

    if isinstance(days, str):
        days = days_ago(days)

    if days <= active_limit:
        return "Active"
    elif active_limit < days <= aging_limit:
        return "Aging"
    elif aging_limit < days <= stale_limit:
        return "Stale"
    else:
        return "Unmaintained"

def get_development_status_options():
    """Get the development status options"""
    return ["Active", "Aging", "Stale", "Unmaintained"]

def extract_git_rename_paths(s):
    """ Extract all the 'path => path' git rename occurrences from the input string"""
    # Regular expression pattern to match multiple 'path => path' occurrences
    pattern = r'{[^{]* => [^}]*}'

    # Find all non-overlapping matches in the input string
    matches = re.findall(pattern, s)

    # Return the list of matched parts
    return matches

def extract_git_rename_values(event_str):
    """ split on => and return the old and new values"""
    # Check if the string contains '=>'
    if '=>' not in event_str:
        return None
    # Remove the curly braces
    event_str = event_str.replace('}', '')
    event_str = event_str.replace('{', '')
    #print(event_str)
    return event_str.split(' => ')

def parse_git_rename_event(event_str):
    """ Parse a git rename event and return the new path"""
    # Split the string into segments based on ' => ' within curly braces
    #segments = re.split(r'\{([^}]*)\}', event_str)
    #segments = re.split(r'{[^{]* => [^}]*}', event_str)

    renames = extract_git_rename_paths(event_str)
    for rename in renames:
        old, new = extract_git_rename_values(rename)
        event_str = event_str.replace(rename, new)

    return event_str

#def parse_git_rename_event(event_str):
#    """ Parse a git rename event and return the new path"""
    # Split the string into segments based on ' => ' within curly braces
    #segments = re.split(r'\{([^}]*)\}', event_str)
#    segments = re.split(r'{[^{]* => [^}]*}', event_str)

    # Process each segment
#    for i in range(1, len(segments), 2):
#        print(segments[i])
#        old, new = segments[i].split(' => ')
#        segments[i] = new

    # Reassemble the path
#    return ''.join(segments)

def git_url_to_repo_id(git_url):
    """ Convert a git URL to a unique repo ID"""
    # Remove the .git extension
    git_url = git_url.replace('.git', '')

    # Remove the protocol and username
    git_url = re.sub(r'^https?://', '', git_url)
    git_url = re.sub(r'^git@', '', git_url)
    git_url = re.sub(r'[^/]+@', '', git_url)

    # Remove the trailing slash
    git_url = git_url.rstrip('/')

    return git_url

def parse_repo_id(repo_id):
    """ Parse a repo ID into its components"""
    parts = repo_id.split('~')
    if len(parts) != 3:
        return None
    return {
        'git_server': parts[0],
        'org': parts[1],
        'repo': parts[2],
        'repo_id': repo_id,
        'org_key': f"{parts[0]}~{parts[1]}",
    }

def get_last_commit_info(filename,remote=None):
    """ Get the last commit info for a given file."""

    # TODO: check if remote = false, and then
    # We won't run the git remote data

    is_dir = False

    if os.path.isdir(filename):
        is_dir = True

    original_dir = os.getcwd()

    abs_path = os.path.abspath(filename)
    basefile = os.path.basename(abs_path)
    if is_dir:
        file_dir = abs_path
    else:
        file_dir = os.path.dirname(abs_path)

    default = { 'file_path': filename }

    # TODO: Check if the file is in a git repo

    try:
        # Get the last commit for the file
        os.chdir(file_dir)
        last_cmd = f"git log -1 --pretty=format:'%H|%ad|%cd' --date=iso-strict -- {basefile}"
        if is_dir:
            last_cmd = "git log -1 --pretty=format:'%H|%ad|%cd' --date=iso-strict"

        commit_info = subprocess.check_output(
            shlex.split(last_cmd),
            encoding='utf-8'
        )
        # Split the output to get commit hash, author date, and committer date
        commit_hash, author_date, committer_date = commit_info.strip().split('|', 2)

        remote = get_git_remote_url(file_dir)
        if remote:
            # remove the .git extension if present
            remote = remote.removesuffix('.git')

        os.chdir(original_dir)

        return {
            'file_path': filename,
            'author_date': author_date,
            'committer_date': committer_date,
            'days_ago': days_ago(author_date),
            'status': development_status(days_ago(author_date)),
            'repo': remote,
            'commit_hash': commit_hash
        }

    except subprocess.CalledProcessError as e:
        print(f"An error occurred: {e}")
    except Exception as e:
        print(f"An unexpected error occurred: {e} for {filename}, potentially not managed by git.")
        default['error'] = "Potentially not managed by git"
    finally:
        os.chdir(original_dir)

    return default

def get_all_last_commit_info(file_list):
    """ Get the last commit info for a list of files"""

    records = []

    for f in file_list:
        #details = { "file_path": f }
        details = get_last_commit_info(f)
        if details and details.get("repo"):
            details["repo"] = extract_git_url(details["repo"])
        else:
            print(f"Error getting last commit information for {f}")

        records.append(details)

    return records

def get_git_metadata(file_list):
    """ Get the last commit info for a list of files"""

    records = []

    for f in file_list:
        #details = { "file_path": f }
        details = get_last_commit_info(f)

        if details and details.get("repo"):
            details["repo"] = extract_git_url(details["repo"])
        else:
            print(f"Error getting last commit information for {f}")

        records.append(details)

    return records

def init_repo_stats():
    """ Initialize the repo stats dictionary"""
    stats = {}
    for status in get_development_status_options():
        stats[status] = 0

    return stats

def repo_stats(records, fieldname):
    """ Get the stats for a given field in a list of records"""
    # Get the list of values for the given field
    stats = init_repo_stats()
    #for status in get_development_status_options():
    #    stats[status] = 0

    for item in records:
        lookup = item.get(fieldname)
        if lookup:
            last_seen = days_ago(lookup)
            status = development_status(last_seen)
            stats[status] += 1
        #last_seen = days_ago(item.get(fieldname))
        #status = development_status(last_seen)
        #stats[status] += 1

    return stats

def add_status(record,status):
    """
    Add the status to the count in the record.
    """
    if status in get_development_status_options():
        record[status] += 1
    else:
        print(f"Invalid status: {status}")

    return record

def get_repo_stats_table(stats=None, fieldname=None):
    """
    Return a Pretty Table for the repo stats.
    Should have the columns: repo = {Active: #, Aging: #, Stale: #, Unmaintained: #}
    fieldname will usually be "repo" or "Repo Status", but can pass in anything
    """
    if not fieldname:
        fieldname = "Repo"
    table = PrettyTable()
    table.field_names = [fieldname, "Active", "Aging", "Stale", "Unmaintained"]
    table.align[fieldname] = "l"
    table.align["Active"] = "r"
    table.align["Aging"] = "r"
    table.align["Stale"] = "r"
    table.align["Unmaintained"] = "r"

    if stats:
        for repo, status in stats.items():
            table.add_row([repo, status["Active"], status["Aging"],
                           status["Stale"], status["Unmaintained"]])

    return table

def get_dependency_files_table(list_of_commit_info, images=None):
    """ Take a list of commit info requests and return a Pretty Table """

    table = PrettyTable()
    if images:
        table.field_names = ["base_image", "type", "File path", "Author Date", "Committer Date",
                         "Days Ago", "Status", "Repo", "Repo Status"]
        table.align["base_image"] = "l"
        table.align["type"] = "l"

    else:
        table.field_names = ["File path", "Author Date", "Committer Date",
                         "Days Ago", "Status", "Repo", "Repo Status"]

    table.align["File path"] = "l"
    table.align["Repo"] = "l"
    table.align["Repo Status"] = "l"
    table.align["Status"] = "l"
    table.align["Days Ago"] = "r"

    for commit_info in list_of_commit_info:

        table_row = []
        if images:
            table_row.append(commit_info['base_image'])
            table_row.append(commit_info['type'])

        table_row.extend((
            commit_info.get('file_path'),
            commit_info.get('author_date'),
            commit_info.get('committer_date'),
            commit_info.get('days_ago'),
            commit_info.get('status'),
            commit_info.get('repo'),
            commit_info.get('repo_status'),
        ))

        table.add_row(table_row)

    return table

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
    with open(csv_file, 'w', newline='', encoding='utf-8') as output_file:
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
    result = None
    #return subprocess.check_output(['git', '-C', directory] + args).decode().strip()
    try:
        result = subprocess.check_output(['git', '-C', directory] + args).decode().strip()
    except subprocess.CalledProcessError as e:
        print(f"An error occurred: {e}.")
        print(f"Probably {directory} is NOT a repo or is empty, but initiliased.\n")

    return result

def get_git_stats(directory, last_days=None):
    """ Return some basic git stats for a given directory"""
    if not os.path.isdir(os.path.join(directory, '.git')):
        raise ValueError("The specified directory is not a Git repository.")

    if not last_days:
        last_days = 90

    #def run_git_command(args):
    #    return subprocess.check_output(['git', '-C', directory] + args).decode().strip()

    # Get first and last commit dates
    #  git log --pretty=format:"%ci" --max-parents=0 HEAD
    #first_commit_date = run_git_command(['log', '--reverse', '--format=%ci', '-1'])
    first_commit_date = run_git_command(directory,['log',
                                                   '--pretty=format:%cI',
                                                   '--max-parents=0', 'HEAD'])

    last_commit_date = run_git_command(directory,['log', '--format=%cI', '-1'])

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

    status = development_status(last_commit_date)

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
        'unique_recent_authors': unique_recent_authors,
        'status': status
    }

def get_first_commit_date(directory):
    """ Get the first commit date for a given directory"""
    first_commit_date = ""
    try:
        first_commit_date = run_git_command(directory,['log', '--pretty=format:%cI',
                                                       '--max-parents=0', 'HEAD'])
    finally:
        pass

    return first_commit_date

def get_last_commit_date(directory):
    """ Get the first commit date for a given directory"""
    last_commit_date = ""
    try:
        last_commit_date = run_git_command(directory,['log', '--format=%cI', '-1'])

    finally:
        pass

    return last_commit_date


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
    # Function to extract the web Git URL
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

    # if we have a https https://user@example.com/org/repo
    if "@" in url:
        parts = url.split('@')
        if len(parts) == 2:
            # If a username is found, reconstruct the URL without it
            url = 'https://' + parts[1]

    if url.startswith("git:"):
        url = url.replace("git:", "https:")

    return url

def merge_dicts(dict1, dict2):
    merged_dict = {}

    # Add all keys from dict1 with their values under 'recent'
    for key in dict1:
        merged_dict[key] = {'recent': dict1[key]}

    # Add/Update keys from dict2 with their values under 'previous'
    for key in dict2:
        if key in merged_dict:
            merged_dict[key]['previous'] = dict2[key]
        else:
            merged_dict[key] = {'previous': dict2[key]}

    # Ensuring that each key has both 'recent' and 'previous' entries
    for key in merged_dict:
        merged_dict[key].setdefault('recent', None)
        merged_dict[key].setdefault('previous', None)

    return merged_dict

def get_values_by_keys(my_dict, my_list):
    """
    Take a dictionary and a list of strings as input.
    Return a list of values from the dictionary corresponding to the keys in the list.
    If a key is not found in the dictionary, an empty string is returned.

    Args:
        my_dict: A dictionary.
        my_list: A list of strings.

    Returns:
        A list of values from the dictionary corresponding to the keys in the list.
    """

    results = []
    for key in my_list:
        results.append(my_dict.get(key, ""))  # Use get() to handle missing keys gracefully

    return results

def extract_db_date(date_str):
    """ Extract a date from a string which often is returned by the database."""
    # Function to extract a date from a string
    # Where it looks like 2014-04-03T15:06:24-06:00 or 2014-04-03 15:06:24-06:00
    # We are expecting YYYY-MM-DD
    # It looks for patterns like "2021-01-01" or "2021/01/01"
    pattern = r'(\d{4}[-/]\d{2}[-/]\d{2})'

    if "T" in date_str:
        date_str = date_str.split("T")[0]

    # Check it looks like the pattern above
    match = re.search(pattern, date_str)

    if match:
        return date_str
    else:
        return None

def key_person_prettytable():
    """ Return a prettytable object for the key_persons table."""

    table = PrettyTable()
    headers = ["author", "commits", "% commits", "last_commit",
               "first_commit", "active_commits", "% active"]
    table.field_names = headers
    table.align["author"] = "l"
    table.align["commits"] = "r"
    table.align["last_commit"] = "r"
    table.align["first_commit"] = "r"
    table.align["active_commits"] = "r"
    table.align["% commits"] = "r"
    table.align["% active"] = "r"

    return table

def get_krunner_directory():
    """ Return the krunner directory. """

    user_kospex_home = os.path.expanduser("~/kospex")
    kospex_home = os.getenv("KOSPEX_HOME",user_kospex_home)
    krunner_path = f"{kospex_home}/krunner"

    return krunner_path

def orgs_prettytable():
    """ Return a prettytable object for the orgs query."""

    table = PrettyTable()
    table.field_names = ["org_key", "org", "commits", "repos", "authors",
                         "committers", "days_ago", "last_commit"]
    table.align["org_key"] = "l"
    table.align["org"] = "l"
    table.align["commits"] = "r"
    table.align["repos"] = "r"
    table.align["authors"] = "r"
    table.align["committers"] = "r"
    table.align["days_ago"] = "r"
    table.align["last_commit"] = "r"

    return table

def get_keyvalue_table(details=None):
    """ Return a prettytable object for the keyvalue query."""

    table = PrettyTable()
    table.field_names = ["key", "value"]
    table.align["key"] = "l"
    table.align["value"] = "l"

    if details:
        for key, value in details.items():
            table.add_row([key, value])

    return table

def convert_to_percentage(data):
    """
    Take a dictionary with numerical values as input,
    calculate the total and return a dict with the same keys 
    and their percentage value of the total
    """
    # Calculate the total sum of all values in the dictionary
    total = sum(data.values())

    # Create a new dictionary where each value is the percentage of the total
    percentage_data = {}
    if total and total > 0:
        for key, value in data.items():
            percentage = (value / total) * 100
            percentage_data[key] = round(percentage, 2)

    return percentage_data

def get_status_table(status):
    """
    Return a prettytable object for the status results ("Active", "Aging", "Stale", "Unmaintained").
    The raw numbers are shown in the first row and the percentages in the second row.
    """

    total = sum(status.values())
    status_percentage = convert_to_percentage(status)
    # Need to run convert first, or the generic function
    # will include the percentage values in the calculation
    status["Total"] = total

    status_percentage["Total"] = 100

    table = PrettyTable()
    table.field_names = ["Active", "Aging", "Stale", "Unmaintained", "Total"]
    values = [status.get(key,0) for key in table.field_names]

    table.add_row(values)

    status_percentage["Total"] = 100
    values = [ f"{status_percentage.get(key,0)}%" for key in table.field_names]
    table.add_row(values)

    return table

def orphan_prettytable():
    """ Return a prettytable object for showing orphaned repos."""

    table = PrettyTable()
    headers = ["_repo_id", "committers", "active", "Orphaned", "% Here"]
    table.field_names = headers
    table.align["_repo_id"] = "l"
    table.align["committers"] = "r"
    table.align["active"] = "r"
    table.align["Orphaned"] = "l"
    table.align["% Here"] = "r"

    return table

def parse_docker_image(image):
    """
    Take a Docker image and return a Dict with the keys
    registry (default to docker.io), namespace, image_name and tag.
    """

    registry = "docker.io"
    namespace = None
    image_name = None
    tag = ""

    if not "/" in image:
    # No slash, so no namespace or registry
    # E.g. postgres:14
    # or python

        if ":" in image:
            image_name, tag = image.split(":")
        else:
            image_name = image

    else:
        # Handle namespace and/or registry
        # We have a slash if we're here
        element, image_name = image.split("/", 1)

        # Once we've split, if we see a dot, it's probably a registry
        # E.g. ghcr.io
        # TODO - think how to check for URLS, this should still work it if has a .
        # but MAY miss some cases like https://Registry:8000
        if "." in element:
            registry = element
        else:
            namespace = element

        if "/" in image_name:
            #if namespace:
            #    print("WARNING: already had a namespace, overwriting")
            # TODO - Log if we already had a namespace
            namespace, image_name = image_name.split("/", 1)

        if ":" in image_name:
            image_name, tag = image_name.split(":")

    return {
            "registry": registry,
            "namespace": namespace,
            "image": image_name,
            "tag": tag,
            "raw": image
        }


def validate_only_one(params, message, exit_required=None):
    """
    Validate that one of the parameters is set.
    """
    # Only set the exit_required flag if it's not already set
    # an it's NOT False
    # E.g. if it's False, we don't want to override it and exit
    if exit_required is None:
        exit_required = True

    if sum(params) != 1:
        print(f"ERROR: {message}")
        if exit_required:
            exit(1)
