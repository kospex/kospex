"""High level functions for common queries on Github orgs and users. """
import json
import os
from posix import access
from typing import Optional, List, Dict, Any
import requests
from github import Github, Auth
from github.GithubException import UnknownObjectException

class KospexGithub:
    """
    GitHub functions for common kospex queries.
    """

    # This the original token name used by Kospex
    ENV_GITHUB_AUTH_TOKEN = "GITHUB_AUTH_TOKEN"
    # This is the token name used by Github and the gh CLI
    ENV_GH_TOKEN = "GH_TOKEN"

    def __init__(self):

        self.access_token = ""
        self.headers = {}
        self.timeout = 10
        self.throttled = False
        self.gh = Github()
        self.authenticated = False

    def set_access_token(self, access_token):
        """Set the access token"""
        self.access_token = access_token
        self.headers = { "Authorization": f"token {self.access_token}",
                        "Accept": "application/vnd.github.v3+json" }

    def set_timeout(self, timeout):
        """
        Set the timeout for requests, when using raw requests to the GitHub API.
        """
        self.timeout = timeout

    def get_env_credentials(self):
        """Get the Github PAT / Auth token from the environment."""

        access_token = os.getenv(self.ENV_GITHUB_AUTH_TOKEN)
        if access_token is None:
            access_token = os.getenv(self.ENV_GH_TOKEN)

        if access_token:
            self.set_access_token(access_token)
            auth = Auth.Token(access_token)
            self.gh = Github(auth=auth)
            self.authenticated = True
            return True
        else:
            return False

    def github_url_to_api_url(self, github_url):
        """
        Return the GitHub API URL for a given GitHub URL.
        E.g. given https://github.com/kospex/kospex,
        return https://api.github.com/repos/kospex/kospex
        """
        # Remove the ".git" suffix if present
        if github_url.endswith('.git'):
            github_url = github_url[:-4]

        # Extract the owner and repo name from the GitHub URL
        parts = github_url.rstrip('/').split('/')
        owner = parts[-2]
        repo = parts[-1]

        # TODO : Check if the owner and repo are valid

        # Construct the API URL
        api_url = f"https://api.github.com/repos/{owner}/{repo}"

        return api_url

    def get_repos(self, username_or_org: str, no_auth: Optional[bool] = False) -> List[Dict[str, Any]]:
        """
        Get the repos for a user or organization.

        Args:
            username_or_org: The username or organization name
            no_auth: If True only fetch public repositories (default: False)

        Returns:
            List of repository information dictionaries
        """
        owner = self.gh.get_user(username_or_org)
        owner_type = owner.type
        repos = []

        # Check if no_auth and the owner type and get all available repositories
        if no_auth:
            print("Fetching public repositories...")
            repos = self.gh.get_user(username_or_org).get_repos()
        elif username_or_org == self.gh.get_user().login:
            # This requires authentication to check
            print("Fetching your own repositories (including private)...")
            user = self.gh.get_user()  # No parameters to get authenticated user
            repos = user.get_repos(type='all')
        elif owner_type == 'Organization':
            print("Owner is an organization")
            org = self.gh.get_organization(username_or_org)
            repos = org.get_repos(type='all')
        else:
            print("Owner is not an organization, getting public repos")
            repos = self.gh.user.get_repos()

        repo_list = []

        for repo in repos:
            #print(repo.full_name)
            #print(json.dumps(repo._rawData, indent=4))
            #print("\n")

            record = {
                'name': repo.full_name,
                'visibility': repo.visibility,
                'type': owner.type,
                'owner': username_or_org,
                'updated_at': repo.updated_at,
                'pushed_at': repo.pushed_at,
                'archived': repo.archived,
                'homepage': repo.homepage,
                'description': repo.description,
                'clone_url': repo.clone_url,
                'ssh_url': repo.ssh_url,
                'fork': repo.fork
            }

            # We need to check if the repository is a fork
            full_repo = None
            if repo.fork:
                # Then request the full repo which has the parents detail
                full_repo = self.gh.get_repo(repo.full_name)
                parent = full_repo.parent
                if parent:
                    record['parent_url'] = parent.html_url

            repo_list.append(record)

        return repo_list

    def get_repo(self, repo_full_name):
        """
        Get the full repository information
        """
        repo = self.gh.get_repo(repo_full_name)

        return repo

    def get_account_type(self, value):
        """
        Get the account type of a user or organization
        If the account does not exist, return None
        """
        url = f'https://api.github.com/users/{value}'

        account_type = None

        try:
            response = requests.get(url, headers=self.headers, timeout=self.timeout)
            data = response.json()
            account_type = data.get('type')
        except requests.exceptions.HTTPError as err:
            print(f"Error: {err}")
        except requests.exceptions.Timeout as err:
            print(f"Timeout: {err}")

        return account_type

    def test_auth(self):
        """
        Test the authentication token
        """
        url = 'https://api.github.com/user'
        response = requests.get(url, headers=self.headers, timeout=self.timeout)
        data = response.json()
        if data:
            status = data.get('status')
            # '401' is unauthorized, so if the status is not 401,
            if status == '401':
                return False
            else:
                print(data)
                return True
        else:
            print("No data returned")
            return False
