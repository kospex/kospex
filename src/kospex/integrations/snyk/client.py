"""SnykClient: a small read-only client for the Snyk REST API.

Every request is a GET with `?version=REST_VERSION` and
`Authorization: token <SNYK_TOKEN>`. Responses are JSON:API, paged by cursor
through `links.next`.

Most of the Snyk API is Enterprise-only. On the free plan (measured 2026-11-15)
/self, /orgs and /orgs/{id}/targets answer 200, while projects, issues and
package issues return 403 with a bare "Forbidden" body. That body gives no
reason, so a 403 is reported as "not available to this token or plan",
never as an empty result.
"""
import os
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

import requests

# Pinned GA version. The issues, projects and orgs schemas are identical in
# 2025-11-05 and 2026-09-09 (compared 2026-11-15). Snyk supports a GA version
# for at least six months after the next GA release.
REST_VERSION = "2025-11-05"
DEFAULT_API_URL = "https://api.snyk.io"
TOKEN_ENV_VAR = "SNYK_TOKEN"
# Same variable name as the Snyk CLI uses to select a region, e.g. https://api.eu.snyk.io
API_URL_ENV_VAR = "SNYK_API"

PAGE_LIMIT = 100        # the REST maximum for orgs, projects and issues (10..100, step 10)
PROBE_LIMIT = 10        # the REST minimum: a probe only needs to see whether a call answers
MAX_RATE_LIMIT_WAIT = 60
MAX_RETRIES = 3


class SnykError(Exception):
    """A Snyk API call failed."""

    def __init__(self, message, status=None, path=None):
        super().__init__(message)
        self.status = status
        self.path = path


class SnykAuthError(SnykError):
    """401: the token is missing, wrong or expired."""


class SnykNotAvailable(SnykError):
    """403: the endpoint isn't available to this token or Snyk plan."""


class SnykRateLimited(SnykError):
    """429 that persisted after waiting."""


@dataclass
class ProbeResult:
    name: str
    path: str
    status: int | None
    detail: str


def api_url_from_env(value=None):
    """The base URL (scheme + host), from SNYK_API or the default.

    Accepts the forms the Snyk CLI documents (with or without /v1 or /rest)
    and refuses anything that isn't https, because the token goes with every
    request.
    """
    url = (value if value is not None else os.environ.get(API_URL_ENV_VAR, "")).strip()
    if not url:
        return DEFAULT_API_URL
    url = url.rstrip("/")
    for suffix in ("/v1", "/rest"):
        if url.endswith(suffix):
            url = url[: -len(suffix)]
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.netloc or parts.path not in ("", "/"):
        raise SnykError(f"{API_URL_ENV_VAR} must be an https base URL such as "
                        f"{DEFAULT_API_URL}, got {url!r}")
    return f"https://{parts.netloc}"


def token_from_env():
    """SNYK_TOKEN, stripped; None when unset or blank."""
    token = os.environ.get(TOKEN_ENV_VAR, "").strip()
    return token or None


def _error_detail(response):
    try:
        errors = response.json().get("errors") or []
    except (ValueError, AttributeError):
        errors = []
    for e in errors:
        detail = e.get("detail") or e.get("title")
        if detail:
            return str(detail)
    return response.reason or f"HTTP {response.status_code}"


