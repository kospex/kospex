"""Functions extract git metadata from a repo directory such as remote, hash, etc."""

import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import panopticas as Panopticas
from prettytable import PrettyTable

import kospex_schema as KospexSchema
import kospex_utils as KospexUtils
from kospex_observation import Observation
from kospex_query import KospexQuery


class KospexGit:
    """Git metadata class for kospex"""

    def __init__(self):
        # Git variables
        self.repo_dir = ""
        # remote should be either HTTP, HTTPS or SSH
        self.remote_type = ""
        self.remote_url = ""
        self.remote = ""
        self.org = ""
        self.repo = ""
        self.current_hash = ""
        self.repo_files = {}  # Store information about files
        # REPO_ID is going to be a simplified version of the remote URL
        # E.g. github.com~owner~repo
        self.repo_id = ""
        self.has_head = False

    def is_git_repo(self, repo_dir):
        """Simple directory check to see if directory is a git repo"""
        git_path = f"{repo_dir}/.git/"
        return os.path.exists(git_path)

    @staticmethod
    def parse_ssh_git_url(url):
        pattern = r"git@(?P<remote>[^:]+):(?P<org>[\w-]+(?:/[\w-]+)*)/(?P<repo>[\w-]+)(?:\.git)?"
        match = re.match(pattern, url)

        if match:
            details = match.groupdict()
            details["remote_type"] = "ssh"
            return details
        else:
            return None

    @staticmethod
    def get_repos_pretty_table(repos=None):
        """
        Get a default repos pretty table
        If a list of repos is provided, it will be used to populate the table.
        """
        table = PrettyTable()
        table.field_names = [
            "name",
            "type",
            "fork",
            "visibility",
            "owner",
            "clone_url",
            "updated_at",
            "status",
        ]
        table.align["name"] = "l"
        table.align["clone_url"] = "l"
        table.align["status"] = "l"

        if repos:
            for repo in repos:
                if updated_at := repo.get("updated_at"):
                    days_ago = KospexUtils.days_ago(str(updated_at))
                    repo["status"] = KospexUtils.development_status(days_ago)
                table.add_row([repo.get(field, None) for field in table.field_names])

        return table

    @staticmethod
    def parse_ado_git_url(clone_url):
        """
        Parse Azure DevOps clone URL and return server, org, and repo.

        Args:
            clone_url (str): Azure DevOps clone URL

        Returns:
            dict: Contains 'server', 'org', and 'repo' keys
            or
            None: if URL format is not recognized as Azure DevOps or visual studio team services

        """
        # Remove .git suffix if present
        clean_url = clone_url.rstrip(".git")

        # Parse the URL
        parsed = urlparse(clean_url)

        # Check if it's a dev.azure.com URL
        if parsed.netloc == "dev.azure.com":
            # Format: https://dev.azure.com/{organization}/{project}/_git/{repository}
            path_parts = parsed.path.strip("/").split("/")

            if len(path_parts) >= 4 and path_parts[2] == "_git":
                organization = path_parts[0]
                project = path_parts[1]
                repository = "/".join(path_parts[3:])  # Handle repos with slashes in name

                return {
                    "remote": parsed.netloc,
                    "org": f"{organization}-{project}",
                    "project": project,
                    "repo": repository,
                    "remote_type": parsed.scheme,
                }

        # Check if it's a legacy visualstudio.com URL
        elif parsed.netloc.endswith(".visualstudio.com"):
            # Format: https://{organization}.visualstudio.com/{project}/_git/{repository}
            # For visualstudio.com, we'll use the project as the "org" since domain includes organisation
            # which makes it unique
            path_parts = parsed.path.strip("/").split("/")

            if len(path_parts) >= 3 and path_parts[1] == "_git":
                project = path_parts[0]
                repository = "/".join(path_parts[2:])  # Handle repos with slashes in name

                return {
                    "remote": parsed.netloc,
                    "org": project,  # Use project as org for visualstudio.com
                    "project": project,
                    "repo": repository,
                    "remote_type": parsed.scheme,
                }

        # If we got here, nothing parsed, so not a valid ADO or visualstudio.com URL
        return None

    @staticmethod
    def parse_bitbucket_onpremise_url(clone_url):
        """
        Parse a Bitbucket on-premise/datacenter clone URL and extract components.

        Args:
            clone_url (str): The git clone URL to parse

        Returns:
            dict or None: Dictionary with 'remote', 'org', 'repo', 'remote_type' keys
                        if valid Bitbucket on-premise URL, otherwise None
        """
        if not clone_url or not isinstance(clone_url, str):
            return None

        # Parse the URL
        try:
            parsed = urlparse(clone_url.strip())
        except Exception:
            return None

        # Get scheme and clean up netloc for SSH URLs
        scheme = parsed.scheme.lower()
        netloc = parsed.netloc

        # For SSH URLs, remove 'git@' prefix from netloc if present
        if scheme == "ssh" and netloc.startswith("git@"):
            domain = netloc[4:].lower()  # Remove 'git@'
        else:
            domain = netloc.lower()

        # Check if domain exists
        if not domain:
            return None

        # Exclude public hosting services
        excluded_domains = ["github.com", "gitlab.com", "bitbucket.org"]
        if any(excluded in domain for excluded in excluded_domains):
            return None

        # Check if path starts with /scm/
        path = parsed.path
        if not path.startswith("/scm/"):
            return None

        # Extract the path after /scm/
        scm_path = path[5:]  # Remove '/scm/' prefix

        # Split path into components, filtering out empty strings
        path_parts = [part for part in scm_path.split("/") if part]

        # Need at least 2 parts: org and repo
        if len(path_parts) < 2:
            return None

        org = path_parts[0]
        repo_with_git = path_parts[1]

        # Remove .git suffix if present
        repo = repo_with_git[:-4] if repo_with_git.endswith(".git") else repo_with_git

        return {"remote": domain, "org": org, "repo": repo, "remote_type": scheme}

    @staticmethod
    def parse_git_remote(url):
        """
        Extracts the domain name, organisation/user/team, and repository name from a given URL.

        Given the org and workspace are in the URL, we'll concatenate them to form the 'org' in our schema


        Args:
        url (str): The URL to extract information from.

        Returns:
        dict: A dictionary containing the remote, org, repo, and remote_type.
        """
        # Regular expression to match the default pattern
        pattern = r"^(https?|git|ssh)\:\/\/(?:[\w.-]+@)?([\w.-]+)\/([\w.-]+)\/([\w.-]+)(?:\.git)?$"
        match = re.match(pattern, url)
        # There are some Go libraries which use a different URL format from Google
        # https://go.googlesource.com/oauth2
        g_pattern = r"(?P<protocol>^\w+)://(?P<domain>[^\/?#]+)/(?P<directory>.*)"
        google_match = re.match(g_pattern, url)

        # gitlab_pattern = r"(?P<protocol>^https?://)" \
        gitlab_pattern = (
            r"(?P<protocol>^\w+)://"
            r"(?P<hostname>[^/]+)"
            r"(?P<directories>(?:/[^/]+)*?)/"
            r"(?P<last_part>[^/]+)$"
        )

        gitlab_match = re.match(gitlab_pattern, url)

        ssh_git = KospexGit.parse_ssh_git_url(url)

        slashes_count = url.count("/")
        # Looks like github URLS have 4 slashes,
        # gitlab URLs have more than 4 slashes (more like 5 or 6 for subprojects),
        # Google/Go URLs have 3 slashes

        ado_repo = KospexGit.parse_ado_git_url(url)
        if ado_repo:
            return ado_repo

        bitbucket_onpremise = KospexGit.parse_bitbucket_onpremise_url(url)
        if bitbucket_onpremise:
            return bitbucket_onpremise

        # Check SSH URLs first
        if ssh_git:
            return ssh_git

        elif slashes_count > 4 and gitlab_match:
            return {
                "remote": gitlab_match.group("hostname"),
                "org": gitlab_match.group("directories").removeprefix("/"),
                "repo": gitlab_match.group("last_part").removesuffix(".git"),
                "remote_type": gitlab_match.group("protocol"),
            }
        elif match:
            return {
                "remote": match.group(2),
                "org": match.group(3),
                "repo": match.group(4).removesuffix(".git"),
                "remote_type": match.group(1),
            }
        elif google_match:
            return {
                "remote": google_match.group("domain"),
                "org": "",
                "repo": google_match.group("directory").removesuffix(".git"),
                "remote_type": google_match.group("protocol"),
            }
        else:
            return None

    # @staticmethod
    # def get_repo_size(directory):

    #     results = {}

    #     result_git = subprocess.run(
    #         ['du', '-s', str(directory)],
    #         capture_output=True,
    #         text=True,
    #         check=True,
    #         timeout=300
    #     )
    #     results["total"] = int(result_git.stdout.split('\t')[0])
    #     result_git = subprocess.run(
    #         ['du', '-s', str(f"{directory}/.git")],
    #         capture_output=True,
    #         text=True,
    #         check=True,
    #         timeout=300
    #     )
    #     results[".git"] = int(result_git.stdout.split('\t')[0])
    #     results["working"] = results["total"] - results[".git"]

    #     return results

    @staticmethod
    def get_repo_size(directory=None):
        """
        Get disk usage information for a git repository.

        Args:
            directory (str, optional): Directory path to analyze.
                                     Uses current directory if None.

        Returns:
            dict: Dictionary containing:
                - total: Total disk usage of directory in bytes
                - git: Disk usage of .git directory in bytes
                - workspace: Workspace disk usage (total - git) in bytes

        Raises:
            ValueError: If directory is not a git repository
            subprocess.CalledProcessError: If du command fails
        """
        if directory is None:
            directory = os.getcwd()

        # Create KospexGit instance to use is_git_repo method
        kgit = KospexGit()
        if not kgit.is_git_repo(directory):
            raise ValueError(f"Directory {directory} is not a git repository")

        results = {}

        # Get total directory size
        result_total = subprocess.run(
            ["du", "-sk", str(directory)],
            capture_output=True,
            text=True,
            check=True,
            timeout=300,
        )
        results["total"] = int(result_total.stdout.split("\t")[0])

        # Get .git directory size
        git_dir = os.path.join(directory, ".git")
        result_git = subprocess.run(
            ["du", "-sk", git_dir],
            capture_output=True,
            text=True,
            check=True,
            timeout=300,
        )
        results["git"] = int(result_git.stdout.split("\t")[0])

        # Calculate workspace size (total - git)
        results["workspace"] = results["total"] - results["git"]

        return results

    @staticmethod
    def get_branches(directory):
        """
        Retrieves the branches of a Git repository.

        Args:
        directory (str): The path to the Git repository.

        Returns:
        list: A list of branch names.
        """
        original_directory = os.getcwd()
        os.chdir(directory)
        # Run git command to get remote branches
        result = subprocess.run(["git", "branch", "-r"], capture_output=True, text=True, check=True)

        # Parse the output
        remote_branches = []
        for line in result.stdout.strip().split("\n"):
            line = line.strip()
            if line and not line.startswith("origin/HEAD"):
                # Remove 'origin/' prefix and any leading/trailing whitespace
                if line.startswith("origin/"):
                    branch_name = line[7:]  # Remove 'origin/' (7 characters)
                    remote_branches.append(branch_name)
                else:
                    # Handle other remotes (not just origin)
                    if "/" in line:
                        remote_branches.append(line.split("/", 1)[1])

        os.chdir(original_directory)

        return remote_branches

    def extract_git_url_parts(self, url):
        """
        Extracts the domain name, organization, and repository name from a given URL.

        Args:
        url (str): The URL to extract information from.

        Returns:
        dict: A dictionary containing the remote, org, repo, and remote_type.
        """
        # Regular expression to match the default pattern
        pattern = r"^(https?|git|ssh)\:\/\/(?:[\w.-]+@)?([\w.-]+)\/([\w.-]+)\/([\w.-]+)(?:\.git)?$"
        match = re.match(pattern, url)
        # There are some Go libraries which use a different URL format from Google
        # https://go.googlesource.com/oauth2
        g_pattern = r"(?P<protocol>^\w+)://(?P<domain>[^\/?#]+)/(?P<directory>.*)"
        google_match = re.match(g_pattern, url)

        # gitlab_pattern = r"(?P<protocol>^https?://)" \
        gitlab_pattern = (
            r"(?P<protocol>^\w+)://"
            r"(?P<hostname>[^/]+)"
            r"(?P<directories>(?:/[^/]+)*?)/"
            r"(?P<last_part>[^/]+)$"
        )

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
                "remote_type": gitlab_match.group("protocol"),
            }
        elif match:
            return {
                "remote": match.group(2),
                "org": match.group(3),
                "repo": match.group(4).removesuffix(".git"),
                "remote_type": match.group(1),
            }
        elif google_match:
            return {
                "remote": google_match.group("domain"),
                "org": "",
                "repo": google_match.group("directory").removesuffix(".git"),
                "remote_type": google_match.group("protocol"),
            }
        else:
            return None

    @staticmethod
    def generate_repo_id(remote, org, repo):
        """
        A static method to generate a repo_id
        some git orgs have slashes, which we'll replace
        with a double ~~ for parsing and use in web URLs
        The general format for a repo_id is
        remote~org~repo
        """
        repo_id = f"{remote}~"
        repo_id += org.replace("/", "~~")
        repo_id += f"~{repo}"

        return repo_id

    def repo_id_from_url_parts(self, parts):
        """Create a simplified repo_id from the parts"""
        org = parts["org"]
        return f"{parts['remote']}~{parts['org']}~{parts['repo']}"

    def set_remote_url(self, remote_url):
        """
        Set the remote URL and extract the remote, org, repo, etc.
        We're going to use Remote URL formats as described in
        https://docs.github.com/en/get-started/getting-started-with-git/about-remote-repositories
        Example expected URLs are:
        https://github.com/user/repo.git or
        git@github.com:user/repo.git
        """
        self.remote_url = remote_url

        parts = None
        if remote_url:
            parts = self.parse_git_remote(self.remote_url)

        else:
            # TODO - add logging
            # This situation should not really happen
            return None

        if parts:
            self.remote = parts["remote"]
            self.org = parts["org"]
            self.repo = parts["repo"]
            self.remote_type = parts["remote_type"]
        else:
            print("WARNING: Failed specific parsing of remote URL")
            # Assuming it's a HTTP/S remote
            # TODO - better data validation checking
            # TODO - we have the better function above, this code may be redundant
            url_parts = self.remote_url.split("/")
            self.remote = url_parts[2]
            self.org = url_parts[3]
            self.repo = url_parts[4].removesuffix(".git")
            self.remote_type = "HTTPS"

        # Set the repo ID
        # self.repo_id = f"{self.remote}~{self.org}~{self.repo}"
        self.repo_id = self.generate_repo_id(self.remote, self.org, self.repo)

    def set_repo(self, repo_dir):
        """Extract the git metadata (remote, hash) from the repo directory"""
        self.repo_dir = repo_dir  # Expecting this as a full path

        # Get the current hash
        try:
            self.current_hash = KospexUtils.get_git_hash(repo_dir)
            self.has_head = True
        except Exception:
            print(f"No 'HEAD' for {repo_dir}")
            self.current_hash = "NO_HEAD"
        # If we don't have a head, it's probably a new repo without any commits
        # The following getting of origin remote still works on a new repo with no commits
        self.set_remote_url(KospexUtils.get_git_remote_url(repo_dir))

    def get_current_hash(self):
        """return the current git hash"""
        return self.current_hash

    def add_git_to_dict(self, row_dict):
        """
        We're going to a add the GIT details (REMOTE, ORG, REPO) to the dict and return it
        """
        row_dict["_git_server"] = self.remote
        row_dict["_git_owner"] = self.org
        row_dict["_git_repo"] = self.repo
        row_dict["_repo_id"] = self.repo_id

        return row_dict

    def get_remote_url(self):
        """return the URL as per the Git 'origin' remote"""
        return self.remote_url

    def get_repo_id(self):
        """return the repo ID (e.g. github.com~owner~repo)"""
        return self.repo_id

    def get_repo_files(self, language=None, skip_last_commit=None):
        """return a list of files in the repo, excluding .git"""
        repo_files = {}
        repo_path = Path(self.repo_dir).resolve()
        p_files = Panopticas.identify_files(repo_path)

        unmanaged = 0

        for entry in p_files:
            data = {}
            self.add_git_to_dict(data)
            data["hash"] = self.current_hash

            data["Language"] = p_files[entry]
            data["Location"] = entry
            data["Filename"] = os.path.basename(entry)

            tags = Panopticas.get_filename_metatypes(entry)
            data["tech_type"] = tags

            # This process is very CPU intensitve, so might skip it for big repos
            git_metadata = {}
            if skip_last_commit:
                data["committer_when"] = None
                data["status"] = None
                repo_files[entry] = data
            else:
                git_metadata = KospexUtils.get_last_commit_info(entry)
                data["committer_when"] = git_metadata.get("committer_when")
                data["status"] = git_metadata.get("status")

                if git_metadata.get("unmanaged"):
                    unmanaged += 1
                else:
                    repo_files[entry] = data

        self.repo_files = repo_files

        if language:
            language_files = {}
            for item in repo_files:
                if repo_files[item].get("Language") == language:
                    language_files[item] = repo_files[item]

            return language_files

        else:
            return repo_files

    def new_observation(self, observation_key, observation_type=None):
        """
        Create a template observation for the current repo.
        Prerequisites: KospexGit object initialized with the current repo
        """
        obs = Observation(self.current_hash, self.repo_dir, self.repo_id, observation_key)

        if observation_type:
            obs.observation_type = observation_type

        obs.update_from_dict(self.add_git_to_dict({}))

        return obs

    def clone_repo(self, repo_url):
        """Clone a repo to the kospex code directory"""
        repo_dir = os.getenv("KOSPEX_CODE")
        if not os.path.isdir(repo_dir):
            exit("KOSPEX_CODE directory not found: " + repo_dir)

        current_dir = os.getcwd()
        os.chdir(repo_dir)

        # Need to strip a trailing slash if it's there
        # https://github.com/kospex/kospex/
        repo_url = repo_url.rstrip("/")
        # the trailing slash messes with the parsing

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
        repo_path = os.path.join(org_dir, parts["repo"])
        print("Current directory: " + os.getcwd())

        status = 0

        if os.path.isdir(parts["repo"]):
            print("Repo exists: " + parts["repo"])
            print("Trying pulling latest changes instead ...")
            os.chdir(parts["repo"])
            status = os.system("git pull")
        else:
            print("Cloning repo: " + repo_url)
            status = os.system(f"git clone {repo_url}")

        if status != 0:
            print("Error cloning or pulling repo: " + repo_url)
            repo_path = None

        os.chdir(current_dir)

        return repo_path

    def get_latest_commit_datetime(self, repo_id):
        """Get the latest commit datetime for the given repo_id"""
        cursor = KospexQuery().kospex_db.execute(
            "SELECT MAX(committer_when) FROM commits WHERE _repo_id = ?", (repo_id,)
        )
        latest_datetime = cursor.fetchone()[0]
        return latest_datetime

    def sync_repo(self, directory=None, limit=None, from_date=None, to_date=None, no_scc=None):
        """
        Sync the commit data (authors, commmitters, files, etc) for the given directory
        This is a work in progress refactor from kospex_core.py sync_repo
        """
        results = []

        use_scc = True
        if no_scc:
            use_scc = False
        # See at the bottom for file metadata

        self.set_repo_dir(directory)

        # If we don't have a from date from the use, get the last commit date from DB
        if not from_date:
            latest_datetime = self.get_latest_commit_datetime(self.git.get_repo_id())
            if latest_datetime:
                from_date = latest_datetime

        # Use hash (#) as a delimeter as names can contain spaces
        cmd = ["git", "log", "--pretty=format:%H#%aI#%cI#%aN#%aE#%cN#%cE", "--numstat"]

        if from_date and to_date:
            cmd += ["--since={}".format(from_date), "--until={}".format(to_date)]
            print(f"Syncing commits from {from_date} to {to_date}...")
        elif from_date:
            cmd += ["--since={}".format(from_date)]
            # print("Syncing commits from {}...".format(from_date))
            print(f"Syncing commits from {from_date}...")
        elif limit:
            cmd += ["-n", str(limit)]
            print(f"Syncing {limit} commits...")
        else:
            print("Syncing all commits...")

        result = subprocess.run(cmd, capture_output=True, text=True).stdout.split("\n")

        commits = []
        commit = {}

        for line in result:
            if line:
                if "\t" in line and len(line.split("\t")) == 3:
                    # Check if the line represents file stats
                    additions, deletions, filename = line.split("\t")
                    if "filenames" in commit:
                        # The following checks for git rename events which change the filename
                        # With a git rename event, the filename will be in the format of
                        # old_filename => new_filename
                        if "=>" in filename:
                            fpath = KospexUtils.parse_git_rename_event(filename)
                            path_change = filename
                        else:
                            fpath = filename
                            path_change = None

                        commit["filenames"].append(
                            {
                                "file_path": fpath,
                                "path_change": path_change,
                                "additions": int(additions) if additions != "-" else 0,
                                "deletions": int(deletions) if deletions != "-" else 0,
                            }
                        )
                elif "#" in line:
                    if commit:  # Save the previous commit
                        commits.append(commit)

                    (
                        hash_value,
                        author_datetime,
                        committer_datetime,
                        author_name,
                        author_email,
                        committer_name,
                        committer_email,
                    ) = line.split("#", 6)

                    commit = {
                        "hash": hash_value,
                        "author_when": author_datetime,
                        "committer_when": committer_datetime,
                        "author_name": author_name,
                        "author_email": author_email,
                        "committer_name": committer_name,
                        "committer_email": committer_email,
                        "filenames": [],
                    }

                else:
                    commit["filenames"].append({"filename": line, "additions": 0, "deletions": 0})

            else:
                if commit:  # Save the last commit
                    commits.append(commit)
                commit = {}

        # cursor = conn.cursor()

        counter = 0
        print("About to insert commits into the database...")

        # Insert the commits to the database
        for commit in commits:
            counter += 1
            # Insert the commit to the database
            files = len(commit["filenames"])
            commit["_files"] = files
            commit = self.git.add_git_to_dict(commit)
            commit_files = commit["filenames"]
            # Need to copy as we
            results.append(commit.copy())
            del commit["filenames"]
            self.kospex_db.table(KospexSchema.TBL_COMMITS).upsert(commit, pk=["_repo_id", "hash"])

            # Insert the filenames to the database
            for file_info in commit_files:
                file_info = self.git.add_git_to_dict(file_info)
                file_info["hash"] = commit["hash"]
                file_info["_ext"] = KospexUtils.get_extension(file_info["file_path"])
                file_info["committer_when"] = commit["committer_when"]
                self.kospex_db.table(KospexSchema.TBL_COMMIT_FILES).upsert(
                    file_info, pk=["file_path", "_repo_id", "hash"]
                )

            # we'll print a + for each commit and a newline every 80 commits
            print("+", end="")
            if (counter % 80) == 0:
                print()
            if (counter % 500) == 0:
                print(f"\nSynced {counter} commits so far ...\n")

        print()
        print(f"Synced {len(commits)} total commits")

        # Update the repos table with the last sync time
        last_sync = datetime.now(timezone.utc).astimezone().replace(microsecond=0).isoformat()
        self.update_repo_status(last_sync=last_sync)

        print("Processing file metadata...")

        # We should process the metadata after the commits, so we can query the last date time from the database
        # if not no_scc:
        if use_scc:
            metadata_files = self.file_metadata(directory, skip_last_commit=True)

        self.chdir_original()

        return results


class MissingGitDirectory(Exception):
    """Exception for missing git directory"""

    pass
