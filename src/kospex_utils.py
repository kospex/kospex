""" Helper functions for kospex """
import os
import re
import subprocess
import shlex
import base64
import csv
import time
import functools
import logging
from datetime import datetime, timezone, timedelta
from dateutil import parser
from collections import Counter, OrderedDict
from prettytable import PrettyTable
from dotenv import load_dotenv

# Import centralized logging after other imports to handle potential import errors
try:
    from .kospex_logging import get_logger, validate_logging_setup
except ImportError:
    # Try absolute import as fallback
    try:
        from kospex_logging import get_logger, validate_logging_setup
    except ImportError:
        # Final fallback if kospex_logging is not available
        get_logger = None
        validate_logging_setup = None

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

DEFAULT_STATUS_THRESHOLDS = OrderedDict({
    "Single day": 1,
    "< 3 months": 90,
    "< 6 months": 180,
    "< 1 year": 365,
    "< 2 years": 730,
    #"5+ years": 1825
})

DEFAULT_MAX_STATUS = "2+ Years"

def is_git(directory):
    """Simple directory check to see if it's a git repo"""
    git_path = f"{directory}/.git/"
    return os.path.exists(git_path)

def init(create_directories=True, setup_logging=True, verbose=False):
    """
    Initialize the kospex environment with enhanced directory and logging setup.

    Args:
        create_directories: Whether to create directories if they don't exist
        setup_logging: Whether to initialize the centralized logging system
        verbose: Whether to print detailed status information

    Returns:
        dict: Initialization status and validation results
    """
    user_kospex_home = os.path.expanduser("~/kospex")
    kospex_home = os.getenv("KOSPEX_HOME", user_kospex_home)

    # Initialize status tracking
    init_status = {
        "kospex_home": kospex_home,
        "directories_created": [],
        "environment_vars_set": [],
        "logging_status": None,
        "warnings": [],
        "errors": []
    }

    try:
        # Create KOSPEX_HOME directory if needed
        if kospex_home == user_kospex_home and create_directories:
            if not os.path.exists(kospex_home):
                os.makedirs(kospex_home, mode=0o750, exist_ok=True)
                init_status["directories_created"].append(kospex_home)
                if verbose:
                    print(f"✓ Created KOSPEX_HOME: {kospex_home}")

                # Create default environment file
                env_file_path = f"{kospex_home}/kospex.env"
                with open(env_file_path, "w") as env_file:
                    env_file.write(KOSPEX_DEFAULT_ENV)
                if verbose:
                    print(f"✓ Created environment file: {env_file_path}")

        # Set environment variables
        if not os.getenv("KOSPEX_HOME"):
            os.environ["KOSPEX_HOME"] = kospex_home
            init_status["environment_vars_set"].append("KOSPEX_HOME")

        default_kospex_db = f"{kospex_home}/{KOSPEX_DB_FILENAME}"
        os.environ["KOSPEX_DB"] = default_kospex_db
        init_status["environment_vars_set"].append("KOSPEX_DB")

        # Set up configuration
        config_path = f"{kospex_home}/kospex.env"
        os.environ["KOSPEX_CONFIG"] = config_path
        init_status["environment_vars_set"].append("KOSPEX_CONFIG")

        # Load existing configuration
        if os.path.exists(config_path):
            load_config(config_path)

        # Set up code directory
        kospex_code = os.path.expanduser("~/code")
        if os.getenv("KOSPEX_CODE") is None:
            os.environ["KOSPEX_CODE"] = kospex_code
            init_status["environment_vars_set"].append("KOSPEX_CODE")

        # Validate code directory exists
        if not os.path.isdir(os.getenv("KOSPEX_CODE")):
            warning_msg = f"KOSPEX_CODE directory '{kospex_code}' does not exist"
            init_status["warnings"].append(warning_msg)
            if verbose:
                print(f"⚠ WARNING: {warning_msg}")

        # Set up logging directory (legacy support)
        kospex_logs = os.getenv("KOSPEX_LOGS", f"{kospex_home}/logs")
        if os.getenv("KOSPEX_LOGS") is None:
            os.environ["KOSPEX_LOGS"] = kospex_logs
            init_status["environment_vars_set"].append("KOSPEX_LOGS")

        # Create logs directory (will be handled by centralized logging, but maintain compatibility)
        if create_directories and not os.path.exists(kospex_logs):
            os.makedirs(kospex_logs, mode=0o750, exist_ok=True)
            init_status["directories_created"].append(kospex_logs)
            if verbose:
                print(f"✓ Created logs directory: {kospex_logs}")

        # Initialize centralized logging system if available and requested
        if setup_logging and get_logger and validate_logging_setup:
            try:
                # Validate logging setup
                logging_status = validate_logging_setup()
                init_status["logging_status"] = logging_status

                if verbose:
                    if logging_status.get("directories_exist") and logging_status.get("directories_writable"):
                        print("✓ Logging system validated successfully")
                    else:
                        print("⚠ Logging system validation found issues")
                        for error in logging_status.get("errors", []):
                            print(f"  - {error}")
            except Exception as e:
                error_msg = f"Failed to initialize centralized logging: {e}"
                init_status["errors"].append(error_msg)
                if verbose:
                    print(f"⚠ WARNING: {error_msg}")
                    print("  Continuing with legacy logging setup")

        if verbose and not init_status["errors"]:
            print("\n✓ Kospex initialization complete!")

    except Exception as e:
        error_msg = f"Critical error during initialization: {e}"
        init_status["errors"].append(error_msg)
        if verbose:
            print(f"✗ ERROR: {error_msg}")

    return init_status


