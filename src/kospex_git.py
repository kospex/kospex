"""Functions extract git metadata from a repo directory such as remote, hash, etc. """
import os
import re
from pathlib import Path
#import pygit2
import kospex_utils as KospexUtils


class KospexGit:
    """Git metadata class for kospex"""
    def __init__(self):

        # Git variables
        # We are specifically using pygit2 to get metadata about the remotes, org, repo, etc.
        self.pygit2 = None
        self.repo_dir = ""
        # remote should be either HTTP, HTTPS or SSH
        self.remote_type  = ""
        self.remote_url   = ""
        self.remote       = ""
        self.org          = ""
        self.repo         = ""
        self.current_hash = ""
        # REPO_ID is going to be a simplified version of the remote URL
        # E.g. github.com~owner~repo
        self.repo_id      = ""
        self.has_head     = False

    def is_git_repo(self, repo_dir):
        """Simple diretory check to see if directory is a git repo"""
        git_path = f"{repo_dir}/.git/"
        return os.path.exists(git_path)

    def extract_git_url_parts(self, url):
        """
        Extracts the domain name, organization, and repository name from a given URL.

        Args:
        url (str): The URL to extract information from.

        Returns:
        dict: A dictionary containing the remote, org, repo, and remote_type.
        """
        # Regular expression to match the default pattern
        pattern = r'^(https?|git|ssh)\:\/\/(?:[\w.-]+@)?([\w.-]+)\/([\w.-]+)\/([\w.-]+)(?:\.git)?$'
        match = re.match(pattern, url)
        # There are some Go libraries which use a different URL format from Google
        # https://go.googlesource.com/oauth2
        g_pattern = r"(?P<protocol>^\w+)://(?P<domain>[^\/?#]+)/(?P<directory>.*)"
        google_match = re.match(g_pattern,url)

        #gitlab_pattern = r"(?P<protocol>^https?://)" \
        gitlab_pattern = r"(?P<protocol>^\w+)://" \
          r"(?P<hostname>[^/]+)" \
          r"(?P<directories>(?:/[^/]+)*?)/" \
          r"(?P<last_part>[^/]+)$"

        gitlab_match = re.match(gitlab_pattern, url)

        slashes_count = url.count("/")
        # Looks like github URLS have 4 slashes,
        # gitlab URLs have more than 4 slashes (more like 5 or 6 for subprojects),
        # Google/Go URLs have 3 slashes
        # TODO - check SSH URLs

        if slashes_count > 4 and gitlab_match:
            return {
                "remote": gitlab_match.group("hostname"),
                "org": gitlab_match.group("directories").removeprefix("/"),
                "repo": gitlab_match.group("last_part").removesuffix(".git"),
                "remote_type": gitlab_match.group("protocol")
            }
        elif match:
            return {
                "remote": match.group(2),
                "org": match.group(3),
                "repo": match.group(4).removesuffix(".git"),
                "remote_type": match.group(1)
            }
        elif google_match:
            return {
                "remote": google_match.group("domain"),
                "org": "",
                "repo": google_match.group("directory").removesuffix(".git"),
                "remote_type": google_match.group("protocol")
            }
        else:
            return None

    def repo_id_from_url_parts(self, parts):
        """Create a simplified repo_id from the parts"""
        return f"{parts['remote']}~{parts['org']}~{parts['repo']}"

    def set_remote_url(self, remote_url):
        """ Set the remote URL and extract the remote, org, repo, etc.
        We're going to use Remote URL formats as described in
        https://docs.github.com/en/get-started/getting-started-with-git/about-remote-repositories
        Example expected URLs are:
        https://github.com/user/repo.git or
        git@github.com:user/repo.git
        """
        self.remote_url = remote_url

        parts = None
        if remote_url:
            parts = self.extract_git_url_parts(self.remote_url)
        else:
            # TODO - add logging
            # This situation should not really happen
            print("No remote URL provided")
            return None

        if parts:
            self.remote = parts["remote"]
            self.org = parts["org"]
            self.repo = parts["repo"]
            self.remote_type = parts["remote_type"]
        elif "@" in self.remote_url :
            # TODO - better data validation checking
            ssh_parts = self.remote_url.split("/")
            self.repo = ssh_parts[1].removesuffix(".git")
            self.org = ssh_parts[0].split(":")[1]
            self.remote_type = "SSH"
        else:
            # Assuming it's a HTTP/S remote
            # TODO - better data validation checking
            # TODO - we have the better function above, this code may be redundant
            url_parts =  self.remote_url.split("/")
            self.remote = url_parts[2]
            self.org = url_parts[3]
            self.repo = url_parts[4].removesuffix(".git")
            self.remote_type = "HTTPS"

        # Set the repo ID
        self.repo_id = f"{self.remote}~{self.org}~{self.repo}"

    def set_repo(self, repo_dir):
        """ Extract the git metadata (remote, hash) from the repo directory """
        #self.pygit2 = pygit2.Repository(repo_dir)
        self.repo_dir = repo_dir # Expecting this as a full path

        # Get the current hash
        try:
            #self.current_hash = self.pygit2[self.pygit2.head.target].hex
            self.current_hash = KospexUtils.get_git_hash(repo_dir)
            self.has_head = True
        except Exception:
            print(f"No 'HEAD' for {repo_dir}")
            self.current_hash = "NO_HEAD"
        # If we don't have a head, it's probably a new repo without any commits
        # The following getting of origin remote still works on a new repo with no commits
        #self.set_remote_url(self.pygit2.remotes["origin"].url)
        self.set_remote_url(KospexUtils.get_git_remote_url(repo_dir))


    def get_current_hash(self):
        """ return the current git hash """
        return self.current_hash

    def add_git_to_dict(self,row_dict):
        """
        We're going to a add the GIT details (REMOTE, ORG, REPO) to the dict and return it
        """
        row_dict["_git_server"] = self.remote
        row_dict["_git_owner"] = self.org
        row_dict["_git_repo"] = self.repo
        row_dict["_repo_id"] = self.repo_id

        return row_dict

    def get_remote_url(self):
        """ return the URL as per the Git 'origin' remote"""
        return self.remote_url

    def get_repo_id(self):
        """ return the repo ID (e.g. github.com~owner~repo)"""
        return self.repo_id

    def get_repo_files(self):
        """ return a list of files in the repo, excluding .git """
        repo_files = {}
        repo_path = Path(self.repo_dir).resolve()

        for root, dirs, files in os.walk(repo_path):
            # Skip .git directory
            if '.git' in dirs:
                dirs.remove('.git')

            for file in files:
                #full_path = Path(root) / file
                data = {}
                data['Location'] = file
                self.add_git_to_dict(data)
                data['hash'] = self.current_hash
                #repo_files.append(file)
                repo_files[file] = data

        return repo_files

    def clone_repo(self, repo_url):
        """ Clone a repo to the kospex code directory """
        #pygit2.clone_repository(repo_url, repo_dir)
        #KospexUtils.clone_git_repo(repo_url, repo_dir)
        repo_dir = os.getenv("KOSPEX_CODE")
        if not os.path.isdir(repo_dir):
            exit("KOSPEX_CODE directory not found: " + repo_dir)

        current_dir = os.getcwd()

        os.chdir(repo_dir)
        parts = self.extract_git_url_parts(repo_url)
        remote_org_dir = None
        print(parts)
        if parts:
            remote_org_dir = f"{parts['remote']}/{parts['org']}"
        else:
            # TODO - add logging
            return None

        if not os.path.isdir(remote_org_dir):
            print("Creating directory: " + remote_org_dir)
            os.makedirs(remote_org_dir)
        else:
            print("Directory exists: " + remote_org_dir)

        os.chdir(remote_org_dir)
        org_dir = os.getcwd()
        repo_path = os.path.join(org_dir, parts['repo'])
        print("Current directory: " + os.getcwd())

        status = 0

        if os.path.isdir(parts['repo']):
            print("Repo exists: " + parts['repo'])
            print("Trying pulling latest changes instead ...")
            os.chdir(parts['repo'])
            status = os.system("git pull")
        else:
            print("Cloning repo: " + repo_url)
            status = os.system(f"git clone {repo_url}")

        if status != 0:
            print("Error cloning or pulling repo: " + repo_url)
            repo_path = None

        os.chdir(current_dir)

        return repo_path


class MissingGitDirectory(Exception):
    """ Exception for missing git directory """
    pass
