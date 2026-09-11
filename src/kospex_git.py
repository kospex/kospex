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
        # Lazily constructed on first use. KospexGit() is created in hot paths,
        # so it must not open a DB connection unless a method needs one.
        # Assignable, so tests can inject a stand-in.
        self.kospex_query = None

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
        # Remove a .git suffix if present. Must be removesuffix, not rstrip:
        # rstrip takes a *character set*, so it eats any trailing run of
        # '.', 'g', 'i', 't' -- a repo named 'digit' became 'd'. (#135)
        clean_url = clone_url.removesuffix(".git")

        # scp-style ADO SSH: git@ssh.dev.azure.com:v3/{org}/{project}/{repo}
        # 'v3' is a path prefix, not a port, and there is no '_git' segment.
        # Normalise it to the HTTPS shape so both forms yield one repo_id --
        # ssh.dev.azure.com is the SSH endpoint of the same service, not a
        # different origin (unlike the legacy *.visualstudio.com tenant hosts).
        scp_ado = re.match(
            r"^git@ssh\.dev\.azure\.com:v3/(?P<rest>.+)$", clean_url)
        scheme_override = None
        if scp_ado:
            clean_url = f"https://dev.azure.com/{scp_ado.group('rest')}"
            scheme_override = "ssh"  # the transport was SSH; only the shape is rewritten

        # Parse the URL
        parsed = urlparse(clean_url)

        # ssh://.../v3/... is the same endpoint reached with an explicit scheme.
        netloc = parsed.netloc
        path = parsed.path
        if netloc == "ssh.dev.azure.com":
            netloc = "dev.azure.com"
            path = re.sub(r"^/v3/", "/", path)

        # Check if it's a dev.azure.com URL
        if netloc == "dev.azure.com":
            # Format: https://dev.azure.com/{organization}/{project}/_git/{repository}
            # The SSH forms omit '_git', so accept both shapes.
            path_parts = [seg for seg in path.strip("/").split("/") if seg]

            if "_git" in path_parts:
                git_at = path_parts.index("_git")
                # project is the segment before '_git'; org is what precedes it,
                # ignoring a legacy collection segment.
                project = path_parts[git_at - 1] if git_at >= 1 else ""
                organization = path_parts[0] if git_at >= 2 else ""
                repository = "/".join(path_parts[git_at + 1:])
            elif len(path_parts) >= 3:
                organization, project = path_parts[0], path_parts[1]
                repository = "/".join(path_parts[2:])
            else:
                organization = project = repository = ""

            if organization and project and repository:

                # org/project is a hierarchy, encoded like a GitLab subgroup
                # ('/' becomes '~~' in generate_repo_id). The previous hyphen
                # join collided: '-' is legal in ADO org names, so my-org/Project
                # and my/org-Project produced one id. '/' cannot appear in an ADO
                # org, project or repo name, so this is unambiguous. Supersedes #50.
                return {
                    "remote": netloc,
                    "org": f"{organization}/{project}",
                    "project": project,
                    "repo": repository,
                    "remote_type": scheme_override or parsed.scheme,
                }

        # Check if it's a legacy visualstudio.com URL
        elif netloc.endswith(".visualstudio.com"):
            # Format: https://{org}.visualstudio.com/[{collection}/]{project}/_git/{repo}
            # Locate '_git' rather than assuming its index, so a legacy
            # collection segment (e.g. 'DefaultCollection') does not make the
            # URL unparseable -- it is routing, not identity.
            path_parts = [seg for seg in path.strip("/").split("/") if seg]

            if "_git" in path_parts:
                git_at = path_parts.index("_git")
                project = path_parts[git_at - 1] if git_at >= 1 else ""
                repository = "/".join(path_parts[git_at + 1:])
                # The organisation is the first hostname label. Using the project
                # as the org (the old behaviour) discarded it entirely, so the
                # project masqueraded as the org and the id could not be compared
                # with the dev.azure.com form.
                organization = netloc.split(".", 1)[0]

                if project and repository:
                    return {
                        "remote": netloc,
                        "org": f"{organization}/{project}",
                        "project": project,
                        "repo": repository,
                        "remote_type": scheme_override or parsed.scheme,
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

        Dispatches to provider rules in order. Each rule takes the normalised
        URL and returns a parts dict or None; the first match wins. Order only
        matters where two rules can claim the same URL -- see PROVIDER_RULES.

        Args:
        url (str): The URL to extract information from.

        Returns:
        dict: A dictionary containing the remote, org, repo, and remote_type,
        or None if the URL is not a recognisable git remote.
        """
        url = KospexGit._normalise_remote_url(url)
        if url is None:
            return None

        # Order matters only where two rules can claim the same URL:
        #   - ADO must precede the scp-style rule, or 'git@ssh.dev.azure.com:v3/...'
        #     is claimed by it and 'v3' becomes the org.
        #   - Bitbucket Server must precede the generic rule, or '/scm/' is
        #     kept as part of the org.
        #   - Gerrit follows the generic rule: a multi-segment googlesource
        #     path is handled generically, and only the single-segment form
        #     (which the generic rule declines for having no org) reaches it.
        rules = (
            KospexGit.parse_ado_git_url,
            KospexGit.parse_bitbucket_onpremise_url,
            KospexGit.parse_ssh_git_url,
            KospexGit._parse_generic_git_url,
            KospexGit._parse_gerrit_url,
        )

        for rule in rules:
            parts = rule(url)
            if parts:
                # Lowercase the identity fields here, not only in
                # generate_repo_id: _git_owner and _git_repo are written from
                # these values rather than derived from the id, and the
                # org-scoped queries bind _git_owner from a split org_key. If
                # the two disagree on case, every org lookup that starts from a
                # repo_id silently returns nothing. (#147)
                for field in ("org", "repo", "project"):
                    if parts.get(field):
                        parts[field] = parts[field].lower()
                return parts

        return None

    @staticmethod
    def _normalise_remote_url(url):
        """Return the URL with credentials and port stripped, or None if it is
        not a git remote at all.

        urlparse().hostname drops embedded credentials and the port for free --
        without this, 'ssh://git@host:7999/PROJ/repo.git' keeps 'git@host:7999'
        as the server, so the same repository cloned over SSH and HTTPS produces
        two different repo_ids.

        scp-style ('git@host:org/repo.git') has no '://' and is passed through
        untouched for parse_ssh_git_url.
        """
        if not url or not isinstance(url, str):
            return None

        # Trailing slashes are legal in a clone URL and carry no meaning. Strip
        # them here, once, so no provider rule has to allow for them -- several
        # call sites used to do this themselves (and one, the sync path, did
        # not, so a repo cloned with a trailing slash failed to parse on sync).
        url = url.strip().rstrip("/")
        if not url:
            return None

        if "://" not in url:
            # scp-style ('git@HOST:org/repo.git'), or junk no rule will claim.
            # Lowercase the host here too -- provider rules compare it by
            # string equality, so casing is not merely cosmetic.
            scp = re.match(r"^(?P<user>[^@]+@)(?P<host>[^:]+)(?P<rest>:.*)$", url)
            if scp:
                return f"{scp.group('user')}{scp.group('host').lower()}{scp.group('rest')}"
            return url

        try:
            parsed = urlparse(url)
        except ValueError:
            return None

        if parsed.scheme.lower() not in ("http", "https", "ssh", "git"):
            return None  # not a git transport
        if not parsed.hostname:
            return None

        # Always rebuild from parsed.hostname: it is lowercased, and drops any
        # credentials and port. Hostnames are case-insensitive (DNS), and the
        # provider rules match them by string equality -- 'Dev.Azure.com' failed
        # `netloc == "dev.azure.com"`, fell through to the generic rule, and put
        # the '_git' routing segment inside the organisation.
        # This is independent of the org/repo case question (#147), where the
        # answer is a judgement call rather than a protocol fact.
        return f"{parsed.scheme.lower()}://{parsed.hostname}{parsed.path}"

    @staticmethod
    def _parse_generic_git_url(url):
        """scheme://host/<org...>/<repo> -- the shape most providers share.

        The org may be several segments (GitLab groups and subgroups, Gerrit
        project paths); generate_repo_id encodes the '/' as '~~'. This single
        rule replaces a pair that differed only in strictness and were selected
        between by counting slashes in the URL -- a proxy for structure that a
        trailing slash was enough to flip.
        """
        pattern = (
            r"^(?P<protocol>https?|git|ssh)://"
            r"(?P<hostname>[^/]+)"
            r"(?P<directories>(?:/[^/]+)*?)/"
            r"(?P<last_part>[^/]+?)$"
        )
        m = re.match(pattern, url)
        if not m:
            return None

        org = m.group("directories").removeprefix("/")
        if not org:
            return None  # no org segment: not addressable as {org}/{repo}

        return {
            "remote": m.group("hostname"),
            "org": org,
            "repo": m.group("last_part").removesuffix(".git"),
            "remote_type": m.group("protocol"),
        }

    @staticmethod
    def _parse_gerrit_url(url):
        """Gerrit hosts under *.googlesource.com allow a single-segment project
        path, which has no org: https://go.googlesource.com/oauth2

        Multi-segment paths there need no special case -- the generic rule
        already encodes them correctly (chromium/src, platform/frameworks/base).

        Deliberately host-scoped. This was once
        '(?P<domain>[^/?#]+)/(?P<directory>.*)', which matched any
        scheme://host/path and made parse_git_remote incapable of ever returning
        None, so junk was silently given a plausible repo_id instead of being
        rejected. (#160)
        """
        pattern = (
            r"^(?P<protocol>https?)://"
            r"(?P<domain>[\w.-]+\.googlesource\.com)/"
            r"(?P<directory>[\w.-]+?)(?:\.git)?$"
        )
        m = re.match(pattern, url)
        if not m:
            return None

        return {
            "remote": m.group("domain"),
            "org": "",
            "repo": m.group("directory").removesuffix(".git"),
            "remote_type": m.group("protocol"),
        }

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

    @staticmethod
    def generate_repo_id(remote, org, repo):
        """
        A static method to generate a repo_id
        some git orgs have slashes, which we'll replace
        with a double ~~ for parsing and use in web URLs
        The general format for a repo_id is
        remote~org~repo
        """
        # Lowercased: providers treat owner and repo as case-insensitive for
        # uniqueness but case-preserving for display, so the same repository
        # arriving with different URL casing must not mint two ids (#147).
        # The guarantee lives on the builder as well as on parse_git_remote
        # because consumers call this directly.
        repo_id = f"{remote.lower()}~"
        repo_id += org.lower().replace("/", "~~")
        repo_id += f"~{repo.lower()}"

        return repo_id

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
        # No rstrip("/") here: parse_git_remote normalises trailing slashes
        # itself, so the defence lives in one place instead of at each caller.
        parts = self.parse_git_remote(repo_url)
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

    def _recorded_clone_path(self, repo_id):
        """The clone path recorded for repo_id, or None.

        Degrades to None on any failure -- an unreadable or absent database
        must not stop a clone.
        """
        try:
            query = self.kospex_query or KospexQuery()
            row = query.get_repo_by_id(repo_id) or {}
        except Exception as exc:  # noqa: BLE001 - a clone must not depend on the DB
            log.debug("could not look up %s: %s", repo_id, exc)
            return None

        file_path = row.get("file_path")
        return Path(file_path) if file_path else None

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

        # Where a repo actually lives is repos.file_path, not the layout the URL
        # implies. The two diverge whenever the derivation changes -- #147
        # lowercased it -- so a repo synced beforehand sits in a mixed-case
        # directory that the planned path no longer names. Checking the layout
        # alone clones it a second time on a case-sensitive filesystem.
        recorded = self._recorded_clone_path(planned["repo_id"])
        if recorded and recorded.is_dir():
            repo_path = recorded

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
            "SELECT strftime('%Y-%m-%dT%H:%M:%SZ', MAX(CAST(strftime('%s', committer_when) AS INTEGER)), 'unixepoch') FROM commits WHERE _repo_id = ?", (repo_id,)
        )
        latest_datetime = cursor.fetchone()[0]
        return latest_datetime


class MissingGitDirectory(Exception):
    """Exception for missing git directory"""

    pass