def get_kospex_logger(module_name='kospex'):
    """
    Get a logger instance for Kospex modules with fallback support.

    Args:
        module_name: Name of the module requesting the logger

    Returns:
        Logger instance (either from centralized system or basic logger)
    """
    if get_logger:
        try:
            return get_logger(module_name)
        except Exception as e:
            # Fallback to basic logging if centralized system fails
            logger = logging.getLogger(module_name)
            if not logger.handlers:
                handler = logging.StreamHandler()
                formatter = logging.Formatter('%(levelname)s: %(message)s')
                handler.setFormatter(formatter)
                logger.addHandler(handler)
                logger.setLevel(logging.INFO)
            logger.warning(f"Using fallback logging due to error: {e}")
            return logger
    else:
        # Basic logger when centralized logging is not available
        logger = logging.getLogger(module_name)
        if not logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter('%(levelname)s: %(message)s')
            handler.setFormatter(formatter)
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)
        return logger


def validate_kospex_setup():
    """
    Comprehensive validation of Kospex environment setup.

    Returns:
        dict: Detailed validation results
    """
    validation = {
        "environment_vars": {},
        "directories": {},
        "logging": None,
        "overall_status": "unknown",
        "recommendations": []
    }

    # Check environment variables
    for var in KOSPEX_CONFIG_ITEMS:
        value = os.getenv(var)
        validation["environment_vars"][var] = {
            "set": value is not None,
            "value": value
        }

    # Check critical directories
    kospex_home = os.getenv("KOSPEX_HOME", os.path.expanduser("~/kospex"))
    kospex_code = os.getenv("KOSPEX_CODE", os.path.expanduser("~/code"))

    validation["directories"]["kospex_home"] = {
        "path": kospex_home,
        "exists": os.path.exists(kospex_home),
        "writable": os.access(kospex_home, os.W_OK) if os.path.exists(kospex_home) else False
    }

    validation["directories"]["kospex_code"] = {
        "path": kospex_code,
        "exists": os.path.exists(kospex_code),
        "readable": os.access(kospex_code, os.R_OK) if os.path.exists(kospex_code) else False
    }

    # Check logging system if available
    if validate_logging_setup:
        try:
            validation["logging"] = validate_logging_setup()
        except Exception as e:
            validation["logging"] = {"error": str(e)}

    # Generate recommendations
    if not validation["directories"]["kospex_home"]["exists"]:
        validation["recommendations"].append("Run 'kospex init --create' to initialize directory structure")

    if not validation["directories"]["kospex_code"]["exists"]:
        validation["recommendations"].append(f"Create code directory: mkdir -p {kospex_code}")

    # Determine overall status
    critical_issues = []
    if not validation["directories"]["kospex_home"]["exists"]:
        critical_issues.append("KOSPEX_HOME missing")
    if not validation["directories"]["kospex_home"]["writable"]:
        critical_issues.append("KOSPEX_HOME not writable")

    if not critical_issues:
        validation["overall_status"] = "healthy"
    else:
        validation["overall_status"] = "issues_found"
        validation["critical_issues"] = critical_issues

    return validation


