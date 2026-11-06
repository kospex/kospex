"""`kgit sync --org` / `--user`: list an owner's repositories, record them, sync them.

Design: changes/202610-kgit-sync-org.md (#335)
Manifest: kospex.org_sync_manifest (migration 0012)

The CLI in kgit.py parses options and prints. This module holds the parts worth
testing on their own: what a target means, which repositories are skipped and why,
how a listing becomes manifest items, and the per-repository loop that has to
survive one repository failing.
"""
import os
import re
from pathlib import Path
from urllib.parse import urlparse

from kospex_git import KospexGit
import kospex.org_sync_manifest as manifest

GITHUB_HOST = "github.com"

# GitHub's own account types, from GET /users/{name}.
GITHUB_ORG = "Organization"
GITHUB_USER = "User"

ENV_CLONE_FORKS_DEFAULT = "KOSPEX_CLONE_FORKS_DEFAULT"
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"", "0", "false", "no", "off"}

SKIP_FORK = "fork"
SKIP_ARCHIVED = "archived"

# GitHub owner names: alphanumerics and single hyphens, no leading hyphen.
_OWNER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")


def parse_target(target):
    """The owner name from a target, as `(host, owner)`. Raises ValueError.

    Accepts `acme`, `github.com/acme`, `https://github.com/acme` and the org key
    `github.com~acme`. A bare name means github.com. A path with a repository in
    it is refused: that's `kgit sync URL`, the single-repository form.
    """
    raw = (target or "").strip().rstrip("/")
    if not raw:
        raise ValueError("No organisation or user given.")

    if "~" in raw:
        host, _, owner = raw.partition("~")
    elif "://" in raw:
        url = urlparse(raw)
        host, owner = url.hostname or "", url.path.strip("/")
    elif "/" in raw:
        host, _, owner = raw.partition("/")
        if "." not in host:
            # `acme/app` is an owner and a repository, not a host and an owner.
            host, owner = GITHUB_HOST, raw
    else:
        host, owner = GITHUB_HOST, raw

    host = host.lower()
    if host != GITHUB_HOST:
        raise ValueError(f"Only GitHub is supported for now, not {host or target!r}.")
    if "/" in owner or "~" in owner:
        raise ValueError(
            f"{target!r} looks like a repository, not an organisation or user. "
            "Use `kgit sync URL` to sync one repository.")
    if not _OWNER_RE.match(owner):
        raise ValueError(f"{owner!r} isn't a valid GitHub organisation or user name.")
    return host, owner


def org_key(host, owner):
    """The org key, as repo ids spell it: lowercase (`github.com~acme`)."""
    return f"{host.lower()}~{owner.lower()}"


def forks_default(environ=None):
    """Whether forks are synced by default: KOSPEX_CLONE_FORKS_DEFAULT, else False.

    Raises ValueError for a value that's neither true nor false, rather than
    guessing: a typo here silently decides whether hundreds of forks are cloned.
    """
    environ = os.environ if environ is None else environ
    value = (environ.get(ENV_CLONE_FORKS_DEFAULT) or "").strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ValueError(
        f"{ENV_CLONE_FORKS_DEFAULT}={environ.get(ENV_CLONE_FORKS_DEFAULT)!r} "
        "isn't true or false.")


