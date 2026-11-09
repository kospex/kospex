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
import kospex_utils as KospexUtils
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


def split_owner(raw):
    """`(host, owner)` from the forms an owner can be given in, without judging them.

    - `github.com~acme`: an org key, through parse_org_key, so a nested
      `gitlab.com~group~~sub` decodes to the owner `group/sub`. A value it doesn't
      accept (a repo id) comes back as `(None, raw)` for the caller to refuse.
    - `https://host/acme`: the URL's host and path. Not parse_git_remote: it
      parses repository URLs and returns None for an owner's URL.
    - `host.tld/acme`: a first segment with a dot is a host.
    - `acme`, or `acme/app` (an owner and a repository, not a host): host None.

    host is None when the value names none; parse_target and resolve_pull_org
    decide what that means.
    """
    if "~" in raw:
        parsed = KospexUtils.parse_org_key(raw)
        return (parsed["git_server"], parsed["org"]) if parsed else (None, raw)
    if "://" in raw:
        url = urlparse(raw)
        return url.hostname or "", url.path.strip("/")
    if "/" in raw:
        host, _, owner = raw.partition("/")
        if "." in host:
            return host, owner.strip("/")
    return None, raw


def resolve_pull_org(value, repos):
    """The org `kgit pull --org VALUE` means, as the stored `(git_server, git_owner)`.

    repos: `(git_server, git_owner, git_repo)` for every repo in the DB. `pull`
    works on what kospex already has, on any host, so unlike `kgit sync --org`
    a bare name isn't taken to mean github.com: it's matched against the owners
    in the DB, case-insensitively. Exactly one match is used. None, or more than
    one, raises ValueError naming what to use instead; it never picks one.
    """
    raw = (value or "").strip().rstrip("/")
    if not raw:
        raise ValueError("No org given.")
    repos = [(s or "", o or "", r or "") for s, o, r in repos]
    if "~" in raw and not KospexUtils.parse_org_key(raw):
        if KospexUtils.parse_repo_id(raw):
            raise ValueError(f"{raw!r} is a repository id, not an org. "
                             f"Use: kgit pull {raw}")
        raise ValueError(f"{raw!r} isn't an org key (<server>~<owner>).")

    host, owner = split_owner(raw)
    if host is not None:
        host = host.lower()
    owner_l = owner.lower()

    orgs = sorted({(s, o) for s, o, _ in repos})
    servers = {s.lower() for s, _ in orgs}
    if not owner or (host is None and owner_l in servers
                     and not any(o.lower() == owner_l for _, o in orgs)):
        # A host on its own, or a host whose repos have no owner
        # (go.googlesource.com): that's --server, not --org.
        server = host or owner
        if server and server.lower() in servers:
            raise ValueError(f"{raw!r} is a git server in kospex, not an org. "
                             f"Use: kgit pull --server {server}")
        raise ValueError(f"No org {raw!r} in kospex.")

    matches = [(s, o) for s, o in orgs
               if o.lower() == owner_l and (host is None or s.lower() == host)]
    if len(matches) == 1:
        return matches[0]
    if matches:
        keys = ", ".join(KospexGit.generate_org_key(s, o) for s, o in matches)
        raise ValueError(f"{raw!r} matches more than one org in kospex: {keys}. "
                         "Use the org key.")

    # Nothing by that owner. Say what it is instead, if it's something else.
    org_part, _, repo_part = owner_l.removesuffix(".git").rpartition("/")
    for s, o, r in repos:
        if (org_part and o.lower() == org_part and r.lower() == repo_part
                and (host is None or s.lower() == host)):
            raise ValueError(f"{raw!r} is a repository, not an org. "
                             f"Use: kgit pull {KospexGit.generate_repo_id(s, o, r)}")
    raise ValueError(f"No org {raw!r} in kospex. If it's on GitHub, add it first "
                     f"with: kgit sync --org {owner or raw}")


def parse_target(target):
    """The owner name from a target, as `(host, owner)`. Raises ValueError.

    Accepts `acme`, `github.com/acme`, `https://github.com/acme` and the org key
    `github.com~acme`. A bare name means github.com. A path with a repository in
    it is refused: that's `kgit sync URL`, the single-repository form.
    """
    raw = (target or "").strip().rstrip("/")
    if not raw:
        raise ValueError("No organisation or user given.")

    host, owner = split_owner(raw)
    # No host given means github.com here; `kgit pull --org` reads it as any host.
    host = (GITHUB_HOST if host is None else host).lower()
    if host != GITHUB_HOST:
        raise ValueError(f"Only GitHub is supported for now, not {host or target!r}.")
    if "/" in owner or "~" in owner:
        raise ValueError(
            f"{target!r} looks like a repository, not an organisation or user. "
            "Use `kgit sync URL` to sync one repository.")
    if not _OWNER_RE.match(owner):
        raise ValueError(f"{owner!r} isn't a valid GitHub organisation or user name.")
    return host, owner


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


# Only the backstop for a reused PID: a lock whose process has died on this host
# is taken over at once, and one whose process is running is never taken over
# for being slow. A full sync of a large org can run for days, so this is set
# well past that rather than at a guess of a run's length.
LOCK_STALE_AFTER_SECONDS = 7 * 24 * 60 * 60


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