class KospexTimer:
    """Simple context manager for timing operations."""

    def __init__(self, description="operation"):
        self.description = description
        self.elapsed = None

    def __enter__(self):
        self.start = time.time()
        return self

    def __exit__(self, *args):
        self.elapsed = time.time() - self.start

    def __str__(self):
        return f"{self.description}: {self.elapsed:.3f}s" if self.elapsed else self.description


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
    # TODO - Also check why we sometimes get nulls in github.com~mergestat~mergestat
    if dt_str:
        dt_str = dt_str.replace("Z","+00:00")
    else:
        return 0.0

    dt = None
    try:
        dt = datetime.fromisoformat(dt_str)
    except ValueError as e:
        # Get a logger for this utility function
        logger = get_kospex_logger('kospex_utils')
        logger.error(f"Error parsing date '{dt_str}': {e}")
        # Try to do something else
        if "T" in dt_str:
            dt_str = dt_str.split("T")[0]
            dt = datetime.fromisoformat(dt_str).astimezone(timezone.utc)
        return None

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
    except ValueError as e:
        # Get a logger for this utility function
        logger = get_kospex_logger('kospex_utils')
        logger.error(f"Error calculating difference between '{datetime1}' and '{datetime2}': {e}")
        return 0

    # Return the difference rounded to one decimal place
    days = round(difference, 1)
    if min_one:
        return max(days, 1)
    return days

def find_git_base(filename):
    """
    Find the base Git directory for a given file path or directory.

    :filename: The filename for which to find the Git base directory.
    :return: The path to the base Git directory, or None if not found.
    """
    # Get the absolute path of the file
    file_path = os.path.abspath(filename)

    # Start checking from the directory of the file
    directory = os.path.dirname(file_path)
    if os.path.isdir(filename):
        directory = filename

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
    """
    Parse a repo ID into its components
    """
    # TODO - Make this work with Gitlab URLs which have slashes
    # required for web requests

    if not repo_id:
        return None
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

def parse_org_key(org_key):
    """
    Parse an org_key into its components
    """
    parts = []
    if org_key:
        parts = org_key.split('~')
    else:
        return None

    if len(parts) != 2:
        return None

    return {
        'git_server': parts[0],
        'org': parts[1],
        'org_key': f"{org_key}",
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
        basefile_quoted = shlex.quote(basefile)

        last_cmd = f"git log -1 --pretty=format:'%H|%ad|%cd' --date=iso-strict -- {basefile_quoted}"
        if is_dir:
            last_cmd = "git log -1 --pretty=format:'%H|%ad|%cd' --date=iso-strict"

        commit_info = subprocess.check_output(
            shlex.split(last_cmd),
            encoding='utf-8'
        )

        remote = get_git_remote_url(file_dir)
        if remote:
            # remove the .git extension if present
            remote = remote.removesuffix('.git')

        # Check we actually have a git result
        if not commit_info:
            default['repo'] = remote
            # Set a flag for unmanaged
            default['unmanaged'] = True
            # TODO - fix to debug logging
            print(f"{filename} does not appear to be managed by git.")
            return default

        # If we're here, we must have gotten a git result

        # Split the output to get commit hash, author date, and committer date
        commit_hash, author_date, committer_date = commit_info.strip().split('|', 2)

        os.chdir(original_dir)

        return {
            'file_path': filename,
            'author_when': author_date,
            'committer_when': committer_date,
            'days_ago': days_ago(author_date),
            'status': development_status(days_ago(author_date)),
            'repo': remote,
            'commit_hash': commit_hash
        }

    except subprocess.CalledProcessError as e:
        # Get a logger for this utility function
        logger = get_kospex_logger('kospex_utils')
        logger.error(f"Git command failed for {filename}: {e}")
    except Exception as e:
        logger = get_kospex_logger('kospex_utils')
        logger.error(f"Unexpected error for {filename}: {e} (potentially not managed by git)")
        default['error'] = "Potentially not managed by git"
        default['unmanaged'] = True
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
        # Get a logger for this utility function
        logger = get_kospex_logger('kospex_utils')
        logger.error(f"Git command failed in {directory}: {e}")
        logger.debug(f"Directory {directory} is probably not a git repo or is empty but initialized")

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
               "first_commit", "active_commits", "% active", "tenure"]
    table.field_names = headers
    table.align["author"] = "l"
    table.align["commits"] = "r"
    table.align["last_commit"] = "r"
    table.align["first_commit"] = "r"
    table.align["active_commits"] = "r"
    table.align["% commits"] = "r"
    table.align["% active"] = "r"
    table.align["tenure"] = "r"

    return table

