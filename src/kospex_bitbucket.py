
"""
High level functions for common queries on Bitbucket orgs, workspaces and users.
"""
import os
import json
import requests

class KospexBitbucket:
    """High level GitHub functions for common kospex queries."""
    def __init__(self):
        self.headers = {}
        self.timeout = 10
        self.workspace_id = ""
        self.username = ""
        self.app_password = ""
        #self.base_url = "https://api.bitbucket.org/2.0/repositories/"
        #{workspace_id}"

    def set_workspace_id(self, workspace_id):
        """Set the workspace ID"""
        self.workspace_id = workspace_id

    def set_username(self, username):
        """Set the username"""
        self.username = username

    def set_app_password(self, app_password):
        """Set the app password"""
        self.app_password = app_password

    def get_env_credentials(self):
        """Get the Bitbucket credentials from the environment."""
        self.username = os.getenv("BITBUCKET_USERNAME")
        self.app_password = os.getenv("BITBUCKET_APP_PASSWORD")

        if self.username and self.app_password:
            return True
        return False

    def get_repos(self,workspace_id=None):
        """Get the repos for a workspace."""
        workspace = workspace_id or self.workspace_id
        base_url = f"https://api.bitbucket.org/2.0/repositories/{workspace}"
        auth = (self.username, self.app_password)
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

    def get_https_clone_url(self,repo):
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