def build_items(repos, owner, include_forks=False, skip_archived=False,
                host=GITHUB_HOST):
    """Turn a listing into manifest items. Returns `(items, others)`.

    Every repository the owner returned becomes an item. The ones that won't be
    synced (forks by default, archived with `skip_archived`) are `skipped` with
    the reason, so the manifest still says the owner has them.

    `others` counts repositories owned by someone else. Listing your own account
    returns the repositories you collaborate on too, and those belong to their
    own owners, not to this one.
    """
    items, others = [], 0
    for repo in repos:
        full_name = repo.get("name") or ""
        repo_owner, _, name = full_name.partition("/")
        if not name or repo_owner.lower() != owner.lower():
            others += 1
            continue

        item = {
            "_repo_id": KospexGit.generate_repo_id(host, repo_owner, name),
            "clone_url": repo.get("clone_url"),
            "ssh_url": repo.get("ssh_url"),
            "provider_repo_id": f"{host}:{repo['id']}" if repo.get("id") else None,
            "archived": bool(repo.get("archived")),
            "fork": bool(repo.get("fork")),
            "fork_parent": repo.get("fork_parent"),
            "fork_source": repo.get("fork_source"),
            "private": (repo.get("visibility") or "").lower() == "private",
            "pushed_at": repo.get("pushed_at"),
        }
        if item["fork"] and not include_forks:
            item["status"], item["detail"] = manifest.SKIPPED, SKIP_FORK
        elif item["archived"] and skip_archived:
            item["status"], item["detail"] = manifest.SKIPPED, SKIP_ARCHIVED
        items.append(item)
    return items, others


def compare_with_run(db, run_id, items):
    """Repositories new since a run, and gone since it. For an unsaved listing.

    Used when `-inventory` finds an unfinished run and keeps it: the fresh
    listing isn't saved, so this is how it's compared.
    """
    in_run = {row["_repo_id"] for row in db.query(
        f"SELECT _repo_id FROM {manifest.TBL_ORG_SYNC_ITEMS} WHERE run_id = ?", [run_id])}
    listed = {item["_repo_id"] for item in items}
    return sorted(listed - in_run), sorted(in_run - listed)


def lock_path(home, key):
    """One lock file per org, under KOSPEX_HOME, so two orgs can sync at once."""
    safe = re.sub(r"[^A-Za-z0-9._~-]", "_", key)
    return Path(home) / f"org-sync-{safe}.lock"


def no_prompt_env():
    """Environment that makes git fail instead of asking for credentials.

    The same effect as `kgit pull --no-prompt`'s `-c credential.interactive=false`,
    set through the environment because clone_repo builds its own git command.
    """
    return {
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "credential.interactive",
        "GIT_CONFIG_VALUE_0": "false",
    }


def sync_items(db, run_id, items, clone, sync, conflict=None, use_ssh=False,
               on_item=None):
    """Clone or pull, then sync, each item. Returns a dict of counts.

    Each item is marked `in_progress` before its work starts, then `synced`,
    `skipped` or `failed`. One repository failing, even by raising, never stops
    the rest. A KeyboardInterrupt does stop the run: the item it interrupted is
    left `in_progress`, and the next invocation picks it up again.

    The work is passed in, so this loop can be tested without git:

    * `clone(url)` returns the local path, or None on failure.
    * `sync(path, repo_id)` syncs it to the database. It may raise.
    * `conflict(url)` returns True when cloning would repoint a repository
      already synced from another directory.
    * `on_item(item, status, detail)` is called after each one, for printing.
    """
    counts = {manifest.SYNCED: 0, manifest.SKIPPED: 0, manifest.FAILED: 0}
    for item in items:
        repo_id = item["_repo_id"]
        url = (item.get("ssh_url") if use_ssh else item.get("clone_url")) or item.get("clone_url")
        manifest.mark(db, run_id, repo_id, manifest.IN_PROGRESS)

        status, detail = manifest.SYNCED, None
        try:
            if not url:
                status, detail = manifest.FAILED, "no clone URL in the listing"
            elif conflict and conflict(url):
                status, detail = manifest.SKIPPED, "already synced from another directory"
            else:
                path = clone(url)
                if not path:
                    status, detail = manifest.FAILED, "clone or pull failed"
                else:
                    sync(path, repo_id)
                    manifest.record_provider_ids(db, run_id, repo_id=repo_id)
        except KeyboardInterrupt:
            raise
        except Exception as e:  # one repository must never stop the run
            status, detail = manifest.FAILED, f"{type(e).__name__}: {e}"

        manifest.mark(db, run_id, repo_id, status, detail=detail)
        counts[status] += 1
        if on_item:
            on_item(item, status, detail)
    return counts