def file_metadata_prettytable():
    """ Return a prettytable object for the file metadata table.
        file_path, Language, tags
    """
    table = PrettyTable()
    headers = ["Filename", "Type", "Tags"]
    table.field_names = headers
    table.align["Filename"] = "l"
    table.align["Type"] = "l"
    table.align["Tags"] = "l"

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
        logger = get_kospex_logger('kospex_utils')
        logger.error(message)
        print(f"ERROR: {message}")  # Keep CLI output for user
        if exit_required:
            exit(1)

def get_scc_build_effort(directory):
    """
    Run the 'scc' command and capture the output relating to
    the estimated cost, schedule, and people required.
    """

    try:
        result = subprocess.run(['scc', directory], capture_output=True, text=True, check=True)
        output = result.stdout
    except subprocess.CalledProcessError as e:
        logger = get_kospex_logger('kospex_utils')
        logger.error(f"scc command failed: {e}")
        return None

    # Define a dictionary to store the parsed results
    results = {}

    # Define the regular expressions for parsing the output
    cost_pattern = r"Estimated Cost to Develop \(organic\) \$(\d+,\d+|\d+)"
    schedule_pattern = r"Estimated Schedule Effort \(organic\) ([\d.]+) months"
    people_pattern = r"Estimated People Required \(organic\) ([\d.]+)"

    cost_match = re.search(cost_pattern, output)
    schedule_match = re.search(schedule_pattern, output)
    people_match = re.search(people_pattern, output)

    # If matches are found, convert them to float and store them in the dictionary
    if cost_match:
        results['cost'] = float(cost_match.group(1).replace(',', ''))
    if schedule_match:
        results['schedule'] = float(schedule_match.group(1))
    if people_match:
        results['people'] = float(people_match.group(1))

    return results

def parse_mailmap(file_path):
    """
    Parse a .mailmap file and return a list of Dict with
    proper_name
    proper_email
    commit_email
    commit_name
    Depending on what was included in the file
    """
    result = []
    with open(file_path, 'r') as file:
        for line in file:

            line = line.strip()

            if line and not line.startswith('#'):
                parts = line.split('<')
                entry = {}

                if len(parts) == 2:
                    # Example: Proper Name <proper@email.com>
                    entry['proper_name'] = parts[0].strip()
                    entry['proper_email'] = parts[1].rstrip('>').strip()
                elif len(parts) == 3:
                    # Example: Proper Name <proper@email.com> <commit@email.com>
                    # Example: Proper Name <proper@email.com> Commit Name <commit@email.com>
                    entry['proper_name'] = parts[0].strip()
                    entry['proper_email'] = parts[1].rstrip('>').strip()

                    email, name = parts[1].split('>')
                    entry['proper_email'] = email
                    name = name.lstrip().rstrip()

                    if name:
                        entry['commit_name'] = name
                    entry['commit_email'] = parts[2].rstrip('>').strip()

                else:
                    logger = get_kospex_logger('kospex_utils')
                    logger.warning(f"Parser error for mailmap line: {line}")
                    print(f"WARNING parser error for line: {line}")  # Keep CLI output for user

                if entry:
                    result.append(entry)

    return result

def is_base64(s):
    # Check if the string matches the Base64 pattern
    pattern = r'^[A-Za-z0-9+/]*={0,2}$'
    if not re.match(pattern, s):
        return False

    # Check if the length is valid (multiple of 4)
    if len(s) % 4 != 0:
        return False

    # Try to decode the string
    try:
        base64.b64decode(s)
        return True
    except:
        return False

