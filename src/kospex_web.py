""" Helper functions for kospex web UI """
import datetime
import os
import re
import shlex
import kospex_utils as KospexUtils
from kospex.extractors.registry import classify

# Friendly labels for the classify() kinds shown in the /osi/ commentary.
_KIND_LABELS = {
    "package": "Package manifests",
    "runtime": "Runtime versions",
    "container": "Container images",
    "unknown": "Unrecognised files",
    "sca_config": "SCA config",
    "lockfile": "Lockfiles",
}
# Kinds recognised but not a package source kospex scans (so not "awaiting a parser").
_NOT_A_SOURCE_KINDS = {"sca_config", "lockfile"}


def get_id_params(request_id,request_params=None):
    """
    Parse a request_id into either:
        repo_id
        org_key
        server
    if request_params is passed, we'll look at that, but the request_id takes precedence
    and return a dict with that key to be passed into a KospexQuery
    """
    params = {}

    # Only look at request_params if the request_id is None
    if request_id is None:

        if request_params:
            if repo_id := request_params.get("repo_id"):
                params["repo_id"] = repo_id
            elif org_key := request_params.get("org_key"):
                params["org_key"] = org_key
            elif server := request_params.get("server"):
                params["server"] = server
            elif author_email := request_params.get("author_email"):
                # Web URLs sometimes translate + to a space
                # so so we need to replace ' ' with +
                # Like in github emails like 109855528+USERNAME@users.noreply.github.com
                params["author_email"] = author_email.replace(" ","+")

        return params

    # These methods return a Dict if parsed or None if not
    if KospexUtils.parse_org_key(request_id):
        params["org_key"] = request_id
    elif KospexUtils.parse_repo_id(request_id):
        params["repo_id"] = request_id
    elif KospexUtils.is_base64(request_id):
        params["author_email"] = KospexUtils.decode_base64(request_id)
    else:
        params["server"] = request_id

    return params

def _humanise_seconds(seconds):
    """A whole-unit duration for display: "1 day", "90 minutes", "45 seconds"."""
    for unit, size in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if seconds % size == 0:
            n = seconds // size
            return f"{n} {unit}{'s' if n != 1 else ''}"
    return f"{seconds} second{'s' if seconds != 1 else ''}"

