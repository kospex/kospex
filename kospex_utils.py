""" Helper functions for kospex """
import os
import glob
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
