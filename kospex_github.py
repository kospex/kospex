"""High level functions for common queries on Github orgs and users. """
import json
import requests

class KospexGithub:
    """High level GitHub functions for common kospex queries."""
    def __init__(self):

        self.access_token = ""
        self.headers = {}
        self.timeout = 10
        self.throttled = False

    def set_access_token(self, access_token):
        """Set the access token"""
        self.access_token = access_token
        self.headers = { "Authorization": f"token {self.access_token}",
                        "Accept": "application/vnd.github.v3+json" }

    def set_timeout(self, timeout):
        """Set the timeout for requests"""
        self.timeout = timeout

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

    def get_repos(self, owner, account_type):
        """
        Get the repos for a user or organization.
        """

        url = None

        if account_type == "User":
            url = f'https://api.github.com/users/{owner}/repos'
        elif account_type == "Organization":
            url = f'https://api.github.com/orgs/{owner}/repos'
        else:
            # TODO: Do we want to raise an exception
            print("Invalid account type")
            return None

        repos = []
        page = 1

        while True:
            try:
                response = requests.get(url,
                                        params={'page': page, 'per_page': 100},
                                        headers = self.headers, timeout=self.timeout)

                data = response.json()
                status_code = response.status_code

                if status_code != 200:
                    print(f"Status Code: {status_code}")
                    break

                repos.extend(data)
                page += 1

            except requests.exceptions.HTTPError as err:
                print(f"Error: {err}")
            except requests.exceptions.Timeout as err:
                print(f"Timeout: {err}")

            if not data:
                break

        return repos

    def get_user_repos(self,username):
        """
        Return the full list of repos for this username, handling multiple pages
        """
        return self.get_repos(username, "User")


    # def get_user_repos(self,username):
    #     url = f'https://api.github.com/users/{username}/repos'
    #     repos = []
    #     page = 1

    #     while True:

    #         response = requests.get(url,
    #                                 params={'page': page, 'per_page': 100}, 
    #                                 headers = self.headers, timeout=self.timeout)
    #         response.raise_for_status()  # Raises an error for bad status codes

    #         data = response.json()
    #         if not data:
    #             break

    #         repos.extend(data)
    #         page += 1

    #     return repos

    def get_org_repos(self,username):
        """
        Return the full list of repos for this username, handling multiple pages
        """
        return self.get_repos(username, "Organization")

    # def get_org_repos(self,org_name):
    #     url = f'https://api.github.com/orgs/{org_name}/repos'
    #     repos = []
    #     page = 1

    #     while True:
    #         try:
    #             response = requests.get(url,
    #                                     params={'page': page, 'per_page': 100},
    #                                     headers = self.headers, timeout=self.timeout)
    #             #response.raise_for_status()  # Raises an error for bad status codes
    #             data = response.json()
    #             status_code = response.status_code

    #             if status_code != 200:
    #                 print(f"Status Code: {status_code}")
    #                 break

    #             repos.extend(data)
    #             page += 1

    #         except requests.exceptions.HTTPError as err:
    #             print(f"Error: {err}")
    #         except requests.exceptions.Timeout as err:
    #             print(f"Timeout: {err}")

    #         if not data:
    #             break

    #     return repos

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
