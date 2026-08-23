"""Functions extract git metadata from a repo directory such as remote, hash, etc."""

import os
import re
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import panopticas as Panopticas
from prettytable import PrettyTable

import kospex_utils as KospexUtils
from kospex.habitat_config import HabitatConfig
from kospex_observation import Observation
from kospex_query import KospexQuery

log = KospexUtils.get_kospex_logger("kospex_git")


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
        # Anchored at both ends so a junk suffix is a rejection, not a silent
        # truncation. Dots are legal in orgs and repo names (e.g. dashboard.js);
        # each segment must start with a word char so ".git", ".." and "-org"
        # can never become directory names.
        pattern = r"^git@(?P<remote>[\w.-]+):(?P<org>\w[\w.-]*(?:/\w[\w.-]*)*)/(?P<repo>\w[\w.-]*?)(?:\.git)?$"
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
        dict: A dictionary containing the remote, org, repo, and remote_type,
        or None if the URL is not a recognisable git remote.
        """
        if not url or not isinstance(url, str):
            return None

        # Normalise scheme-based URLs before any provider rule sees them.
        # urlparse().hostname drops embedded credentials and the port for
        # free -- without this, 'ssh://git@host:7999/PROJ/repo.git' keeps
        # 'git@host:7999' as the server, so the same repository cloned over
        # SSH and HTTPS produces two different repo_ids.
        # scp-style ('git@host:org/repo.git') has no '://' and is handled
        # further down by parse_ssh_git_url, so leave it untouched.
        if "://" in url:
            try:
                _p = urlparse(url.strip())
            except ValueError:
                return None
            if _p.scheme.lower() not in ("http", "https", "ssh", "git"):
                return None  # not a git transport
            if not _p.hostname:
                return None
            if _p.username or _p.port:
                url = f"{_p.scheme}://{_p.hostname}{_p.path}"

        # Regular expression to match the default pattern
        pattern = r"^(https?|git|ssh)\:\/\/(?:[\w.-]+@)?([\w.-]+)\/([\w.-]+)\/([\w.-]+)(?:\.git)?$"
        match = re.match(pattern, url)
        # Gerrit hosts under *.googlesource.com (go., chromium., android.,
        # boringssl., ...) allow a *single-segment* project path, which has no
        # org: https://go.googlesource.com/oauth2
        # Multi-segment paths there need no special case -- the generic nested
        # rule below already encodes them correctly.
        # This pattern is deliberately host-scoped. It used to be
        # '(?P<domain>[^\/?#]+)/(?P<directory>.*)', which matched any
        # scheme://host/path and made parse_git_remote incapable of ever
        # returning None -- so junk was silently given a plausible repo_id
        # instead of being rejected.
        g_pattern = (
            r"(?P<protocol>^https?)://"
            r"(?P<domain>[\w.-]+\.googlesource\.com)/"
            r"(?P<directory>[\w.-]+?)(?:\.git)?/?$"
        )
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
        # Run git *in* the directory rather than chdir'ing the process into it.
        # A missing directory or a failing git command used to leave the process
        # cwd stranded inside (or pointing at) the repo, breaking later commands.
        result = subprocess.run(
            ["git", "branch", "-r"], cwd=directory, capture_output=True, text=True, check=True
        )

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

        return remote_branches

    def extract_git_url_parts(self, url):
        """
        Deprecated: use parse_git_remote().

        Retained so existing callers keep working. parse_git_remote() is the
        single source of truth and additionally handles scp-style SSH, Azure
        DevOps and on-premise Bitbucket URLs. Collapsing the remaining parsers
        is tracked in kospex#94.

        Args:
        url (str): The URL to extract information from.

        Returns:
        dict: A dictionary containing the remote, org, repo, and remote_type,
        or None if the URL could not be parsed.
        """
        return self.parse_git_remote(url)

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

    @staticmethod
    def _unquote_git_path(path):
        """Undo git's C-style path quoting.

        With core.quotePath=false git only quotes control characters, double
        quotes and backslashes, so every escape is ASCII and the round trip
        through latin-1 restores the original UTF-8 bytes.
        """
        if not (path.startswith('"') and path.endswith('"') and len(path) > 1):
            return path

        try:
            return (path[1:-1]
                    .encode("utf-8")
                    .decode("unicode_escape")
                    .encode("latin-1")
                    .decode("utf-8"))
        except (UnicodeDecodeError, UnicodeEncodeError):
            return path

    def _last_commit_by_path(self):
        """Every tracked path's most recent commit, from a single 'git log' walk.

        Returns {path: {"commit_hash", "author_when", "committer_when"}}.

        This replaces a 'git log -1 -- <file>' per file. The per-file cost was
        fork/exec overhead rather than git work, so it scaled with the file
        count and dominated a file_metadata rebuild. One walk is 15-217x faster
        depending on repo size, and does not degrade with history depth.

        Two flags carry the correctness:

        --diff-merges=combined lists, for a merge commit, the files that differ
        from *every* parent. That matches what path-limited 'git log' history
        simplification shows, so content which only ever existed in a conflict
        resolution is found (git's default shows no diff at all for merges),
        while files the merge merely forwarded from the branch stay with the
        commit that actually changed them (which --diff-merges=first-parent
        would wrongly reattribute to the merge).

        core.quotePath=false stops git emitting "caf\\303\\251.py" for
        non-ASCII paths, which would never match the path panopticas walked.

        git log is newest first, so the first commit to mention a path is that
        path's last commit.

        Known divergence from the per-file version, measured at 0-1.3% of paths
        across react/babel/pydantic/kospex: path-limited 'git log' simplifies
        history and prunes a branch whose changes to that path did not survive
        the merge (e.g. an import that was reverted on the branch before it
        landed). An unlimited walk still sees those commits, so such a path gets
        the abandoned commit rather than the one that set its current content.
        Both are commits that really touched the path; the walk's is newer.
        """
        if not self.repo_dir:
            return {}

        cmd = [
            "git", "-c", "core.quotePath=false", "log",
            "--name-only", "--diff-merges=combined",
            "--pretty=format:%x01%H|%ad|%cd", "--date=iso-strict",
        ]

        try:
            out = subprocess.check_output(
                cmd,
                cwd=str(Path(self.repo_dir).resolve()),
                text=True,
                stderr=subprocess.DEVNULL,
            )
        except (subprocess.CalledProcessError, OSError) as exc:
            # No HEAD (a repo without commits) or not a git directory at all.
            log.debug("git log walk failed for %s: %s", self.repo_dir, exc)
            return {}

        last = {}
        current = None

        for line in out.splitlines():
            if line.startswith("\x01"):
                commit_hash, author_when, committer_when = line[1:].split("|", 2)
                current = {
                    "commit_hash": commit_hash,
                    "author_when": author_when,
                    "committer_when": committer_when,
                }
            elif line and current:
                path = self._unquote_git_path(line)
                if path not in last:
                    last[path] = current

        return last

    def get_repo_files(self, language=None, skip_last_commit=None):
        """return a list of files in the repo, excluding .git"""
        repo_files = {}
        repo_path = Path(self.repo_dir).resolve()
        p_files = Panopticas.identify_files(repo_path)

        # One git log walk for the whole repo, then a dict lookup per file.
        last_commits = {} if skip_last_commit else self._last_commit_by_path()

        unmanaged = 0

        for entry in p_files:
            # Never record git internals as repo files. panopticas excludes
            # .git on most repos but not reliably (e.g. a freshly-init'd repo),
            # so exclude it explicitly here.
            if entry == ".git" or entry.startswith(".git/") or "/.git/" in entry:
                continue

            git_metadata = last_commits.get(entry)

            if not skip_last_commit and git_metadata is None:
                # git doesn't track it, so it isn't repo content. Absent from
                # the lookup costs nothing, where the per-file implementation
                # paid a subprocess before discarding the file anyway.
                unmanaged += 1
                continue

            data = {}
            self.add_git_to_dict(data)
            data["hash"] = self.current_hash

            data["Language"] = p_files[entry]
            data["Location"] = entry
            data["Filename"] = os.path.basename(entry)

            tags = Panopticas.get_filename_metatypes(entry)
            data["tech_type"] = tags

            if skip_last_commit:
                data["committer_when"] = None
                data["status"] = None
            else:
                data["committer_when"] = git_metadata.get("committer_when")
                data["status"] = KospexUtils.development_status(
                    KospexUtils.days_ago(git_metadata.get("author_when")))

            repo_files[entry] = data

        if unmanaged:
            # On a clean clone panopticas and git agree, so this is zero. A
            # non-zero count means the sync was taken from a working directory
            # with untracked files (build output, scratch files, an in-progress
            # branch), and the file inventory won't match what is committed.
            log.warning("%s: %d untracked file(s) skipped - synced from a "
                        "working directory rather than a clean clone",
                        self.repo_dir, unmanaged)

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

    def planned_clone_path(self, repo_url):
        """Where clone_repo() would put this URL, worked out without the network.

        Lets callers check a URL against the DB before paying for a clone.

        Args:
        repo_url (str): The git URL to clone.

        Returns:
        dict: {"repo_id", "path", "parts"}, or None if the URL cannot be parsed
        or the destination would fall outside KOSPEX_CODE.
        """
        code_dir = HabitatConfig.get_instance().code_dir

        # Trailing slashes break the parsers
        parts = self.parse_git_remote(repo_url.rstrip("/"))
        if not parts:
            print(f"ERROR: could not parse git URL: {repo_url}")
            return None

        repo_path = code_dir / parts["remote"] / parts["org"] / parts["repo"]

        # ADO repo names can carry path segments straight from the URL, and
        # urlparse does not normalise '..'. Resolve both sides — comparing a
        # resolved path against an unresolved root false-positives wherever the
        # root is a symlink (macOS symlinks /tmp to /private/tmp).
        code_root = code_dir.resolve()
        if not repo_path.resolve().is_relative_to(code_root):
            print(f"ERROR: refusing to clone outside {code_root}: {repo_path}")
            return None

        return {
            "repo_id": self.generate_repo_id(parts["remote"], parts["org"], parts["repo"]),
            "path": str(repo_path),
            "parts": parts,
        }

    def clone_repo(self, repo_url):
        """Clone a repo into the kospex code directory.

        Accepts HTTPS, ssh:// and scp-style (git@host:org/repo.git) URLs.

        Args:
        repo_url (str): The git URL to clone.

        Returns:
        str: Path to the cloned repo on disk, or None on failure.
        """
        code_dir = HabitatConfig.get_instance().code_dir
        if not code_dir.is_dir():
            exit(f"KOSPEX_CODE directory not found: {code_dir}\n"
                 f"Run 'kospex init --create' to create it.")

        planned = self.planned_clone_path(repo_url)
        if not planned:
            return None

        parts = planned["parts"]
        repo_path = Path(planned["path"])
        org_dir = repo_path.parent

        org_dir.mkdir(parents=True, exist_ok=True)

        if repo_path.is_dir():
            print(f"Repo exists, pulling latest changes: {repo_path}")
            result = subprocess.run(["git", "pull"], cwd=repo_path, check=False)
        else:
            print(f"Cloning repo: {repo_url}")
            # List form (no shell) and an explicit destination: the URL cannot
            # reach a shell, and '--' stops a hostile URL being read as a flag.
            result = subprocess.run(
                ["git", "clone", "--", repo_url, parts["repo"]],
                cwd=org_dir, check=False)

        if result.returncode != 0:
            print(f"Error cloning or pulling repo: {repo_url}")
            return None

        # str, not Path: kgit.py:264 concatenates this with a str.
        return str(repo_path)

    def get_latest_commit_datetime(self, repo_id):
        """Get the latest commit datetime for the given repo_id"""
        cursor = KospexQuery().kospex_db.execute(
            "SELECT MAX(committer_when) FROM commits WHERE _repo_id = ?", (repo_id,)
        )
        latest_datetime = cursor.fetchone()[0]
        return latest_datetime


class MissingGitDirectory(Exception):
    """Exception for missing git directory"""

    pass