def dependency_freshness(rows, max_age_days=2, now=None, cache_seconds=None):
    """
    Summarise dependency_data.last_checked over the rows a page shows.
    Reports the OLDEST check, so one stale file can't hide behind fresh ones.
    A NULL last_checked means "never checked" (pre-0006 rows awaiting a
    re-sync), not "checked long ago" - it is counted separately and also
    marks the page stale. Returns None when there are no rows.

    cache_seconds is the deps.dev response cache TTL (url_cache_seconds()).
    A cache hit does not contact deps.dev, so the data can be up to that much
    older than last_checked; cache_window is that span for display, or None
    when nothing is cached.
    """
    if not rows:
        return None
    now = now or datetime.datetime.now(datetime.timezone.utc)
    checked = [r["last_checked"] for r in rows if r.get("last_checked")]
    never_checked = len(rows) - len(checked)
    oldest = min(checked) if checked else None
    stale = never_checked > 0
    if oldest:
        oldest_dt = datetime.datetime.strptime(oldest, "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=datetime.timezone.utc)
        stale = stale or (now - oldest_dt) > datetime.timedelta(days=max_age_days)
    return {
        "oldest": oldest,
        "newest": max(checked) if checked else None,
        "never_checked": never_checked,
        "stale": stale,
        "max_age_days": max_age_days,
        "cache_window": _humanise_seconds(cache_seconds) if cache_seconds else None,
    }

# Why a file's rows were never checked, keyed by its outcome in the latest
# OSI_EXTRACTION record (kospex.osi_outcomes vocabulary). {examined} is the date
# of that extraction. Every case but "no record" means the rows shown are left
# over from an earlier extraction that the latest one did not replace.
_NEVER_CHECKED_REASONS = {
    "empty": "Latest extraction ({examined}) found no packages in this file",
    "parse_error": "Latest extraction ({examined}) could not read this file",
    "extracted": "Latest extraction ({examined}) extracted this file, but did not refresh these rows",
    "unsupported": "Latest extraction ({examined}) has no parser for this file",
    "unclassified": "Latest extraction ({examined}) did not recognise this file",
    "not_a_manifest": "Latest extraction ({examined}) treated this file as not a package manifest",
}

def never_checked_files(rows, outcomes):
    """
    Group rows with no last_checked by (repo, file) and explain each from the
    repo's latest extraction outcome.

    outcomes: {repo_id: kospex.osi_queue.get_outcome() result, or None}.
    Returns a list of dicts: repo_id, file_path, rows, first_seen, reason.
    """
    files = {}
    for row in rows:
        if row.get("last_checked"):
            continue
        key = (row.get("_repo_id"), row.get("file_path"))
        entry = files.setdefault(key, {"repo_id": key[0], "file_path": key[1],
                                       "rows": 0, "first_seen": row.get("created_at")})
        entry["rows"] += 1
        if row.get("created_at") and (not entry["first_seen"] or row["created_at"] < entry["first_seen"]):
            entry["first_seen"] = row["created_at"]

    for entry in files.values():
        outcome = outcomes.get(entry["repo_id"])
        if not outcome:
            entry["reason"] = ("No extraction outcome recorded for this repository - "
                               "these rows predate it; re-run krunner osi to refresh them")
            continue
        examined = (outcome.get("examined_at") or "")[:10]
        file_outcome = outcome.get("files", {}).get(entry["file_path"])
        if file_outcome is None:
            entry["reason"] = (f"Not in the latest extraction ({examined}) - the file may have "
                               "been removed or is no longer detected as a dependency file")
        else:
            entry["reason"] = _NEVER_CHECKED_REASONS.get(
                file_outcome, f"Latest extraction ({{examined}}) recorded '{file_outcome}'"
            ).format(examined=examined)

    return sorted(files.values(), key=lambda e: (e["repo_id"] or "", e["file_path"] or ""))


# How each extraction outcome reads in the "nothing extracted" summary, in display order.
_OUTCOME_PHRASES = {
    "empty": "with no packages",
    "unsupported": "with no parser",
    "parse_error": "unreadable",
    "unclassified": "unrecognised",
    "not_a_manifest": "not a package manifest",
    "extracted": "extracted",
}


def _plural(n, word):
    return f"{n:,} {word}{'' if n == 1 else 's'}"


# What a server, org or repo name can contain, plus the '~' separators (and the
# '~~' a nested group encodes to).
_SAFE_SCOPE_ID = re.compile(r"[A-Za-z0-9._~-]+")


def safe_scope_id(scope_id, placeholder="<scope>"):
    """
    scope_id for a command the page tells the user to paste into a shell, or
    placeholder. The id comes from the request path, so anything outside a
    plain server/org/repo id could be shell syntax (CWE-77): a crafted link
    would otherwise show "krunner osi x;<anything>" as the fix to run.
    """
    if scope_id and _SAFE_SCOPE_ID.fullmatch(scope_id):
        return scope_id
    return placeholder


def unsupported_dependency_files(dependency_files):
    """
    Summarise the dependency files kospex has no parser for yet, so their
    packages are missing from /dependencies/ whether or not other files
    produced rows.

    Uses the rule behind the /osi/ "no parser yet" commentary in
    osi_extraction_view(): not supported, and not a kind kospex deliberately
    does not scan for packages (_NOT_A_SOURCE_KINDS, e.g. go.sum checksums).

    dependency_files: file_metadata rows tagged as dependency files.
    Returns {"total", "count", "by_name": {basename: count}, most common first},
    or None when every file has a parser.
    """
    by_name = {}
    for f in dependency_files or []:
        result = classify(f.get("Provider") or "")
        if result.supported or result.kind.value in _NOT_A_SOURCE_KINDS:
            continue
        basename = os.path.basename(f.get("Provider") or "")
        by_name[basename] = by_name.get(basename, 0) + 1
    if not by_name:
        return None
    return {
        "total": len(dependency_files),
        "count": sum(by_name.values()),
        "by_name": dict(sorted(by_name.items(), key=lambda kv: (-kv[1], kv[0]))),
    }


def dependencies_empty_state(scope_id, repo_scope=False, repo=None, file_count=0,
                             dependency_files=None, outcome=None, file_path=None):
    """
    Explain why /dependencies/ has no rows, and what the user should run.
    Only meaningful when the page has no data; the caller decides that.

    For a repo scope the steps run in pipeline order -- synced, file metadata
    collected, examined by `krunner osi` -- and the first missing step is the
    answer. Wider scopes cover many repos in different states, so they only
    say how to run the analysis for that scope.

    scope_id: the page's id (repo_id, org key, server) or None for all repos.
    repo_scope: True when scope_id is a repo_id; the rest apply only then.
    repo: the repos row, or None if the repo has not been synced.
    file_count: latest file_metadata rows for the repo.
    dependency_files: file_metadata rows tagged as dependency files.
    outcome: kospex.osi_queue.get_outcome() result, or None if never examined.

    Returns a dict: reason, title, detail, actions (commands to run, in
    order; may be empty), optionally actions_label (the line introducing
    them) and osi_url (link to the per-file view, or None).
    """
    osi_cmd = f"krunner osi {safe_scope_id(scope_id)}" if scope_id else "krunner osi -all"

    if not repo_scope:
        return {"reason": "no_data", "title": "No dependency data",
                "detail": "No dependencies have been recorded for the repositories in this "
                          "scope. Dependency analysis runs separately from sync.",
                "actions": [osi_cmd], "osi_url": None}

    osi_url = f"/osi/{scope_id}"
    dependency_files = dependency_files or []

    if repo is None:
        return {"reason": "not_synced", "title": "Repository not synced",
                "detail": f"kospex has no record of {scope_id}. Clone and sync it first, "
                          "then run dependency analysis.",
                "actions": ["kgit clone <repository url>", osi_cmd], "osi_url": None}

    if file_count == 0:
        path = repo.get("file_path")
        path = shlex.quote(path) if path else "<path to repository>"
        return {"reason": "no_file_metadata", "title": "File metadata has not been collected",
                "detail": "Dependency files are found from the file metadata recorded during "
                          "sync, and none has been recorded for this repository. The sync may "
                          "have failed or not finished. Re-sync it, then run dependency analysis.",
                "actions": [f"kospex sync-directory {path}", osi_cmd], "osi_url": None}

    if file_path:
        file_outcome = (outcome or {}).get("files", {}).get(file_path)
        if file_outcome is None:
            detail = f"{file_path} has not been examined by dependency analysis."
            actions = [osi_cmd]
        else:
            examined = (outcome.get("examined_at") or "")[:10]
            detail = _NEVER_CHECKED_REASONS.get(
                file_outcome, f"Latest extraction ({{examined}}) recorded '{file_outcome}'"
            ).format(examined=examined) + "."
            actions = []
        return {"reason": "file_not_extracted", "title": "No dependencies for this file",
                "detail": detail, "actions": actions, "osi_url": osi_url}

    if outcome is None:
        if not dependency_files:
            return {"reason": "no_dependency_files", "title": "No dependency files found",
                    "detail": "The file metadata for this repository contains no dependency "
                              "files, so there is nothing to analyse.",
                    "actions": [], "osi_url": None}
        # The files with no parser are listed by unsupported_dependency_files();
        # this says how many running the analysis will actually help with.
        parseable = sum(1 for f in dependency_files
                        if classify(f.get("Provider") or "").supported)
        if parseable:
            actions_label = f"{parseable:,} of them can be analysed by running:"
        else:
            actions_label = ("None of them can be analysed yet, but running this records "
                             "that they were examined:")
        return {"reason": "osi_not_run", "title": "Dependency analysis has not been run",
                "detail": f"Sync found {_plural(len(dependency_files), 'dependency file')} in "
                          "this repository, but krunner osi has not examined them yet.",
                "actions": [osi_cmd], "actions_label": actions_label, "osi_url": osi_url}

    examined_at = outcome.get("examined_at") or ""
    counts = {}
    for value in (outcome.get("files") or {}).values():
        counts[value] = counts.get(value, 0) + 1
    if counts:
        order = list(_OUTCOME_PHRASES)
        parts = [f"{n:,} {_OUTCOME_PHRASES.get(k, k)}"
                 for k, n in sorted(counts.items(), key=lambda kv: (order.index(kv[0])
                                    if kv[0] in order else len(order), kv[0]))]
        summary = (f"Dependency analysis on {examined_at[:10]} examined "
                   f"{_plural(sum(counts.values()), 'file')}: {', '.join(parts)}.")
    else:
        summary = f"Dependency analysis on {examined_at[:10]} found no dependency files."

    # Both UTC; normalise "YYYY-MM-DDTHH:MM:SSZ" to last_sync's "YYYY-MM-DD HH:MM:SS".
    last_sync = repo.get("last_sync") or ""
    stale = bool(last_sync) and examined_at.replace("T", " ").rstrip("Z") < last_sync
    actions = [osi_cmd] if stale else []
    if stale:
        summary += " The repository has been synced since, so re-run the analysis."
    return {"reason": "nothing_extracted", "title": "No dependencies extracted",
            "detail": summary, "actions": actions, "osi_url": osi_url}

def scope_context(params, page):
    """
    Build the _breadcrumb.html + _scope_label.html context for a sub-page
    (e.g. "Tenure") from get_id_params output. Always sets scope_type and
    scope_name; git_server (which turns the breadcrumb on) is only set for
    server, org and repo scopes.
    """
    if repo_id := params.get("repo_id"):
        if parsed := KospexUtils.parse_repo_id(repo_id):
            return {**parsed, "page": page, "scope_type": "Repository",
                    "scope_name": f"{parsed['org']}/{parsed['repo']}"}
    elif org_key := params.get("org_key"):
        if parsed := KospexUtils.parse_org_key(org_key):
            return {**parsed, "page": page, "scope_type": "Organisation",
                    "scope_name": parsed["org"]}
    elif server := params.get("server"):
        return {"git_server": server, "page": page, "scope_type": "Server",
                "scope_name": server}
    elif author_email := params.get("author_email"):
        return {"scope_type": "Developer", "scope_name": author_email}
    return {"scope_type": "All repositories", "scope_name": ""}


def osi_extraction_view(files, extracted_keys):
    """Annotate /osi/ dependency files with realized extraction status and build
    the commentary buckets.

    files: list of file_metadata dicts (each with _repo_id and Provider).
    extracted_keys: set of (repo_id, file_path) that have extracted dependency rows.

    Sets file["extracted"] = True/False on each file. Returns (files, commentary)
    where commentary now has three keys:
      no_parser {label: {basename: count}} for parser-planned kinds,
      not_a_source {label: {basename: count}} for sca_config/lockfile,
      not_scanned int:
      - no_parser: not-extracted files whose type has no parser (classify.supported False)
        and is genuinely awaiting a parser, grouped by friendly label then basename.
      - not_a_source: not-extracted files whose type is recognised but not a package
        source kospex scans (sca_config/lockfile), grouped by friendly label then basename.
      - not_scanned: count of not-extracted files whose type IS supported (just not scanned).
    """
    no_parser = {}
    not_a_source = {}
    not_scanned = 0

    for f in files:
        key = (f.get("_repo_id"), f.get("Provider"))
        extracted = key in extracted_keys
        f["extracted"] = extracted
        if extracted:
            continue

        result = classify(f.get("Provider") or "")
        if result.supported:
            not_scanned += 1
            continue

        basename = os.path.basename(f.get("Provider") or "")
        kind = result.kind.value
        label = _KIND_LABELS.get(kind, kind)
        bucket = not_a_source if kind in _NOT_A_SOURCE_KINDS else no_parser
        bucket.setdefault(label, {})
        bucket[label][basename] = bucket[label].get(basename, 0) + 1

    return files, {"no_parser": no_parser, "not_a_source": not_a_source,
                   "not_scanned": not_scanned}
