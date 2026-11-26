"""What to ask GitLab for, and what to do with the answer.

Listing groups and projects, plus the pure filtering behind a clone list.
Nothing here touches the kospex database.
"""
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlsplit

from kospex.integrations.gitlab.client import GitLabError

# Offset paging in id order, so a project active mid-listing can't move between
# pages and be missed or repeated.
_STABLE_ORDER = {"order_by": "id", "sort": "asc"}

SKIP_EMPTY = "empty"
SKIP_FORK = "fork"
SKIP_ARCHIVED = "archived"

# Fields GitLab returns to maintainers that must not be written to a file.
_SECRET_FIELDS = {"runners_token"}


def group_ref(value, server):
    """A group's full path or numeric id, from what a person might type or paste.

    A URL must be on `server`: `https://host/groups/acme/platform` (and its
    `/-/...` pages) is `acme/platform`.
    """
    text = (value or "").strip()
    if "://" in text:
        url, base = urlsplit(text), urlsplit(server)
        if url.netloc.lower() != base.netloc.lower():
            raise GitLabError(f"{text} is not on {server}. Use --server for that host.")
        path = url.path.removeprefix(base.path.rstrip("/")).strip("/")
        path = path.removeprefix("groups/").split("/-/")[0]
    else:
        path = text
    path = path.strip("/")
    if not path:
        raise GitLabError(f"No group in {value!r}. Give a full path such as acme/platform.")
    return path


def _group_url(client, group):
    return f"/groups/{quote(group_ref(group, client.server), safe='')}"


def _by(field, items):
    return sorted(items, key=lambda item: str(item.get(field) or "").lower())


def list_groups(client, group=None):
    """The groups the token's user is a member of, or every level under `group`."""
    path = f"{_group_url(client, group)}/descendant_groups" if group else "/groups"
    return _by("full_path", client.paginate(path, dict(_STABLE_ORDER)))


def list_projects(client, group):
    """Every project in the group's tree, without ones merely shared into it."""
    items = client.paginate(f"{_group_url(client, group)}/projects",
                            {"include_subgroups": "true", "with_shared": "false",
                             **_STABLE_ORDER})
    return _by("path_with_namespace", items)


def active_projects(projects, days, now=None):
    """Projects with GitLab activity in the last `days`.

    `last_activity_at` covers more than pushes. An unknown date is not active.
    """
    since = (now or datetime.now(timezone.utc)) - timedelta(days=days)

    def active(project):
        try:
            then = datetime.fromisoformat(project.get("last_activity_at") or "")
        except (TypeError, ValueError):
            return False
        return (then if then.tzinfo else then.replace(tzinfo=timezone.utc)) >= since

    return [p for p in projects if active(p)]


def skip_reason(project, include_forks=False, skip_archived=False):
    """Why a project is left out of the clone list, or None if it goes in."""
    if project.get("empty_repo"):
        return SKIP_EMPTY
    if project.get("forked_from_project") and not include_forks:
        return SKIP_FORK
    if project.get("archived") and skip_archived:
        return SKIP_ARCHIVED
    return None


def clone_urls(projects, ssh=False, include_forks=False, skip_archived=False):
    field = "ssh_url_to_repo" if ssh else "http_url_to_repo"
    return [p[field] for p in projects
            if p.get(field) and not skip_reason(p, include_forks, skip_archived)]


def without_secrets(items):
    """A copy of the API data with secret fields removed, at any depth."""
    if isinstance(items, dict):
        return {k: without_secrets(v) for k, v in items.items() if k not in _SECRET_FIELDS}
    if isinstance(items, list):
        return [without_secrets(v) for v in items]
    return items


def printable(value):
    """Text from the server, safe to print: control characters shown, not sent (CWE-150)."""
    if value is None:
        return ""
    return "".join(f"\\x{ord(c):02x}" if ord(c) < 32 or 127 <= ord(c) < 160 else c
                   for c in str(value))
