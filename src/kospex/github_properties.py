"""`kgit github --properties`: GitHub custom property values, one JSONL line per repo.

Two ways to read them, with the same output:

- org level:  GET /orgs/{org}/properties/values, ~1 request per 100 repos. Needs
  org membership (classic PAT: `read:org`, plus `repo` for private/internal),
  and returns only the repos the token can see.
- repo level: GET /repos/{owner}/{repo}/properties/values, one request per repo.
  Needs only read access to each repo.

Line shape (the contract for whatever loads the file):

    {"repo_id", "org", "repo", "level", "fetched_at", "properties": {name: value}}
    {"repo_id", "org", "repo", "level", "fetched_at", "error": "HTTP 404 Not Found"}

A repo with nothing set has `"properties": {}`; one that couldn't be read has
`error` and no `properties`, so the two can't be confused. Multi-select values
stay arrays. An unset property is left out (see normalise). `level` and `fetched_at` are on every
line so files from several orgs and runs can be concatenated.
"""
import json
import os
import tempfile
from datetime import datetime, timezone

from kospex_git import KospexGit
import kospex_utils as KospexUtils

GITHUB_HOST = "github.com"
LEVEL_ORG = "org"
LEVEL_REPO = "repo"


class RateLimited(Exception):
    """The rate limit was hit partway through a repo-level run.

    Raised rather than recorded per repo: every remaining repo would get an
    error line that reads like an access problem.
    """


def now_utc():
    """The run's timestamp, shared by every line it writes."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalise(properties):
    """GitHub's [{property_name, value}] as {name: value}, without unset properties.

    The org endpoint lists every property in the org's schema, with null for
    one a repo hasn't set; the repo endpoint leaves unset properties out
    (measured 2026-11-09). Dropping nulls makes both levels write the same line,
    and a missing key the one way to say "unset".
    """
    return {p["property_name"]: p["value"] for p in properties or []
            if p.get("value") is not None}


def _line(owner, repo, level, fetched_at):
    return {
        "repo_id": KospexGit.generate_repo_id(GITHUB_HOST, owner, repo),
        "org": owner,
        "repo": repo,
        "level": level,
        "fetched_at": fetched_at,
    }


def org_level_lines(gh, org, fetched_at):
    """Every visible repo's properties from the org endpoint.

    Raises GitHubListingIncomplete if any page fails, so nothing partial is written.
    """
    url = f"{gh.base_url}/orgs/{org}/properties/values"
    lines = []
    for item in gh._paginate(url):
        owner, _, repo = item["repository_full_name"].partition("/")
        line = _line(owner, repo, LEVEL_ORG, fetched_at)
        line["properties"] = normalise(item.get("properties"))
        lines.append(line)
    return lines


def list_org_repos(gh, org):
    """(owner, repo) for every repo in the org listing, for a repo-level run.

    Raises GitHubListingIncomplete if any page fails.
    """
    return [(r["owner"]["login"], r["name"]) for r in gh._get_org_repos(org, repo_type="all")]


def _error_text(response):
    if response is None:
        return "no response"
    text = f"HTTP {response.status_code}"
    try:
        message = response.json().get("message")
    except (ValueError, AttributeError):
        message = None
    return f"{text} {message}" if message else text


def repo_level_lines(gh, repos, fetched_at):
    """One request per (owner, repo). A repo that fails gets an `error` line.

    Raises RateLimited if the rate limit is hit.
    """
    lines = []
    for owner, repo in repos:
        url = f"{gh.base_url}/repos/{owner}/{repo}/properties/values"
        data, response = gh._make_request(url)
        if gh.throttled:
            raise RateLimited(f"{owner}/{repo}")
        line = _line(owner, repo, LEVEL_REPO, fetched_at)
        if data is None:
            line["error"] = _error_text(response)
        else:
            line["properties"] = normalise(data)
        lines.append(line)
    return lines


def parse_github_repo_id(repo_id):
    """(owner, repo) from a github.com repo_id. Raises ValueError otherwise."""
    parsed = KospexUtils.parse_repo_id(repo_id)
    if not parsed or parsed.get("git_server") != GITHUB_HOST or "/" in parsed["org"]:
        raise ValueError(f"Not a github.com repo_id: {repo_id!r} "
                         "(expected github.com~OWNER~REPO)")
    return parsed["org"], parsed["repo"]


def write_jsonl(path, lines):
    """Write one JSON object per line, replacing the file atomically.

    UTF-8 without \\u escapes, and newline-terminated so files concatenate cleanly.
    """
    path = os.fspath(path)
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".props-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for line in lines:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def summarise(lines):
    errors = sum(1 for l in lines if "error" in l)
    with_props = sum(1 for l in lines if l.get("properties"))
    return {"repos": len(lines), "with_properties": with_props,
            "no_properties": len(lines) - errors - with_props, "errors": errors}
