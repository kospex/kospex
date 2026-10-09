"""Extractor for pnpm-lock.yaml lockfiles.

Returns one record per resolved package (the full dependency closure),
shaped to KospexDependencies.get_package_template() so ``krunner osi``
consumes the records unchanged.

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
        # Install state (#227). None means the format could not say -- which
        # for pnpm depends on the lockfile version, not on pnpm. See
        # _install_state_from_packages / _install_state_from_snapshot.
        "is_dev": None,
        "is_optional": None,
        "runs_install_script": None,
        "declared_scope": None,
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



#: ``packages:`` flag → the column it projects onto. The raw key name is kept
#: in ``declared_scope`` because the mapping is lossy: pnpm's requiresBuild is
#: "lifecycle scripts OR a native module that needs building", a strict
#: superset of npm's hasInstallScript, which the shared column name hides.
_PACKAGES_FLAGS = (
    ("dev", "is_dev"),
    ("optional", "is_optional"),
    ("requiresBuild", "runs_install_script"),
)

#: Nothing was said, so claim nothing. Distinct from all-False, which claims
#: the file was read and answered no.
_UNKNOWN = {
    "is_dev": None,
    "is_optional": None,
    "runs_install_script": None,
    "declared_scope": None,
}


def _install_state_from_packages(meta):
    """v5 / v6: all three flags live on the ``packages:`` entry.

    These versions omit a key when it is false, so absence here IS a no --
    the format answers for every entry. That is a v5/v6 property and not a
    pnpm one: see _install_state_from_snapshot for what v9 can say.

    ``declared_scope`` keeps the raw key names, sorted and comma-joined, so
    the projection above stays auditable and ``dev,optional`` remains
    distinguishable from npm's ``devOptional`` -- a third state rather than
    the union of two (#229).
    """
    if not isinstance(meta, dict):
        return dict(_UNKNOWN)

    state, declared = {}, []
    for raw, column in _PACKAGES_FLAGS:
        value = bool(meta.get(raw, False))
        state[column] = value
        if value:
            declared.append(raw)

    state["declared_scope"] = ",".join(sorted(declared)) or None
    return state


def _install_state_from_snapshot(variants):
    """v9: only ``optional`` is recorded, and it is in ``snapshots:``.

    v9 moved the resolved graph out of ``packages:``, which now carries
    resolution / engines / os / cpu / peerDependencies and nothing about
    install state. The 9.0 spec documents no ``dev`` and no
    ``requiresBuild`` anywhere, so both stay None: dev-ness is only
    derivable by walking the graph from each importer's devDependencies,
    which this parser does not do, and pnpm tracks built packages outside
    the lockfile entirely.

    Args:
        variants: the ``optional`` values of every ``snapshots:`` key that
            resolves to this name@version -- peer-dependency suffixes mean
            one package can appear several times. Empty when no snapshot
            mentions it at all.

    A package counts as optional only if EVERY path to it is optional. One
    required path means it installs, and ``is_optional`` says "may not
    install", so ``any()`` would assert something false. On one real v9
    lockfile the two rules differ by a single package of 569 -- the rule is
    chosen for being right, not for the count.
    """
    if not variants:
        return dict(_UNKNOWN)

    optional = all(variants)
    return {
        "is_dev": None,
        "is_optional": optional,
        "runs_install_script": None,
        # Qualified by section: a reader can tell this from a v6 packages:
        # flag of the same name, which came with two companions.
        "declared_scope": "snapshots:optional" if optional else None,
    }


def _snapshot_optionals(doc, splitter):
    """Index ``snapshots:`` by (name, version) → list of ``optional`` values.

    Snapshot keys carry the same ``(peer@x)`` suffixes as ``packages:`` keys
    and are normalised with the same splitter, so several keys can collapse
    onto one package. Matching on the raw key instead missed 5 of 209
    optional packages on one real lockfile, the suffixed ones.
    """
    snapshots = doc.get("snapshots")
    if not isinstance(snapshots, dict):
        return {}

    index = {}
    for key, meta in snapshots.items():
        name, version = splitter(key)
        if not name or not version:
            continue
        value = bool(meta.get("optional")) if isinstance(meta, dict) else False
        index.setdefault((name, version), []).append(value)
    return index


def _collect_direct_dev(doc):
    """Return (direct_names, dev_names) sets.

    Scans every importer's dep blocks (workspace/v9) plus top-level dep
    blocks (non-workspace/v5). Works whether a block maps name→scalar
    (v5) or name→{specifier, version} (v6/v9) — only the keys matter.
    """
    direct, dev = set(), set()

    def _names(block):
        return set(block.keys()) if isinstance(block, dict) else set()

    sources = []
    importers = doc.get("importers")
    if isinstance(importers, dict):
        sources.extend(v for v in importers.values() if isinstance(v, dict))
    sources.append(doc)

    for src in sources:
        direct |= _names(src.get("dependencies"))
        direct |= _names(src.get("optionalDependencies"))
        dev |= _names(src.get("devDependencies"))

    return direct, dev


def extract_pnpm_lock(path):
    """Parse a pnpm-lock.yaml; return get_package_template-shaped
    records for the full resolved package closure.

    Args:
        path: Path to a pnpm-lock.yaml on disk.

    Returns:
        list[dict]: one record per distinct resolved ``name@version``
        (no dedup — diamond deps yield one record per version), with
        ``package_name``, ``package_version``, ``ecosystem="npm"`` and
        ``requirements_type`` ("direct" / "dev" / "resolved"). Empty
        list on missing / invalid / unknown-lockfileVersion files.
    """
    try:
        with open(path) as f:
            doc = yaml.safe_load(f)
    except yaml.YAMLError as e:
        logger.warning("Error parsing pnpm-lock.yaml %s: %s", path, e)
        return []
    except FileNotFoundError:
        logger.warning("File not found: %s", path)
        return []
    except (OSError, UnicodeDecodeError) as e:
        logger.warning("Could not read pnpm-lock.yaml %s: %s", path, e)
        return []

    if not doc or not isinstance(doc, dict):
        return []

    lv = str(doc.get("lockfileVersion", "")).strip()
    # v5.x: "5", "5.0", "5.1", "5.2", "5.3", "5.4" etc.
    # v6.x: "6.0" etc.
    # v9.x: "9.0" etc.
    # Distinguish by major version only — split on "." and check first digit.
    lv_major = lv.split(".")[0]
    if lv_major == "5":
        splitter = _split_v5_key
    elif lv_major in ("6", "9"):
        splitter = _split_at_key
    else:
        logger.warning("Unknown pnpm lockfileVersion %r in %s", lv, path)
        return []

    # Where install state lives is a per-version fact, not a pnpm fact.
    from_snapshots = lv_major == "9"

    packages = doc.get("packages")
    if not isinstance(packages, dict):
        return []

    direct, dev = _collect_direct_dev(doc)
    snapshot_optionals = _snapshot_optionals(doc, splitter) if from_snapshots else {}

    records = []
    for key, meta in packages.items():
        name, version = splitter(key)
        if not name or not version:
            continue
        if name in direct:
            req_type = "direct"
        elif name in dev:
            req_type = "dev"
        else:
            req_type = "resolved"
        rec = _template()
        rec["package_name"] = name
        rec["package_version"] = version
        rec["ecosystem"] = "npm"
        rec["requirements_type"] = req_type
        if from_snapshots:
            rec.update(_install_state_from_snapshot(
                snapshot_optionals.get((name, version), [])))
        else:
            rec.update(_install_state_from_packages(meta))
        records.append(rec)

    return records
