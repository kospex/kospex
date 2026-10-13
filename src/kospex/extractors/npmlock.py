"""Extractor for npm package-lock.json lockfiles.

Returns one record per distinct installed ``name@version`` (the full
dependency closure), shaped to KospexDependencies.get_package_template()
so ``krunner osi`` consumes the records unchanged.

npm's ``lockfileVersion`` marks three structurally distinct formats:

- v1 — no ``packages:`` section; a nested ``dependencies`` tree where each
       entry's own transitives are the ``dependencies`` map inside it.
- v2 — carries BOTH ``packages:`` and the v1-style ``dependencies:`` tree,
       for backwards compatibility. The two describe the same closure, so
       only ``packages:`` is read and the duplicate is ignored.
- v3 — ``packages:`` only.

``packages:`` keys are install *paths*, not names
(``node_modules/findup-sync/node_modules/glob``), which is what makes
nesting visible without walking a graph. One package can therefore appear
at several depths with DIFFERENT flags, while dependency_data's primary key
has no install-path column -- so those entries are reconciled into one
record rather than overwriting each other. See _aggregate_flags. The paths
themselves are then discarded; storing the parent is #227.

Integrity/checksum hashes are intentionally dropped -- SCA does not consume
them. On missing / invalid / unknown-lockfileVersion files we log a warning
and return [] (extractors package convention).
"""

import json

from kospex_utils import get_kospex_logger

logger = get_kospex_logger("extractors.npmlock")

_NODE_MODULES = "node_modules/"


