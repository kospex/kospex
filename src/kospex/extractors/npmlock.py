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
them.

A missing / invalid / unknown-lockfileVersion file raises UnreadableLockfile
rather than returning []. See that class: an empty list is reported as
`empty`, which asserts the file was read and declared nothing, and that
difference is exploitable. This module was the first to do it; every extractor
now does, through the shared `UnreadableManifest` base.
"""

import json
import re
from urllib.parse import unquote, urlparse

from kospex.extractors.errors import UnreadableManifest
from kospex_utils import get_kospex_logger

logger = get_kospex_logger("extractors.npmlock")

_NODE_MODULES = "node_modules/"


class UnreadableLockfile(UnreadableManifest):
    """This file could not be read, so nothing is known about its contents.

    A package-lock.json-specific name for the shared contract in
    `kospex.extractors.errors`; kept because it is the name this module
    already raises and that its tests import. Catch `UnreadableManifest` to
    cover every extractor at once.

    The distinction from returning [] is not stylistic: `osi_extract` turns an
    exception into a `parse_error` outcome, while an empty list becomes
    `empty` -- whose documented meaning is "examined, nothing to find", and
    which is the result that stops a repository being re-queued.

    Reporting an unreadable lockfile as empty is an evasion. Changing
    `lockfileVersion: 3` to `"3"` in a real lockfile read zero of its packages
    and reported the repository as cleanly empty; so did bumping it to 4,
    deleting the field, or truncating the JSON. One edit, every dependency
    hidden, and a benign-looking scan result.
    """


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


#: How npm encodes an alias in a v1 version string: `npm:<name>@<version>`.
_NPM_ALIAS = "npm:"


#: An npm registry tarball path: ``/<name>/-/<file>-<version>.tgz``, where the
#: name may be scoped. 89.5% of installed entries in the reference estate carry
#: one.
_TARBALL_PATH = re.compile(r"^/(?P<name>(?:@[^/]+/)?[^/]+)/-/")

#: A conservative npm package name. The length cap and the leading dot /
#: underscore rules are npm's own. The character class allows every real name,
#: including the historic uppercase ones npm no longer issues, while excluding
#: whitespace, control characters and the punctuation that makes a value
#: dangerous once it leaves here -- this string becomes part of a primary key
#: and is written into CSV exports.
_PACKAGE_NAME = re.compile(r"^(?:@[A-Za-z0-9._~-]+/)?[A-Za-z0-9._~-]+$")


def _is_package_name(value):
    """Could `value` be an npm package name at all?"""
    return (
        isinstance(value, str)
        and 0 < len(value) <= 214
        and not value.startswith((".", "_"))
        and bool(_PACKAGE_NAME.match(value))
    )


def _name_from_resolved(url):
    """The package name encoded in a registry tarball URL, or None.

    Only a registry tarball is read. A git, file or workspace URL names no
    package, and neither does a URL whose path does not look like one.
    """
    if not isinstance(url, str) or not url:
        return None
    try:
        path = unquote(urlparse(url).path or "")
    except ValueError:
        return None

    match = _TARBALL_PATH.match(path)
    if not match:
        return None
    name = match.group("name")
    return name if _is_package_name(name) else None


#: The version in a registry tarball filename: ``…/-/<base>-<version>.tgz``.
#: Anchored on a digit so a hyphenated package name is not mistaken for one,
#: and greedy to the extension so a prerelease (``1.0.0-beta.1``) survives.
_TARBALL_VERSION = re.compile(r"-(?P<version>\d+\.\d+\.\d+[^/]*)\.tgz$")


def _identities(path_name, meta):
    """Every package name this entry asserts, install path first.

    A lockfile gives up to three: the install path, an explicit ``name`` (how
    npm records ``npm i foo@npm:bar``), and the package named in the
    ``resolved`` tarball URL. They usually agree -- 12,687 of the 12,689 entries
    with a tarball URL in the reference estate -- and when they do this returns
    one name and nothing changes.

    When they disagree, ALL of them are recorded, and no field is treated as
    authoritative over another.

    That is deliberate, and it replaces an earlier attempt to corroborate
    ``name`` against ``resolved``. Corroboration was a conceptual error: both
    fields come from the same untrusted document, so their agreement is not
    evidence of anything, and the URL's host was never checked either. More
    fundamentally, any rule that elects one field is spoofable in whichever
    direction it elects -- which is how each previous version of this function
    was defeated.

    Recording every identity is not spoofable in the direction that matters. A
    package cannot be hidden, because the install path is always recorded; and a
    genuine alias target is always recorded, so deps.dev is asked about the
    package that is really installed. The cost is a row for a name the registry
    may not know: a visible, dismissible false positive in place of an invisible
    false negative.

    Measured on the reference estate: 32 entries assert more than one identity,
    which after de-duplication within a repository is 10 extra rows out of
    13,739. All ten are the real aliased packages -- the alias TARGET is what the
    second identity names. The install-path names a registry would not recognise
    (`string-width-cjs` and friends) are written either way, because the install
    path is always the first identity.

    It also needs no registry allowlist, which matters because private
    registries (Artifactory, GitHub Packages) are normal in the estates kospex
    scans and an allowlist would quietly degrade them.
    """
    # The install path is checked like any other candidate. A key such as a
    # bare `node_modules/` yields an empty segment, and an empty package_name
    # is an unusable primary key -- the entry's other identities still record
    # it, so refusing the blank name does not hide anything.
    names = [path_name] if _is_package_name(path_name) else []

    for candidate in (meta.get("name"), _name_from_resolved(meta.get("resolved"))):
        # Still not a free-for-all: this string becomes part of a primary key
        # and is written into exports, so a value that cannot be an npm package
        # name is not recorded as one.
        if _is_package_name(candidate) and candidate not in names:
            names.append(candidate)

    if len(names) > 1:
        logger.warning(
            "entry %r asserts more than one package identity %r; recording each "
            "-- no field of a lockfile corroborates another", path_name, names)

    return names


def _entry_versions(meta):
    """Every version this entry claims, the declared one first.

    The same rule as _identities, in the other primary-key column, and for the
    same reason: ``version`` and the version inside the ``resolved`` tarball URL
    are both attacker-supplied, so neither vouches for the other. Trusting
    ``version`` alone let a lockfile claim a version that does not exist --
    deps.dev then has nothing to answer with, so a genuinely vulnerable package
    reported no advisories. That is identity spoofing in the version field.

    Measured: the two agree on all 12,719 entries where both are present, so a
    disagreement never occurs in an honest file and recording both costs
    nothing. 1,490 entries have no parseable tarball URL and are unaffected.

    This also covers an absent ``version``: deleting that one key used to remove
    the entry from the scan entirely, while ``resolved`` and ``integrity`` still
    told npm exactly what to fetch.
    """
    versions = []

    declared = meta.get("version")
    if isinstance(declared, str) and declared:
        versions.append(declared)

    url = meta.get("resolved")
    if isinstance(url, str) and url:
        match = _TARBALL_VERSION.search(url)
        if match and match.group("version") not in versions:
            versions.append(match.group("version"))

    if len(versions) > 1:
        logger.warning(
            "entry claims more than one version %r; recording each -- the "
            "declared version and the tarball URL disagree", versions)

    return versions


def _is_installed_entry(key, meta):
    """Does this ``packages:`` entry describe a fetched package?

    The root ``""`` entry is the project. A key with no ``node_modules/``
    segment is normally a workspace importer: a local directory that is built,
    not downloaded.

    But testing the key alone let a dependency parked at ``vendor/evil-pkg``
    disappear, because nothing about such a key is checked against its content.
    A real importer is never fetched -- measured, none of the 13 in the
    reference estate carries both a version and a registry tarball URL -- so an
    entry that carries both is a dependency at an unusual path, and skipping it
    would hide it.
    """
    if key == "":
        return False
    if _NODE_MODULES in key:
        return True
    return bool(_entry_versions(meta)) and _looks_fetched(meta)


def _looks_fetched(meta):
    """Was this entry downloaded from somewhere, rather than built in place?

    The question is deliberately "is there a URL", not "is it a recognisable
    npm tarball". Requiring the latter asked the wrong thing: Artifactory and
    Nexus serve packages under a repository prefix
    (``/api/npm/npm-remote/lodash/-/lodash-4.17.21.tgz``), so their URLs do not
    parse as a public registry path -- and those are exactly the registries the
    estates kospex scans use, which would have left the unusual-path hole open
    for them while closing it for everyone else.

    A workspace link's ``resolved`` is a relative directory
    (``apps/shared``) with no scheme, and measured, none of the 13 importer
    entries in the reference estate has one with a scheme. So a scheme is the
    distinction, and it covers https, http, git+ssh and file alike.
    """
    url = meta.get("resolved")
    if not isinstance(url, str) or not url:
        return False
    try:
        return bool(urlparse(url).scheme)
    except ValueError:
        # A malformed URL is still an assertion that something was fetched.
        return True


def _lockfile_version(raw):
    """``lockfileVersion`` as an int, or None if it is not unambiguously one.

    npm writes an integer, but a parser that insists on the int *type* fails
    open: ``lockfileVersion: "3"`` read nothing and reported the file as
    `empty`, which means "read, and genuinely declared nothing" -- so a
    one-character edit hid every dependency behind a clean-looking result.
    Accept any value that unambiguously IS an integer.

    `bool` is checked first because it is an int subclass in Python, and
    ``lockfileVersion: true`` is not version 1.
    """
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        return int(raw) if raw == int(raw) else None
    if isinstance(raw, str):
        try:
            return int(raw.strip())
        except ValueError:
            return None
    return None


def _v1_identity(name, version):
    """v1 has no ``name`` field, so it encodes an alias in the version.

    ``{"foo": {"version": "npm:left-pad@1.2.3"}}`` means left-pad@1.2.3 is
    installed as ``foo``. Stored verbatim that is a package called ``foo`` at
    version ``npm:left-pad@1.2.3`` -- neither of which is true, and the version
    is not a version.

    Returns (name, version) unchanged for an ordinary entry. An ``npm:`` spec
    that names nothing parseable is ALSO returned unchanged rather than
    dropped: dropping let ``version: "npm:anything"`` remove a package from the
    scan entirely, and a row kospex cannot look up is still a row someone can
    see.
    """
    if not isinstance(version, str) or not version.startswith(_NPM_ALIAS):
        return name, version

    real_name, sep, real_version = version[len(_NPM_ALIAS):].rpartition("@")
    if not sep or not _is_package_name(real_name) or not real_version:
        logger.warning(
            "could not read the alias %r for %r; recording it as declared",
            version, name)
        return name, version
    return real_name, real_version


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


def _group_by_package(entries):
    """[(name, version, meta, alias)] → {(name, version): (metas, names)}.

    Insertion-ordered so the records a parse returns follow the file, which
    keeps diffs between two runs readable.

    ``names`` collects every name the package was reached under -- its own and
    any alias it was installed as. A project declares an alias by the alias, so
    direct/dev lookups have to match on either.
    """
    groups = {}
    for name, version, meta, alias in entries:
        metas, names = groups.setdefault((name, version), ([], set()))
        metas.append(meta)
        names.add(name)
        if alias:
            names.add(alias)
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
            # v1 encodes an alias in the version string, so the identity has to
            # be resolved before the entry is recorded at all.
            alias_name, alias_version = _v1_identity(name, meta.get("version"))

            # Same rules as v2/v3: every identity and every version the entry
            # asserts, with the declared ones first. See _identities and
            # _entry_versions.
            claims = {"name": alias_name if alias_name != name else None,
                      "version": alias_version,
                      "resolved": meta.get("resolved")}
            identities = _identities(name, claims)
            versions = _entry_versions(claims)

            # Both package_name and package_version are part of the
            # dependency_data primary key, so an entry missing either cannot be
            # written. Say so: the v2/v3 path reports this, and dropping the
            # same malformed entry silently here made it visible in one npm
            # format and invisible in the other.
            if not identities:
                logger.warning(
                    "v1 entry %r asserts no usable package name; it cannot be "
                    "recorded", name)
            elif not versions:
                logger.warning(
                    "v1 entry %r has no version and none could be recovered "
                    "from its resolved URL; it cannot be recorded", name)

            for identity in identities:
                for version in versions:
                    found.append((identity, version, meta, name))
            # Nested transitives live inside their parent's own dependencies
            # map. Walked even when the parent was skipped.
            walk(meta.get("dependencies"))

    walk(dependencies)

    records = []
    for (name, version), (metas, _names) in _group_by_package(found).items():
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
        if not isinstance(meta, dict):
            continue
        if _is_installed_entry(key, meta):
            continue  # a fetched package, not an importer
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
        if not isinstance(meta, dict):
            continue
        if not _is_installed_entry(key, meta):
            continue
        # The install path's own name: the last node_modules/ segment, or the
        # last path segment for a dependency parked somewhere unusual.
        path_name = _package_name_from_key(key) or key.rsplit("/", 1)[-1]

        # No explicit `link` check. A real link: entry is a symlink to a
        # workspace directory and carries only {link, resolved} -- all 13 in
        # the reference estate have no `version`, so the check below already
        # excludes them. Skipping on `link` as well would mean one extra key
        # could remove any dependency from the scan.
        #
        # A version is required because package_version is part of the
        # dependency_data primary key: a version-less row could never update,
        # only duplicate on each re-sync.
        versions = _entry_versions(meta)
        if not versions:
            if not meta.get("link"):
                logger.warning(
                    "%s has no version and none could be recovered from its "
                    "resolved URL; it cannot be recorded", path_name)
            continue

        # One row per identity AND per version the entry asserts, because no
        # field of a lockfile corroborates another. See _identities.
        identities = _identities(path_name, meta)
        if not identities:
            logger.warning(
                "entry %r asserts no usable package name; it cannot be "
                "recorded", key)
            continue
        for name in identities:
            for version in versions:
                found.append((name, version, meta, path_name))

    records = []
    for (name, version), (metas, names) in _group_by_package(found).items():
        record = _template()
        record["package_name"] = name
        record["package_version"] = version
        record["ecosystem"] = "npm"
        # Matched on every name it was reached under, because a project
        # declares an aliased dependency by its alias, not by its real name.
        if names & direct:
            record["requirements_type"] = "direct"
        elif names & dev:
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
        raise UnreadableLockfile(f"{path}: invalid JSON: {e}") from e
    except FileNotFoundError as e:
        raise UnreadableLockfile(f"{path}: file not found") from e
    except (OSError, UnicodeDecodeError) as e:
        raise UnreadableLockfile(f"{path}: could not read: {e}") from e

    if not doc or not isinstance(doc, dict):
        raise UnreadableLockfile(
            f"{path}: not a JSON object, so not a lockfile")

    raw_lv = doc.get("lockfileVersion")
    lv = _lockfile_version(raw_lv)
    if lv not in (1, 2, 3):
        raise UnreadableLockfile(
            f"{path}: unsupported lockfileVersion {raw_lv!r} -- refusing to "
            f"report this file as empty, because its dependencies are unread")

    if lv == 1:
        # v1 omits `dependencies` for a project that has none, so an absent
        # tree is genuinely empty rather than malformed.
        return _extract_from_dependencies_tree(doc.get("dependencies"))

    packages = doc.get("packages")
    if not isinstance(packages, dict):
        # npm always writes `packages:` for v2/v3, even when the only entry is
        # the root, so its absence means the file is malformed -- not that the
        # project declares nothing.
        raise UnreadableLockfile(
            f"lockfileVersion {lv} file has no packages section: {path}")

    records = _extract_from_packages(packages)

    # v2 carries the v1-style `dependencies:` tree as well, describing the same
    # closure. `packages:` OWNS the flags for every package it lists -- the
    # legacy tree cannot express hasInstallScript, so merging its entries would
    # flip an aggregate computed with all(). But a package present only in the
    # tree is one an npm 6 client installs and kospex would never have seen, so
    # it is added rather than ignored.
    if lv == 2:
        known = {(r["package_name"], r["package_version"]) for r in records}
        for extra in _extract_from_dependencies_tree(doc.get("dependencies")):
            if (extra["package_name"], extra["package_version"]) not in known:
                logger.warning(
                    "%s: %s@%s is in the legacy dependencies tree but not in "
                    "packages:", path, extra["package_name"],
                    extra["package_version"])
                records.append(extra)

    return records
