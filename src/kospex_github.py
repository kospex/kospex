"""High level functions for common queries on Github orgs and users. """
import json
import os
from typing import Optional, List, Dict, Any
import requests
from kospex_utils import timer


class GitHubListingIncomplete(Exception):
    """A paginated GitHub listing failed partway, so the result is incomplete.

    Raised instead of returning what was read so far: a shorter list looks
    exactly like a complete one, and the missing repos never show up as failed.
    """

    def __init__(self, url, page, items_read, reason):
        self.url = url
        self.page = page
        self.items_read = items_read
        self.reason = reason
        super().__init__(
            f"GitHub listing incomplete: page {page} of {url} failed ({reason}) "
            f"after {items_read} items")


class KospexGithub:
    """
    GitHub functions for common kospex queries.
    """

    # This the original token name used by Kospex
    ENV_GITHUB_AUTH_TOKEN = "GITHUB_AUTH_TOKEN"
    # The token names used by the gh CLI, in its order of precedence.
    # GITHUB_TOKEN is also what GitHub Actions provides.
    ENV_GH_TOKEN = "GH_TOKEN"
    ENV_GITHUB_TOKEN = "GITHUB_TOKEN"
    # Order of precedence. The kospex-specific name wins, so a token set
    # deliberately for kospex isn't overridden by a personal gh token.
    TOKEN_ENV_VARS = (ENV_GITHUB_AUTH_TOKEN, ENV_GH_TOKEN, ENV_GITHUB_TOKEN)

    def __init__(self):

        self.access_token = ""
        self.headers = {}
        self.timeout = 10
        self.throttled = False
        self.authenticated = False
        self.token_source = None  # name of the env var the token came from
        self.base_url = "https://api.github.com"

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

    def _make_request(self, url, params=None, method='GET'):
        """
        Make a request to the GitHub API with proper error handling.

        Args:
            url: The full URL to request
            params: Optional query parameters
            method: HTTP method (default: GET)

        Returns:
            Tuple of (response_data, response) or (None, response) on error
        """
        try:
            response = requests.request(
                method=method,
                url=url,
                headers=self.headers,
                params=params,
                timeout=self.timeout
            )

            # Check rate limiting
            if response.status_code == 403:
                if 'X-RateLimit-Remaining' in response.headers:
                    remaining = response.headers.get('X-RateLimit-Remaining')
                    if remaining == '0':
                        self.throttled = True
                        print("GitHub API rate limit exceeded")
                        return None, response

            # Raise for other HTTP errors
            response.raise_for_status()

            # Return JSON data if available
            if response.text:
                return response.json(), response
            else:
                return None, response

        except requests.exceptions.HTTPError as err:
            print(f"HTTP Error: {self._redact(err)}")
            return None, response
        except requests.exceptions.Timeout as err:
            print(f"Timeout: {self._redact(err)}")
            return None, None
        except requests.exceptions.RequestException as err:
            print(f"Request Error: {self._redact(err)}")
            return None, None

    def _redact(self, err):
        """The exception text with the token removed (CWE-532).

        requests quotes the offending header value when it refuses a header,
        so the message can carry the whole Authorization value.
        """
        text = str(err)
        if self.access_token:
            for part in self.access_token.split():
                text = text.replace(part, "<redacted>")
        return text

    def _paginate(self, url, params=None):
        """
        Handle GitHub API pagination and return all results.

        Args:
            url: The API endpoint URL
            params: Optional query parameters

        Yields:
            Individual items from all pages

        Raises:
            GitHubListingIncomplete: if any page fails, including the first.
        """
        params = dict(params or {})

        # GitHub returns max 100 items per page
        params['per_page'] = 100
        params['page'] = 1
        items_read = 0

        while True:
            data, response = self._make_request(url, params=params)

            if data is None:
                # Not `break`: a partial listing must not pass as a complete one.
                raise GitHubListingIncomplete(
                    url, params['page'], items_read, self._failure_reason(response))

            # Yield all items from this page
            if isinstance(data, list):
                for item in data:
                    items_read += 1
                    yield item

                # If we got fewer items than requested, we're done
                if len(data) < params['per_page']:
                    break

                # Move to next page
                params['page'] += 1
            else:
                # Single object response, not paginated
                yield data
                break

    def _failure_reason(self, response):
        """Why a request returned no data, for error messages."""
        if self.throttled:
            return "rate limit exceeded"
        if response is None:
            return "no response"
        return f"HTTP {response.status_code}"

    def _get_authenticated_user(self):
        """
        Get information about the authenticated user.

        Returns:
            User data dictionary or None if not authenticated
        """
        url = f'{self.base_url}/user'
        data, response = self._make_request(url)
        return data

    def _get_user_info(self, username):
        """
        Get information about a specific user or organization.

        Args:
            username: The username or organization name

        Returns:
            User/org data dictionary or None if not found
        """
        url = f'{self.base_url}/users/{username}'
        data, response = self._make_request(url)
        return data

    def _get_user_repos(self, username, repo_type='all', owned_only=False):
        """
        Get repositories for a specific user.

        Args:
            username: The username
            repo_type: Type of repos ('all', 'owner', 'member') - other users only
            owned_only: For the token's own account, ask only for the repos it owns

        Yields:
            Repository dictionaries
        """
        # Check if this is the authenticated user
        auth_user = self._get_authenticated_user()
        if auth_user and auth_user.get('login') == username and self.authenticated:
            # Use authenticated endpoint for own repos. No `type`: GitHub answers
            # 422 "If you specify visibility or affiliation, you cannot specify
            # type", which failed every listing of the token's own account.
            url = f'{self.base_url}/user/repos'
            params = {'affiliation': 'owner' if owned_only
                      else 'owner,collaborator,organization_member'}
        else:
            # Use public endpoint for other users
            url = f'{self.base_url}/users/{username}/repos'
            params = {'type': 'all'}


        for repo in self._paginate(url, params):
            yield repo

    @timer()
    def _get_org_repos(self, org, repo_type='all'):
        """
        Get repositories for an organization.

        Args:
            org: The organization name
            repo_type: Type of repos ('all', 'public', 'private', 'forks', 'sources', 'member')

        Yields:
            Repository dictionaries
        """
        url = f'{self.base_url}/orgs/{org}/repos'
        params = {'type': repo_type}

        for repo in self._paginate(url, params):
            yield repo

    @timer()
    def _get_repo_details(self, owner, repo):
        """
        Get detailed information about a specific repository.

        Args:
            owner: Repository owner (user or org)
            repo: Repository name

        Returns:
            Repository data dictionary or None if not found
        """
        url = f'{self.base_url}/repos/{owner}/{repo}'
        data, response = self._make_request(url)
        return data

    def get_env_credentials(self):
        """Get the Github PAT / Auth token from the environment."""

        # Empty or whitespace-only counts as unset, so `export GITHUB_AUTH_TOKEN=`
        # doesn't hide a GH_TOKEN. Surrounding whitespace (a pasted value, a
        # CRLF .env file) would otherwise be sent and rejected.
        for var in self.TOKEN_ENV_VARS:
            access_token = (os.getenv(var) or "").strip()
            if access_token:
                self.set_access_token(access_token)
                self.authenticated = True
                self.token_source = var
                return True
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

    @timer()
    def get_repos(self, username_or_org: str, no_auth: Optional[bool] = False,
                  owned_only: bool = False) -> List[Dict[str, Any]]:
        """
        Get the repos for a user or organization.

        Args:
            username_or_org: The username or organization name
            no_auth: If True only fetch public repositories (default: False)
            owned_only: Keep only repos whose owner is username_or_org. A user's
                listing also returns repos they collaborate on or reach through
                an organisation (octocat: 8 owned of 203). Dropped before the
                per-fork lookup, so they cost no extra requests.

        Returns:
            List of repository information dictionaries

        Raises:
            GitHubListingIncomplete: if the owner lookup or any page of the
            listing fails. An owner that doesn't exist (404) returns [].
        """
        # Get owner information to determine type
        url = f'{self.base_url}/users/{username_or_org}'
        owner, response = self._make_request(url)
        if not owner:
            if response is not None and response.status_code == 404:
                print(f"Could not find user or organization: {username_or_org}")
                return []
            # Anything else (timeout, 5xx, rate limit) is not "doesn't exist".
            raise GitHubListingIncomplete(url, 1, 0, self._failure_reason(response))

        owner_type = owner.get('type')
        repos = []

        # Determine which repos to fetch based on authentication and owner type
        if no_auth:
            print("Fetching public repositories...")
            # Fetch public repos only
            if owner_type == 'Organization':
                repos = self._get_org_repos(username_or_org, repo_type='public')
            else:
                repos = self._get_user_repos(username_or_org, repo_type='all')
        elif self.authenticated:
            # Get authenticated user to check if it's the same as username_or_org
            auth_user = self._get_authenticated_user()
            if auth_user and auth_user.get('login') == username_or_org:
                # This requires authentication to check
                print("Fetching your own repositories (including private)...")
                repos = self._get_user_repos(username_or_org, repo_type='all',
                                             owned_only=owned_only)
            elif owner_type == 'Organization':
                print("Owner is an organization")
                repos = self._get_org_repos(username_or_org, repo_type='all')
            else:
                print("Owner is not an organization, getting public repos")
                repos = self._get_user_repos(username_or_org, repo_type='all')
        else:
            print("Not authenticated, fetching public repositories only...")
            if owner_type == 'Organization':
                repos = self._get_org_repos(username_or_org, repo_type='public')
            else:
                repos = self._get_user_repos(username_or_org, repo_type='all')

        repo_list = []

        for repo in repos:
            repo_owner = (repo.get('full_name') or '').partition('/')[0]
            if owned_only and repo_owner.lower() != username_or_org.lower():
                continue
            record = {
                # The provider's own id survives renames and transfers (#348).
                'id': repo.get('id'),
                'name': repo.get('full_name'),
                'visibility': repo.get('visibility'),
                # The repo's own owner, not the account asked about: a user's
                # listing includes other owners' repos (-include-member).
                'type': (repo.get('owner') or {}).get('type') or owner_type,
                'owner': (repo.get('owner') or {}).get('login') or repo_owner or username_or_org,
                'updated_at': repo.get('updated_at'),
                'pushed_at': repo.get('pushed_at'),
                'archived': repo.get('archived'),
                'homepage': repo.get('homepage'),
                'description': repo.get('description'),
                'clone_url': repo.get('clone_url'),
                'ssh_url': repo.get('ssh_url'),
                'fork': repo.get('fork')
            }

            # Check if the repository is a fork and get parent info
            if repo.get('fork'):
                # The listing endpoint doesn't include parent or source (checked
                # against the live API), so this is normally one request per fork.
                full_repo = repo if repo.get('parent') else \
                    self._get_repo_details(username_or_org, repo.get('name'))
                parent = (full_repo or {}).get('parent') or {}
                source = (full_repo or {}).get('source') or {}
                if parent:
                    record['parent_url'] = parent.get('html_url')
                # Full names: the immediate parent, and the root of the fork network.
                record['fork_parent'] = parent.get('full_name')
                record['fork_source'] = source.get('full_name')

            repo_list.append(record)

        return repo_list

    def get_repo(self, repo_full_name):
        """
        Get the full repository information

        Args:
            repo_full_name: Full repository name in format 'owner/repo'

        Returns:
            Repository data dictionary or None if not found
        """
        parts = repo_full_name.split('/')
        if len(parts) != 2:
            print(f"Invalid repository name format: {repo_full_name}. Expected 'owner/repo'")
            return None

        owner, repo = parts
        return self._get_repo_details(owner, repo)

    def get_account_type(self, value):
        """
        Get the account type of a user or organization
        If the account does not exist, return None
        """
        url = f'{self.base_url}/users/{value}'

        data, response = self._make_request(url)
        if data:
            return data.get('type')

        return None

    def test_auth(self):
        """
        Test the authentication token
        """
        url = f'{self.base_url}/user'
        data, response = self._make_request(url)

        if data and 'login' in data:
            # Successfully authenticated
            print(f"Authenticated as: {data.get('login')}")
            return True
        # Not `elif response`: a 4xx Response is falsy, so that never matched.
        elif response is not None and response.status_code == 401:
            print("Authentication failed: Invalid token")
            return False
        else:
            print("No data returned or request failed")
            return False
