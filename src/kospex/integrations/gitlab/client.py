"""GitLabClient: a small read-only client for the GitLab REST API (v4).

Every request is a GET with the token in `PRIVATE-TOKEN`. The token goes only to
the configured server: https is required (CWE-319), and redirects are never
followed because requests keeps a custom auth header across hosts (CWE-522).
"""
import os
import time
from urllib.parse import urlsplit

import requests

DEFAULT_SERVER = "https://gitlab.com"
TOKEN_ENV_VAR = "GITLAB_TOKEN"
URL_ENV_VAR = "GITLAB_URL"
API_PATH = "/api/v4"

PER_PAGE = 100          # the REST maximum
MAX_RATE_LIMIT_WAIT = 60
MAX_RETRIES = 3


class GitLabError(Exception):
    """A GitLab API call failed, or the server was given wrongly."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class GitLabAuthError(GitLabError):
    """The token is missing, or the server rejected it (401)."""


def server_url(value=None):
    """The server's base URL, from `value`, else GITLAB_URL, else gitlab.com.

    Accepts `host`, `host:port` and `https://host[:port][/path]`; a path is a
    GitLab installed under one. Anything but https is refused.
    """
    if value is None:
        value = os.environ.get(URL_ENV_VAR, "").strip() or DEFAULT_SERVER
    text = value.strip()
    if "://" not in text:
        text = f"https://{text}"
    try:
        parts = urlsplit(text)
        parts.port      # raises ValueError for a port that isn't a number
    except ValueError as e:
        raise GitLabError(f"Not a usable GitLab server address: {e}") from None
    # Checked first, so a password is never echoed back in another message.
    if parts.username is not None or parts.password is not None:
        raise GitLabError("The GitLab server address must not contain a user name or "
                          f"password: the token comes from {TOKEN_ENV_VAR}.")
    if parts.scheme != "https" or not parts.hostname or parts.query or parts.fragment:
        raise GitLabError(f"Not a usable GitLab server address: {value!r}. "
                          "Give a host or an https URL.")
    path = parts.path.rstrip("/").removesuffix(API_PATH)
    return f"https://{parts.netloc.lower()}{path}"


def has_path_prefix(url):
    """Whether the server is installed under a path, as in `host/gitlab`."""
    return bool(urlsplit(url).path.strip("/"))


class GitLabClient:
    """Read-only GitLab REST client."""

    def __init__(self, token=None, server=None, session=None, timeout=30, sleep=time.sleep):
        self.server = server_url(server)
        if token is None:
            token = os.environ.get(TOKEN_ENV_VAR, "")
        self.token = token.strip()
        if not self.token:
            raise GitLabAuthError(f"No GitLab token: set {TOKEN_ENV_VAR} in the environment "
                                  "or in ~/kospex/kospex.env")
        self.session = session or requests.Session()
        self.timeout = timeout
        self.sleep = sleep

    def _redact(self, text):
        """The text with the token removed (CWE-532)."""
        for part in self.token.split():
            text = text.replace(part, "<redacted>")
        return text

    def _send(self, url, params):
        try:
            return self.session.get(url, params=params,
                                    headers={"PRIVATE-TOKEN": self.token},
                                    timeout=self.timeout, allow_redirects=False)
        except requests.exceptions.SSLError as e:
            raise GitLabError(
                f"TLS error talking to {self.server}: {self._redact(str(e))}\n"
                "For an internal certificate authority, set REQUESTS_CA_BUNDLE to its "
                "CA bundle.") from None
        except requests.exceptions.InvalidHeader:
            # requests quotes the header value, which is the token.
            raise GitLabAuthError(f"{TOKEN_ENV_VAR} is not a valid token: it contains "
                                  "characters a header can't carry.") from None
        except requests.exceptions.RequestException as e:
            raise GitLabError(f"Couldn't reach {self.server}: "
                              f"{self._redact(str(e))}") from None

    def get(self, path, params=None):
        """One GET of `<server>/api/v4<path>`. Returns a successful response."""
        url = f"{self.server}{API_PATH}{path}"
        for attempt in range(MAX_RETRIES + 1):
            response = self._send(url, params)
            if response.status_code != 429 or attempt == MAX_RETRIES:
                break
            try:
                wait = int(response.headers.get("Retry-After", "1"))
            except ValueError:
                wait = 1
            self.sleep(min(max(wait, 1), MAX_RATE_LIMIT_WAIT))
        self._check(response, path)
        return response

    def _check(self, response, path):
        status = response.status_code
        if 200 <= status < 300:
            return
        if 300 <= status < 400:
            target = response.headers.get("Location", "an unnamed address")
            raise GitLabError(f"{self.server} redirected to {target}. Redirects are not "
                              "followed: give that server's address if it is right.", status)
        if status == 401:
            raise GitLabAuthError(f"{self.server} rejected the token in {TOKEN_ENV_VAR} "
                                  "(401): wrong server, wrong token or expired.", status)
        reason = {403: "not allowed with this token (403)",
                  404: "not found, or not visible to this token (404)",
                  429: "rate limit still in force after waiting (429)",
                  }.get(status, f"HTTP {status}")
        raise GitLabError(f"{self.server}{API_PATH}{path}: {reason}", status)

    def _json(self, response, path):
        try:
            return response.json()
        except ValueError:
            raise GitLabError(f"{self.server}{API_PATH}{path} did not return JSON. "
                              "Is this a GitLab server?") from None

    def paginate(self, path, params=None):
        """Every item of a list endpoint.

        Raises on any failed page: a short list looks exactly like a complete one.
        """
        items, page = [], 1
        while True:
            response = self.get(path, {**(params or {}), "per_page": PER_PAGE, "page": page})
            data = self._json(response, path)
            if not isinstance(data, list):
                raise GitLabError(f"{self.server}{API_PATH}{path} did not return a list.")
            items.extend(data)
            following = response.headers.get("X-Next-Page")
            if following is None:
                following = page + 1 if len(data) == PER_PAGE else ""
            if following == "":
                return items
            try:
                following = int(following)
            except ValueError:
                following = 0
            if following <= page:
                raise GitLabError(f"{self.server}{API_PATH}{path}: paging did not advance "
                                  f"after page {page}.")
            page = following

    def identity(self):
        """Who the token is, and what the server will say about it and itself."""
        user = self._json(self.get("/user"), "/user")

        def optional(path):
            try:
                data = self._json(self.get(path), path)
            except GitLabError:
                return {}
            return data if isinstance(data, dict) else {}

        version = optional("/version")
        token = optional("/personal_access_tokens/self")
        return {"username": user.get("username"), "version": version.get("version"),
                "scopes": token.get("scopes"), "expires_at": token.get("expires_at")}