class SnykClient:
    """Read-only Snyk REST client. Counts requests in `self.requests`."""

    def __init__(self, token=None, api_url=None, version=REST_VERSION,
                 session=None, timeout=30, sleep=time.sleep):
        self.token = token if token is not None else token_from_env()
        if not self.token:
            raise SnykAuthError(f"No Snyk token: set {TOKEN_ENV_VAR} in the environment "
                                "or in ~/kospex/kospex.env")
        self.api_url = api_url_from_env(api_url)
        self.rest_url = f"{self.api_url}/rest"
        self.version = version
        self.session = session or requests.Session()
        self.timeout = timeout
        self.sleep = sleep
        self.requests = 0

    # -- transport ---------------------------------------------------------

    def _headers(self):
        return {"Authorization": f"token {self.token}",
                "Accept": "application/vnd.api+json"}

    def _send(self, url, params):
        """One GET, waiting out 429s. Returns the response, whatever its status."""
        for attempt in range(MAX_RETRIES + 1):
            self.requests += 1
            response = self.session.get(url, params=params, headers=self._headers(),
                                        timeout=self.timeout)
            if response.status_code != 429 or attempt == MAX_RETRIES:
                return response
            try:
                wait = int(response.headers.get("Retry-After", "1"))
            except ValueError:
                wait = 1
            self.sleep(min(max(wait, 1), MAX_RATE_LIMIT_WAIT))
        return response

    def _raise_for(self, response, path):
        status = response.status_code
        if status == 200:
            return
        detail = _error_detail(response)
        if status == 401:
            raise SnykAuthError(f"{path}: 401 {detail}. Check {TOKEN_ENV_VAR}.", status, path)
        if status == 403:
            raise SnykNotAvailable(
                f"{path}: 403 {detail}. Not available to this token or Snyk plan "
                "(most of the Snyk API needs an Enterprise plan); Snyk's response "
                "doesn't say which.", status, path)
        if status == 429:
            raise SnykRateLimited(f"{path}: 429 rate limited after {MAX_RETRIES} retries.",
                                  status, path)
        raise SnykError(f"{path}: {status} {detail}", status, path)

    def _next_url(self, link):
        """Resolve a links.next value, refusing one that points at another host.

        The token is sent with every request, so following a `next` link to a
        different origin would hand it over (CWE-201).
        """
        if isinstance(link, dict):
            link = link.get("href")
        if not link:
            return None
        if link.startswith(("https://", "http://")):
            parts = urlsplit(link)
            if f"{parts.scheme}://{parts.netloc}" != self.api_url:
                raise SnykError(f"Refusing a paging link to another host: {parts.netloc}")
            return link
        if link.startswith("/rest/"):
            return f"{self.api_url}{link}"
        return f"{self.rest_url}{link if link.startswith('/') else '/' + link}"

    def get(self, path, **params):
        """One GET of a REST path; returns the decoded JSON body or raises SnykError."""
        params = {k: v for k, v in params.items() if v is not None}
        params["version"] = self.version
        response = self._send(f"{self.rest_url}{path}", params)
        self._raise_for(response, path)
        return response.json()

    def paginate(self, path, max_items=None, **params):
        """Yield every item in `data` across pages, following links.next.

        max_items stops early (and requests no more pages) once reached.
        """
        params = {k: v for k, v in params.items() if v is not None}
        params.setdefault("limit", PAGE_LIMIT)
        params["version"] = self.version
        url, query = f"{self.rest_url}{path}", params
        seen = 0
        while url:
            response = self._send(url, query)
            self._raise_for(response, path)
            body = response.json()
            for item in body.get("data") or []:
                yield item
                seen += 1
                if max_items is not None and seen >= max_items:
                    return
            url = self._next_url((body.get("links") or {}).get("next"))
            query = None  # a next link carries its own query string, version included

    # -- endpoints ---------------------------------------------------------

    def self_user(self):
        return self.get("/self").get("data") or {}

    def orgs(self):
        return list(self.paginate("/orgs"))

    def projects(self, org_id, ids=None, issue_counts=False):
        return list(self.paginate(
            f"/orgs/{org_id}/projects",
            ids=",".join(ids) if ids else None,
            **({"meta.latest_issue_counts": "true"} if issue_counts else {})))

    def issues(self, org_id, issue_type="package_vulnerability", statuses=("open",),
               severities=None, project_id=None, ignored=None, max_items=None):
        params = {
            "type": issue_type,
            "status": ",".join(statuses) if statuses else None,
            "effective_severity_level": ",".join(severities) if severities else None,
            "ignored": None if ignored is None else str(bool(ignored)).lower(),
        }
        if project_id:
            params["scan_item.id"] = project_id
            params["scan_item.type"] = "project"
        return self.paginate(f"/orgs/{org_id}/issues", max_items=max_items, **params)

    # -- probe -------------------------------------------------------------

    def probe(self, org_id=None):
        """One request per endpoint kospex uses, each at the minimum page size.

        Returns ProbeResults; never raises for an HTTP status. With no org_id,
        the first org /orgs returns is used for the org-scoped calls.
        """
        results = []

        def call(name, path, **params):
            params["version"] = self.version
            try:
                response = self._send(f"{self.rest_url}{path}", params)
            except requests.RequestException as e:
                results.append(ProbeResult(name, path, None, f"request failed: {e}"))
                return None
            if response.status_code == 200:
                body = response.json()
                data = body.get("data")
                count = len(data) if isinstance(data, list) else 1
                more = " (more)" if (body.get("links") or {}).get("next") else ""
                results.append(ProbeResult(name, path, 200, f"{count} item(s){more}"))
                return body
            results.append(ProbeResult(name, path, response.status_code,
                                       _error_detail(response)))
            return None

        call("self", "/self")
        orgs = call("orgs", "/orgs", limit=PROBE_LIMIT)
        if org_id is None and orgs and orgs.get("data"):
            org_id = orgs["data"][0]["id"]
        if org_id is None:
            return results
        call("projects", f"/orgs/{org_id}/projects", limit=PROBE_LIMIT)
        call("issues", f"/orgs/{org_id}/issues", limit=PROBE_LIMIT,
             type="package_vulnerability")
        call("targets", f"/orgs/{org_id}/targets", limit=PROBE_LIMIT)
        return results