def decode_base64(data):
    """
    Decode a base64 string and return the value
    or None if there's an error
    """
    decoded = None

    try:
        base64_bytes = data.encode('ascii')
        message_bytes = base64.b64decode(base64_bytes)
        decoded = message_bytes.decode('ascii')
        return decoded
    except:
        return None

def encode_base64(data):
    """
    Encode a base64 string and return the value
    or None if there's an error
    """
    encoded = None

    try:
        b64_bytes = base64.b64encode(data.encode("utf-8"))
        encoded = b64_bytes.decode('utf-8')
        return encoded
    except:
        return None



def get_status(days: int, thresholds: OrderedDict = None) -> str:
    """
    Determine status based on the number of days and configured thresholds.

    Args:
        days: Integer representing number of days
        thresholds: OrderedDict with status labels as keys and day thresholds as values

    Returns:
        String representing the status
    """
    if thresholds is None:
        # thresholds = OrderedDict({
        #     "Single day": 1,
        #     "< 3 months": 90,
        #     "< 6 months": 180,
        #     "< 1 year": 365,
        #     "< 2 years": 730,
        #     "5+ years": 1825
        # })
        thresholds = DEFAULT_STATUS_THRESHOLDS

    # Handle case where days is less than the first threshold
    if days <= list(thresholds.values())[0]:
        return list(thresholds.keys())[0]

    # Iterate through thresholds to find the correct status
    prev_threshold = 0
    for status, threshold in thresholds.items():
        if prev_threshold < days <= threshold:
            return status
        prev_threshold = threshold

    # If days is greater than all thresholds, return the last status
    #return list(thresholds.keys())[-1]
    return "2+ Years"

def get_status_distribution(data):
    """
    Take a list of Dicts, and calculate the percentages based on the
    DEFAULT_STATUS_THRESHOLDS categories
    Return an Order list of thresholds like
    "Single day" : 45.2
    """

    tenure_status = [entry['tenure_status'] for entry in data]

    # Count occurrences of each status
    status_counts = Counter(tenure_status)
    total_count = len(tenure_status)

    # Calculate percentages
    distribution = OrderedDict()
    all_thresholds = DEFAULT_STATUS_THRESHOLDS

    # TODO - See what our max threshold should be if any
    all_thresholds[DEFAULT_MAX_STATUS] = 20000

    for threshold in all_thresholds:
        count = status_counts[threshold]
        if count:
            distribution[threshold] = round((count / total_count) * 100, 2)
        else:
            distribution[threshold] = 0

    return distribution


def timer(logger=None, level=logging.INFO):
    """
    Configurable timer decorator for debugging function performance.

    Args:
        logger: Logger instance to use for output (optional, defaults to print)
        level: Logging level to use (default: logging.INFO)

    Returns:
        Decorator function

    Usage:
        @timer()  # Uses print for output
        def my_function():
            pass

        @timer(logger=logging.getLogger(__name__))
        def my_function():
            pass

        @timer(logger=logging.getLogger(__name__), level=logging.DEBUG)
        def my_function():
            pass
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            start = time.perf_counter()
            result = func(*args, **kwargs)
            end = time.perf_counter()
            duration = end - start
            message = f"{func.__name__} executed in {duration:.6f}s"
            if logger:
                logger.log(level, message)
            else:
                print(message)
            return result
        return wrapper
    return decorator


def filenames_by_repo_id(json_data):
    """
    Analyzes a JSON array and returns filename statistics.

    Args:
        json_data (list): List of dictionaries containing file information

    Returns:
        list: List of tuples containing (filename, occurrence_count, unique_repo_count)
    """
    filename_stats = {}

    for item in json_data:
        filename = item.get('Filename')
        repo_id = item.get('_repo_id')

        if filename is None:
            continue

        if filename not in filename_stats:
            filename_stats[filename] = {
                'count': 0,
                'repo_ids': set()
            }

        filename_stats[filename]['count'] += 1

        if repo_id is not None:
            filename_stats[filename]['repo_ids'].add(repo_id)

    # Convert to list of tuples
    result = []
    for filename, stats in filename_stats.items():
        result.append({
                    'Filename': filename,
                    'number': stats['count'],
                    'repos': len(stats['repo_ids'])
                })

    # Sort by filename for consistent output
    result.sort(key=lambda x: x['Filename'])

    return result
