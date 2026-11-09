"""`kgit github --properties`: GitHub custom property values, one JSONL line per repo.

Two ways to read them, with the same output:

- org level:  GET /orgs/{org}/properties/values, ~1 request per 100 repos. Needs
  org membership (classic PAT: `read:org`, plus `repo` for private/internal),
  and returns only the repos the token can see.
- repo level: GET /repos/{owner}/{repo}/properties/values, one request per repo.
  Needs only read access to each repo.

Line shape (the contract for whatever loads the file). Every line has the same
keys, so a loader that infers columns from the first rows (sqlite-utils reads
100) never meets a new one later:

    {"repo_id", "github_repo_id", "org", "repo", "level", "fetched_at", "properties": {name: value}, "error": null}
    {"repo_id", "github_repo_id", "org", "repo", "level", "fetched_at", "properties": null, "error": "HTTP 404 Not Found"}

`repo_id` is the kospex id (github.com~owner~repo, lowercased), for joining to
the kospex DB. `github_repo_id` is GitHub's numeric id, which survives renames
and transfers; it is null only when a single -repo_id couldn't be read.

`"properties": {}` means nothing is set; `"properties": null` with an error means
the repo couldn't be read, so its properties are unknown. Multi-select values
stay arrays. An unset property is left out (see normalise). `level` and
`fetched_at` are on every line so files from several orgs and runs can be
concatenated.
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


def _line(owner, repo, github_repo_id, level, fetched_at):
    return {
        "repo_id": KospexGit.generate_repo_id(GITHUB_HOST, owner, repo),
        "github_repo_id": github_repo_id,
        "org": owner,
        "repo": repo,
        "level": level,
        "fetched_at": fetched_at,
        "properties": None,
        "error": None,
    }


def org_level_lines(gh, org, fetched_at):
    """Every visible repo's properties from the org endpoint.

    Raises GitHubListingIncomplete if any page fails, so nothing partial is written.
    """
    url = f"{gh.base_url}/orgs/{org}/properties/values"
    lines = []
    for item in gh._paginate(url):
        owner, _, repo = item["repository_full_name"].partition("/")
        line = _line(owner, repo, item.get("repository_id"), LEVEL_ORG, fetched_at)
        line["properties"] = normalise(item.get("properties"))
        lines.append(line)
    return lines


def list_org_repos(gh, org):
    """(owner, repo, github_repo_id) for every repo in the org listing, for a repo-level run.

    Raises GitHubListingIncomplete if any page fails.
    """
    return [(r["owner"]["login"], r["name"], r["id"])
            for r in gh._get_org_repos(org, repo_type="all")]


def lookup_repo(gh, owner, repo):
    """(owner, repo, github_repo_id) for a single -repo_id, in GitHub's casing.

    The repo properties endpoint doesn't return the numeric id, so this costs one
    extra request. If the repo can't be read, the names are kept and the id is
    None; the properties request that follows records the error line.

    Raises RateLimited if the rate limit is hit.
    """
    data, _ = gh._make_request(f"{gh.base_url}/repos/{owner}/{repo}",
                              quiet_statuses=(403, 404))
    if gh.throttled:
        raise RateLimited(f"{owner}/{repo}")
    if not data:
        return owner, repo, None
    return data["owner"]["login"], data["name"], data["id"]


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
    """One request per (owner, repo, github_repo_id). A repo that fails gets an `error` line.

    Raises RateLimited if the rate limit is hit.
    """
    lines = []
    for owner, repo, github_repo_id in repos:
        url = f"{gh.base_url}/repos/{owner}/{repo}/properties/values"
        data, response = gh._make_request(url)
        if gh.throttled:
            raise RateLimited(f"{owner}/{repo}")
        line = _line(owner, repo, github_repo_id, LEVEL_REPO, fetched_at)
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


def notes(lines, org_scope):
    """What the summary counts can't show, for the console after a run.

    Measured 2026-11-09: a repo's property values are readable by anyone who can
    read the repo (public repos even anonymously), and a repo the token can't
    read returns 404, recorded as an error line. So `{}` really means "none set".
    What *can* be silently missing is whole repos: both the org endpoint and the
    org repo listing return only the repos the token can see, and for a
    non-member that's public repos only.
    """
    out = []
    if org_scope:
        out.append("Only repos this token can see are included. If the token isn't an "
                   "org member, that's public repos only: private and internal repos "
                   "are left out, with no error.")
    out.append("{} means no properties are set. A repo the token can't read is an "
               "error line, not {}.")
    s = summarise(lines)
    if s["repos"] and not s["with_properties"] and not s["errors"]:
        out.append(f"None of the {s['repos']} repos has a property set; the org may not "
                   "use custom properties.")
    return out


def summarise(lines):
    errors = sum(1 for l in lines if l.get("error"))
    with_props = sum(1 for l in lines if l.get("properties"))
    return {"repos": len(lines), "with_properties": with_props,
            "no_properties": len(lines) - errors - with_props, "errors": errors}