def _template():
    """Local copy of KospexDependencies.get_package_template()'s shape.

    Duplicated (not imported) to keep this extractor pure -- it must not
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
        # Install state (#227). None means the format could not say, which for
        # npm depends on the lockfileVersion -- see _install_state_*.
        "is_dev": None,
        "is_optional": None,
        "runs_install_script": None,
        "declared_scope": None,
    }


def _package_name_from_key(key):
    """``packages:`` key (an install path) → package name, or None.

    Returns None for the two key shapes that are not installed packages: the
    root entry ``""`` (the project itself) and a workspace importer such as
    ``apps/desktop`` (a local package, not something fetched). Both are
    identified by having no ``node_modules/`` segment at all.

    The name is what follows the LAST ``node_modules/``, so a nested
    transitive is named for itself and keeps a scope prefix intact.
    """
    if not key or _NODE_MODULES not in key:
        return None
    name = key.rsplit(_NODE_MODULES, 1)[1]
    return name or None


#: Flags describing how a package was REACHED. One entry per install path, so a
#: package is only excluded when every path agrees it is -- aggregated with
#: all(). The rule settled for pnpm v9 snapshots, for the same reason: one
#: required path means the package installs, and claiming otherwise is a false
#: negative in exactly the layer nobody inspects.
_PATH_FLAGS = ("dev", "optional", "devOptional")

#: Flags describing the package ITSELF, which every path should agree on, so
#: any() is the safe aggregate. Measured: zero duplicate groups across the
#: reference estate disagree on hasInstallScript, consistent with it being a
#: property of the tarball rather than of the path.
_PACKAGE_FLAGS = ("hasInstallScript", "peer", "inBundle")

#: v1 states only these two. There is no hasInstallScript, devOptional, peer or
#: inBundle anywhere in the format -- re-measured across both v1 files on disk
#: after docs/dependency-flags.md wrongly claimed v1 records nothing at all.
_V1_PATH_FLAGS = ("dev", "optional")


def _aggregate_flags(metas, path_flags, package_flags):
    """Reduce one package's install-path entries to a single flag per axis.

    ``metas`` is every ``packages:`` entry (or v1 tree node) that resolves to
    the same name@version. There is usually one, but dependency_data's primary
    key has no install-path column, so several must collapse to one row and the
    flags have to be reconciled rather than overwritten.
    """
    metas = [m for m in metas if isinstance(m, dict)]
    if not metas:
        return {}

    raw = {}
    for flag in path_flags:
        raw[flag] = all(bool(m.get(flag)) for m in metas)
    for flag in package_flags:
        raw[flag] = any(bool(m.get(flag)) for m in metas)
    return raw


def _scope(raw):
    """``declared_scope`` from the AGGREGATED flags, sorted for stability.

    Built from the aggregate rather than from the raw keys seen on any path, so
    the value cannot contradict the booleans in its own row: a package with one
    dev path and one production path is not "dev".
    """
    return ",".join(sorted(f for f, on in raw.items() if on)) or None


def _install_state_from_packages(metas):
    """v2 / v3: a ``packages:`` entry answers every axis.

    npm omits each of these keys when false, so absence here IS a no -- the
    format answers for every entry. That is a v2/v3 property, not an npm one:
    v1 cannot speak to install scripts at all.

    ``devOptional`` is npm's third state and is NOT the union of the other
    two. Measured across the reference estate it appears on 71 entries and
    never alongside ``dev`` or ``optional``, while those two co-occur 555
    times -- so npm sets exactly one of the three, and ``devOptional`` means
    "reachable only via a dev path OR an optional path" without saying which.
    It therefore sets is_dev (it certainly does not reach production) and
    leaves is_optional NULL, because False would claim npm had confirmed the
    package always installs. The raw key in ``declared_scope`` is what keeps
    that distinguishable from a plain ``dev`` entry.
    """
    raw = _aggregate_flags(metas, _PATH_FLAGS, _PACKAGE_FLAGS)
    if not raw:
        return {}

    return {
        "is_dev": raw["dev"] or raw["devOptional"],
        # NULL only when every path is devOptional. A package reached both as
        # devOptional and as a plain production dependency does install.
        "is_optional": None if raw["devOptional"] else raw["optional"],
        "runs_install_script": raw["hasInstallScript"],
        "declared_scope": _scope(raw),
    }


def _install_state_from_v1(metas):
    """v1: ``dev`` and ``optional`` are stated; install scripts are not.

    Same omitted-means-false rule as v2/v3 for the two keys the format has, so
    those are False rather than NULL. ``runs_install_script`` stays NULL
    because v1 has no key for it -- the format cannot answer, which is a
    different fact from answering no.
    """
    raw = _aggregate_flags(metas, _V1_PATH_FLAGS, ())
    if not raw:
        return {}

    return {
        "is_dev": raw["dev"],
        "is_optional": raw["optional"],
        "runs_install_script": None,
        "declared_scope": _scope(raw),
    }


def _group_by_package(pairs):
    """[(name, version, meta), ...] → {(name, version): [meta, ...]}, in order.

    Insertion-ordered so the records a parse returns follow the file, which
    keeps diffs between two runs readable.
    """
    groups = {}
    for name, version, meta in pairs:
        groups.setdefault((name, version), []).append(meta)
    return groups


def _extract_from_dependencies_tree(dependencies):
    """v1: walk the nested ``dependencies`` tree, one record per package.

    Every entry is reported as ``resolved``. v1's top level is the *hoisted*
    install set rather than the declared set, so a transitive lifted to the
    root cannot be told apart from a direct dependency, and claiming "direct"
    for the top level would be a guess. ``is_dev`` still carries what npm did
    state, at every depth.

    v1 nests by default, so one package commonly appears at several depths --
    see _aggregate_flags for how their flags are reconciled.
    """
    found = []

    def walk(block):
        if not isinstance(block, dict):
            return
        for name, meta in block.items():
            if not isinstance(meta, dict):
                continue
            version = meta.get("version")
            if name and version:
                found.append((name, version, meta))
            # Nested transitives live inside their parent's own dependencies
            # map. Walked even when the parent was skipped.
            walk(meta.get("dependencies"))

    walk(dependencies)

    records = []
    for (name, version), metas in _group_by_package(found).items():
        record = _template()
        record["package_name"] = name
        record["package_version"] = version
        record["ecosystem"] = "npm"
        record["requirements_type"] = "resolved"
        record.update(_install_state_from_v1(metas))
        records.append(record)

    return records


def _collect_direct_dev(packages):
    """Return (direct_names, dev_names) from the root entry and importers.

    The root ``""`` entry holds the project's own dependency blocks, and each
    workspace importer holds its own. A dependency declared by any of them is
    direct to the repository; everything else in the closure is transitive.
    Mirrors pnpm's _collect_direct_dev, which reads importers the same way.
    """
    direct, dev = set(), set()

    for key, meta in packages.items():
        if _package_name_from_key(key) is not None:
            continue  # an installed package, not an importer
        if not isinstance(meta, dict):
            continue
        for block in ("dependencies", "optionalDependencies"):
            value = meta.get(block)
            if isinstance(value, dict):
                direct |= set(value.keys())
        value = meta.get("devDependencies")
        if isinstance(value, dict):
            dev |= set(value.keys())

    return direct, dev


def _extract_from_packages(packages):
    """v2 / v3: one record per distinct name@version in ``packages:``.

    Keys are install paths, so the same package can appear several times at
    different depths. Those collapse to one record -- see _aggregate_flags.
    """
    direct, dev = _collect_direct_dev(packages)
    found = []

    for key, meta in packages.items():
        name = _package_name_from_key(key)
        if name is None:
            continue
        if not isinstance(meta, dict):
            continue
        # A link: entry is a symlink to a workspace dir, and carries no
        # version. package_version is part of the dependency_data primary key,
        # so a version-less row could never update -- only duplicate.
        if meta.get("link"):
            continue
        version = meta.get("version")
        if not version:
            continue
        found.append((name, version, meta))

    records = []
    for (name, version), metas in _group_by_package(found).items():
        record = _template()
        record["package_name"] = name
        record["package_version"] = version
        record["ecosystem"] = "npm"
        if name in direct:
            record["requirements_type"] = "direct"
        elif name in dev:
            record["requirements_type"] = "dev"
        else:
            # Not "peer" or "optional" even when npm flags them. Those
            # PACKAGE_USE values exist but neither scanner's _REQ_TO_USE maps
            # them, and the two disagree on the fallback (direct vs ""), so
            # emitting them would write silently wrong package_use values. The
            # flags are preserved in declared_scope instead.
            record["requirements_type"] = "resolved"
        record.update(_install_state_from_packages(metas))
        records.append(record)

    return records


def extract_package_lock(path):
    """Parse a package-lock.json; return get_package_template-shaped records.

    Args:
        path: Path to a package-lock.json on disk.

    Returns:
        list[dict]: one record per installed package, with ``package_name``,
        ``package_version``, ``ecosystem="npm"`` and ``requirements_type``
        ("direct" / "dev" / "resolved"). Empty list on missing / invalid /
        unknown-lockfileVersion files.
    """
    try:
        with open(path) as f:
            doc = json.load(f)
    except json.JSONDecodeError as e:
        logger.warning("Error parsing package-lock.json %s: %s", path, e)
        return []
    except FileNotFoundError:
        logger.warning("File not found: %s", path)
        return []
    except (OSError, UnicodeDecodeError) as e:
        logger.warning("Could not read package-lock.json %s: %s", path, e)
        return []

    if not doc or not isinstance(doc, dict):
        return []

    lv = doc.get("lockfileVersion")
    if lv not in (1, 2, 3):
        logger.warning("Unknown npm lockfileVersion %r in %s", lv, path)
        return []

    if lv == 1:
        return _extract_from_dependencies_tree(doc.get("dependencies"))

    # v2 carries the v1-style `dependencies:` tree as well, describing the same
    # closure. Only `packages:` is read: the two can disagree on versions, and
    # reading both double-counts.
    packages = doc.get("packages")
    if not isinstance(packages, dict):
        logger.warning(
            "lockfileVersion %r has no packages section in %s", lv, path)
        return []

    return _extract_from_packages(packages)
