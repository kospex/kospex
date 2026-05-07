
"""
High level functions for common queries on Bitbucket orgs, workspaces and users.
"""
import os
import requests

# Credential modes returned by get_env_credentials
MODE_TOKEN = "token"
MODE_LEGACY = "legacy"
MODE_NONE = "none"
MODE_CONFIG_ERROR = "config_error"

# Static basic-auth username Atlassian accepts when authenticating with an
# API token without an email or username
TOKEN_ONLY_USERNAME = "x-bitbucket-api-token-auth"

# Scopes a Bitbucket-scoped API token must carry to run get_repos against a
# workspace. Atlassian account API tokens (created at id.atlassian.com) have
# no scopes and also work; these are only required for the Bitbucket-scoped
# token type.
REQUIRED_SCOPES = (
    "read:project:bitbucket",
    "read:repository:bitbucket",
    "read:workspace:bitbucket",
)


class KospexBitbucket:
    """High level Bitbucket functions for common kospex queries."""

    def __init__(self):
        self.timeout = 10
        self.workspace_id = ""
        self.username = ""
        self.email = ""
        self.api_token = ""
        self.app_password = ""

    def set_workspace_id(self, workspace_id):
        """Set the workspace ID"""
        self.workspace_id = workspace_id

    def set_username(self, username):
        """Set the Bitbucket username"""
        self.username = username

    def set_email(self, email):
        """Set the Atlassian account email"""
        self.email = email

    def set_api_token(self, api_token):
        """Set the Bitbucket API token (preferred over app_password)"""
        self.api_token = api_token

    def set_app_password(self, app_password):
        """Set the legacy Bitbucket app password (deprecated by Atlassian)"""
        self.app_password = app_password

    def get_env_credentials(self):
        """
        Read Bitbucket credentials from the environment and populate this
        instance.

        Returns a (mode, reason) tuple where mode is one of:
        - MODE_TOKEN: BITBUCKET_API_TOKEN is set; basic-auth username comes
          from BITBUCKET_EMAIL or BITBUCKET_USERNAME (mutually exclusive),
          falling back to the static x-bitbucket-api-token-auth username.
        - MODE_LEGACY: BITBUCKET_USERNAME + BITBUCKET_APP_PASSWORD are set
          and no BITBUCKET_API_TOKEN. App passwords are deprecated by
          Atlassian; reason carries a deprecation message.
        - MODE_CONFIG_ERROR: BITBUCKET_API_TOKEN is set together with both
          BITBUCKET_EMAIL and BITBUCKET_USERNAME.
        - MODE_NONE: nothing usable in the environment.
        """
        api_token = os.getenv("BITBUCKET_API_TOKEN")
        email = os.getenv("BITBUCKET_EMAIL")
        username = os.getenv("BITBUCKET_USERNAME")
        app_password = os.getenv("BITBUCKET_APP_PASSWORD")

        if api_token:
            if email and username:
                return (
                    MODE_CONFIG_ERROR,
                    "BITBUCKET_EMAIL and BITBUCKET_USERNAME are mutually "
                    "exclusive when using BITBUCKET_API_TOKEN — set one or "
                    "the other, not both.",
                )
            self.api_token = api_token
            self.email = email or ""
            self.username = username or ""
            return (MODE_TOKEN, None)

        if username and app_password:
            self.username = username
            self.app_password = app_password
            return (
                MODE_LEGACY,
                "BITBUCKET_APP_PASSWORD is deprecated by Atlassian and "
                "stops working entirely on 2026-06-09. Switch to "
                "BITBUCKET_API_TOKEN before then. See "
                "`kgit bitbucket --help`.",
            )

        return (MODE_NONE, None)

    def _auth_tuple(self):
        """
        Return the (username, password) pair for HTTP Basic auth.

        Token mode prefers email, falls back to username, then to the
        static x-bitbucket-api-token-auth string.
        """
        if self.api_token:
            user = self.email or self.username or TOKEN_ONLY_USERNAME
            return (user, self.api_token)
        return (self.username, self.app_password)

    def get_repos(self, workspace_id=None):
        """Get the repos for a workspace."""
        workspace = workspace_id or self.workspace_id
        base_url = f"https://api.bitbucket.org/2.0/repositories/{workspace}"
        auth = self._auth_tuple()
        page = 1
        page_size = 100

        all_repos = []

        while True:
            url = f"{base_url}?pagelen={page_size}&page={page}"
            response = requests.get(url, auth=auth, timeout=self.timeout)
            response.raise_for_status()

            data = response.json()
            repos = data['values']
            all_repos.extend(repos)

            if len(repos) < page_size:
                break

            page += 1

        return all_repos

    def get_https_clone_url(self, repo):
        """
        Get the HTTPS clone URL from the repo.
        """
        clone = None
        links = repo.get("links")

        if links:
            clone = links.get("clone")

        if clone:
            for i in clone:
                if i.get("name") == "https":
                    return i.get("href")

        return None

    def test_auth(self, workspace_id=None):
        """
        Test that the configured credentials can authenticate against
        Bitbucket and have the scopes get_repos needs. Hits
        /2.0/repositories/{workspace}?pagelen=1 — a perfect mirror of the
        get_repos call, just one page.

        A workspace is required. /2.0/user needs an account-level scope
        get_repos doesn't, and /2.0/workspaces was observed returning HTTP
        410 (Atlassian is retiring listing endpoints for token auth), so
        neither is a reliable proxy.

        Returns (ok, status_code). Callers can distinguish 401 (bad
        credentials) from 403 (auth ok but token missing scopes).
        """
        workspace = workspace_id or self.workspace_id
        if not workspace:
            raise ValueError(
                "test_auth requires a workspace_id; pass one or call "
                "set_workspace_id() first."
            )
        url = (
            f"https://api.bitbucket.org/2.0/repositories/{workspace}"
            "?pagelen=1"
        )
        auth = self._auth_tuple()
        response = requests.get(url, auth=auth, timeout=self.timeout)
        return (response.status_code == 200, response.status_code)
