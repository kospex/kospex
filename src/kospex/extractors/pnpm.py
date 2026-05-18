"""Extractor for pnpm-lock.yaml lockfiles.

Returns one record per resolved package (the full dependency closure),
shaped to KospexDependencies.get_package_template() so ``krunner osi``
consumes the records unchanged. Design:
deploy-kospex/planning/pnpm-extractor-osi-plan.md.

pnpm publishes a per-lockfileVersion spec at github.com/pnpm/spec
(lockfile/{5,5.2,6.0,9.0}.md). Three structurally distinct families:

- v5.x  — ``packages:`` keyed ``/name/version`` (path style; scoped
          ``/@scope/name/version``); peer suffix ``_peer@x``.
- v6.0  — ``packages:`` keyed ``/name@version`` (scoped
          ``/@scope/name@version``); peer suffix ``(peer@x)``.
- v9.0  — ``packages:`` keyed ``name@version`` (no leading ``/``;
          YAML-quoted when scoped — yaml.safe_load returns it
          unquoted); resolved graph split into a separate
          ``snapshots:`` section we ignore; peer suffix ``(peer@x)``.

Integrity/checksum hashes are intentionally dropped — SCA does not
consume them (producing checksum-bearing SBOMs is the out-of-scope
"generator" category). On missing / invalid / unknown-lockfileVersion
files we log a warning and return [] (extractors package convention).
"""

import yaml

from kospex_utils import get_kospex_logger

logger = get_kospex_logger("extractors.pnpm")


def _template():
    """Local copy of KospexDependencies.get_package_template()'s shape.

    Duplicated (not imported) to keep this extractor pure — it must not
    depend on the KospexDependencies / DB stack. A contract test
    (test_template_matches_get_package_template) fails if the two drift.
    """
    return {
        "package_name": "",
        "package_version": "",
        "version_type": "",
        "_repo_id": "",
        "file_path": "",
        "requirements_type": "",
        "extras": "",
        "ecosystem": "",
        "versions_behind": "",
        "advisories": "",
        "published_at": "",
    }


def _split_at_key(key):
    """v6/v9 ``packages:`` key → (name, version).

    Handles the v6 leading ``/``, the v9 no-slash form, scoped names
    (``@scope/name`` — the ``@`` that is *not* the version separator),
    and the ``(peer@x)`` peer-dependency suffix.
    """
    key = key.lstrip("/")
    key = key.split("(", 1)[0]
    name, sep, version = key.rpartition("@")
    if not sep or not name:
        return None, None
    return name, version


def _split_v5_key(key):
    """v5.x ``packages:`` key → (name, version).

    v5 separates name and version with ``/`` (not ``@``); scoped names
    keep their ``@scope/`` prefix. Peer suffix is ``_peer@x`` on the
    version segment.
    """
    key = key.lstrip("/")
    name, sep, version = key.rpartition("/")
    if not sep or not name:
        return None, None
    version = version.split("_", 1)[0]
    return name, version


def _collect_direct_dev(doc):
    """Stub — implemented in 3.3."""
    raise NotImplementedError


def extract_pnpm_lock(path):
    """Stub — implemented in 3.5."""
    raise NotImplementedError
