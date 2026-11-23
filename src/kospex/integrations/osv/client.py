"""OsvClient: a small read-only client for the OSV API (https://api.osv.dev/v1).

No authentication and no plan tiers. Two calls are used:

- POST /query     one package at one version -> the full vulnerability records that
                  affect it, paged by `next_page_token` in the request body.
- GET /vulns/{id} one full record by ID (GHSA-..., CVE-..., PYSEC-..., ...).

Only a public package name, ecosystem and version go in a request. A caller must
check `KospexDependencies.lookup_permitted()` first: this client does not know which
names are private.

Redirects are never followed, so a response cannot send a later request to another
host (CWE-201). OSV does not redirect these endpoints, so a 3xx is an error.
"""
import json
import time
from urllib.parse import quote

import requests

DEFAULT_API_URL = "https://api.osv.dev/v1"
TIMEOUT = 30
MAX_RETRIES = 3
MAX_RATE_LIMIT_WAIT = 60
MAX_TOTAL_WAIT = 20   # seconds slept across one call's retries: a CLI should not hang
MAX_PAGES = 20   # a package with this many pages of advisories has other problems


class OsvError(Exception):
    """An OSV call failed."""

    def __init__(self, message, status=None, path=None):
        super().__init__(message)
        self.status = status
        self.path = path


class OsvClient:
    """`cached_get(url) -> (text, status)` is optional: when given, vuln() goes through
    it, so a record is read from kospex's url_cache and shared by every package that
    names it. POST /query is never cached: it cannot be keyed by URL.
    """

    def __init__(self, base_url=DEFAULT_API_URL, session=None, cached_get=None,
                 sleep=time.sleep):
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.cached_get = cached_get
        self._sleep = sleep
        self.requests_made = 0

    def query(self, ecosystem, name, version):
        """Every vulnerability record affecting this package at this version.

        Returns a list of OSV vulnerability dicts, in OSV's order. Raises OsvError.
        """
        body = {"package": {"name": name, "ecosystem": ecosystem}, "version": version}
        vulns = []
        for _ in range(MAX_PAGES):
            data = self._request("POST", "/query", json=body)
            vulns.extend(data.get("vulns") or [])
            token = data.get("next_page_token")
            if not token:
                return vulns
            body = dict(body, page_token=token)
        raise OsvError(f"more than {MAX_PAGES} pages of results", path="/query")

    def vuln(self, osv_id):
        """One full vulnerability record, or None when OSV has no such ID (404)."""
        path = f"/vulns/{quote(osv_id, safe='')}"
        if self.cached_get is not None:
            text, status = self.cached_get(self.base_url + path)
            if text is None:
                if status == 404:
                    return None
                raise OsvError(f"GET {path} failed"
                               + (f" with HTTP {status}" if status else ""),
                               status=status, path=path)
            return self._json(text, path, status)
        try:
            return self._request("GET", path)
        except OsvError as error:
            if error.status == 404:
                return None
            raise

    def _request(self, method, path, **kwargs):
        url = self.base_url + path
        waited = 0.0
        for attempt in range(MAX_RETRIES + 1):
            self.requests_made += 1
            try:
                response = self.session.request(method, url, timeout=TIMEOUT,
                                                allow_redirects=False, **kwargs)
            except requests.RequestException as error:
                raise OsvError(f"{method} {path} failed: {type(error).__name__}",
                               path=path) from error
            status = response.status_code
            if status == 200:
                return self._json(response.text, path, status)
            retryable = status == 429 or status >= 500
            if retryable and attempt < MAX_RETRIES:
                wait = self._wait(response, attempt)
                if waited + wait <= MAX_TOTAL_WAIT:
                    waited += wait
                    self._sleep(wait)
                    continue
            raise OsvError(f"{method} {path} answered HTTP {status}", status=status,
                           path=path)
        raise OsvError(f"{method} {path} failed", path=path)   # pragma: no cover

    @staticmethod
    def _wait(response, attempt):
        """Seconds to wait: Retry-After if it is a number, else 1, 2, 4..., capped."""
        try:
            wait = float(response.headers.get("Retry-After", ""))
        except ValueError:
            wait = float(2 ** attempt)
        return max(0.0, min(wait, MAX_RATE_LIMIT_WAIT))

    @staticmethod
    def _json(text, path, status):
        try:
            data = json.loads(text)
        except ValueError as error:
            raise OsvError(f"{path} returned something that is not JSON",
                           status=status, path=path) from error
        if not isinstance(data, dict):
            raise OsvError(f"{path} returned an unexpected shape", status=status,
                           path=path)
        return data
