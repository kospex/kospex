""" Helper functions for kospex web UI """
import os
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
