"""Extractor for uv.lock lockfiles.

Returns one record per locked third-party package (the full resolved
closure), shaped to KospexDependencies.get_package_template() so
``krunner osi`` and ``kospex sca`` consume the records unchanged.

A uv.lock is TOML: a ``version`` / ``revision`` header and a
``[[package]]`` array. Each package names its own ``source`` and lists its
edges in three places:

- ``dependencies``                  -- always installed with the package
- ``optional-dependencies.<extra>`` -- installed when the extra is requested
- ``dev-dependencies.<group>``      -- the project's dependency groups

The project itself, and every workspace member, is a ``[[package]]`` too,
with ``source = { editable = ... }`` or ``{ virtual = ... }``. Those are
first-party and produce no record -- reporting them would attribute a
repository's own packages to it as supply chain, the same class of problem
as the pnpm ``.`` importer. They are where the graph walk starts.

``version`` is the format version and the only gate: uv 0.11 refuses
anything but the integer 1 ("unsupported schema version"). ``revision`` is
a compatible bump within it, and uv reads a revision it does not know, so
this parser does too. Measured against uv itself, not inferred.

Integrity hashes (``sdist`` / ``wheels``) are dropped -- SCA does not
consume them. A missing, unreadable or uninterpretable file raises
UnreadableManifest rather than returning [] -- see that class. [] is
reserved for a lockfile that was read and locks nothing but the project.
"""

import tomllib

from kospex.extractors.errors import UnreadableManifest
from kospex_utils import get_kospex_logger

logger = get_kospex_logger("extractors.uvlock")

#: The one format version uv reads. A different one is a different schema.
SUPPORTED_VERSION = 1

#: Revisions seen on the development estate: 1 (x1), 2 (x4), 3 (x19). A
#: higher one is read, as uv reads it, and logged so the drift is visible.
KNOWN_REVISION = 3

#: ``source`` kinds that are the project or a workspace member. Measured: 27
#: editable and 1 virtual entry of 2,434, and these are all 13 of the
#: entries carrying no ``version``.
_FIRST_PARTY_SOURCES = frozenset(("editable", "virtual"))

#: Reached through a package's ``dependencies``: installs unconditionally.
_RUNTIME = None

_OPTIONAL_SECTION = "optional-dependencies"
_DEV_SECTION = "dev-dependencies"


def _template():
    """Local copy of KospexDependencies.get_package_template()'s shape.

    Duplicated (not imported) to keep this extractor pure -- it must not
    depend on the KospexDependencies / DB stack. A contract test
    (test_template_matches_get_package_template) fails if the two drift.

    ``versions_behind`` and ``advisories`` are None, not "": both columns are
    INTEGER, SQLite sorts TEXT above every INTEGER, and '' > 0 is true (#258).
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
        "versions_behind": None,
        "advisories": None,
        "published_at": "",
        # Install state (#227). uv.lock answers dev and optional through the
        # graph; it records nothing about build or install hooks.
        "is_dev": None,
        "is_optional": None,
        "runs_install_script": None,
        "declared_scope": None,
    }


def _is_int(value):
    """A TOML integer. bool is an int subclass, and `true` is not version 1."""
    return isinstance(value, int) and not isinstance(value, bool)


def _check_format(path, doc):
    """Gate on ``version``; accept any well-typed ``revision``.

    Every refusal here is one uv makes too: a missing or non-integer
    ``version``, a version other than 1, and a non-integer ``revision``.
    """
    if "version" not in doc:
        # Includes the 0-byte file, which uv also refuses.
        raise UnreadableManifest(f"{path}: no `version`, so not a uv.lock")
    version = doc["version"]
    if not _is_int(version) or version != SUPPORTED_VERSION:
        raise UnreadableManifest(
            f"{path}: unsupported uv.lock version {version!r} -- refusing to "
            f"report this file as empty, because its packages are unread")

    if "revision" in doc:
        revision = doc["revision"]
        if not _is_int(revision) or revision < 0:
            raise UnreadableManifest(
                f"{path}: `revision` is {revision!r}, not a revision number")
        if revision > KNOWN_REVISION:
            logger.info(
                "%s: uv.lock revision %d is newer than any measured (%d); "
                "read as version %d", path, revision, KNOWN_REVISION, version)


def _edges(path, owner, value, where):
    """Validate one list of dependency edges and return it."""
    if not isinstance(value, list):
        raise UnreadableManifest(
            f"{path}: {owner} `{where}` is {type(value).__name__}, not a list")
    for edge in value:
        if not isinstance(edge, dict) or not isinstance(edge.get("name"), str):
            raise UnreadableManifest(
                f"{path}: {owner} `{where}` holds an edge with no name: {edge!r}")
    return value


def _groups(path, owner, entry, section):
    """Validate an ``optional-dependencies`` / ``dev-dependencies`` table."""
    table = entry.get(section, {})
    if not isinstance(table, dict):
        raise UnreadableManifest(
            f"{path}: {owner} `{section}` is {type(table).__name__}, not a table")
    return {group: _edges(path, owner, edges, f"{section}.{group}")
            for group, edges in table.items()}


def _node(path, entry):
    """Validate one ``[[package]]`` entry into the parts the walk needs."""
    if not isinstance(entry, dict):
        raise UnreadableManifest(
            f"{path}: a `package` entry is {type(entry).__name__}, not a table")
    name = entry.get("name")
    if not isinstance(name, str) or not name:
        raise UnreadableManifest(f"{path}: a `package` entry has no name")
    source = entry.get("source")
    if not isinstance(source, dict) or not source:
        # 2,434 of 2,434 real entries carry one, and it is what decides
        # first-party: guessing would decide it wrongly in one direction.
        raise UnreadableManifest(f"{path}: package {name!r} has no `source`")
    version = entry.get("version")
    if version is not None and not isinstance(version, str):
        raise UnreadableManifest(
            f"{path}: package {name!r} has version {version!r}, not a string")

    return {
        "name": name,
        "version": version,
        "source": source,
        "first_party": bool(_FIRST_PARTY_SOURCES & source.keys()),
        "dependencies": _edges(
            path, name, entry.get("dependencies", []), "dependencies"),
        "optional": _groups(path, name, entry, _OPTIONAL_SECTION),
        "dev": _groups(path, name, entry, _DEV_SECTION),
    }


def _resolver(nodes):
    """Edge → the node indices it names.

    uv forks resolution by marker, so one name can lock at several versions;
    it then writes ``version`` (and ``source``) on the edge to say which.
    Measured: all 5,140 edges on the estate resolve to exactly one entry.
    """
    by_name = {}
    for i, node in enumerate(nodes):
        by_name.setdefault(node["name"], []).append(i)

    def resolve(edge):
        found = by_name.get(edge["name"], [])
        if "version" in edge:
            found = [i for i in found if nodes[i]["version"] == edge["version"]]
        if "source" in edge:
            found = [i for i in found if nodes[i]["source"] == edge["source"]]
        return found

    return resolve


def _walk(path, nodes):
    """Label every node with each way it is reached from the project.

    A label is _RUNTIME, or (section, group) for the extra or dependency
    group a path starts from -- the group name is kept because ``[postgres]``
    and ``[dev]`` mean very different things. Every first-party node is a
    root, so a workspace member's runtime dependencies are the workspace's.

    An edge requesting extras (``{ name = "x", extra = ["socks"] }``) also
    follows x's ``optional-dependencies.socks``, under the same label: an
    extra a runtime dependency asks for installs at runtime.

    Returns (labels, direct): per node index, every label reaching it, and
    the labels of the edges from first-party nodes that name it directly.
    """
    resolve = _resolver(nodes)
    labels = [set() for _ in nodes]
    direct = [set() for _ in nodes]
    seen = set()
    queue = []

    def follow(edge, label, from_first_party):
        targets = resolve(edge)
        if not targets:
            logger.warning(
                "%s: dependency %r names no locked package", path, edge["name"])
        for t in targets:
            if from_first_party:
                direct[t].add(label)
            if (t, label) not in seen:
                seen.add((t, label))
                labels[t].add(label)
                queue.append((t, label))
            for extra in edge.get("extra", []):
                if (t, extra, label) not in seen:
                    seen.add((t, extra, label))
                    for e in nodes[t]["optional"].get(extra, []):
                        follow(e, label, nodes[t]["first_party"])

    for node in nodes:
        if not node["first_party"]:
            continue
        for edge in node["dependencies"]:
            follow(edge, _RUNTIME, True)
        for section, key in ((_OPTIONAL_SECTION, "optional"), (_DEV_SECTION, "dev")):
            for group, edges in node[key].items():
                for edge in edges:
                    follow(edge, (section, group), True)

    while queue:
        i, label = queue.pop()
        for edge in nodes[i]["dependencies"]:
            follow(edge, label, nodes[i]["first_party"])

    return labels, direct


def _install_state(labels):
    """Flags from every path reaching a package.

    One runtime path means it installs, whatever else reaches it. Reached
    only through dev groups AND extras is npm's ``devOptional`` state:
    certainly not production, so is_dev; optionality unsaid, so NULL. No
    path at all means the file does not say, so NULL throughout.
    """
    if not labels:
        return {"is_dev": None, "is_optional": None, "declared_scope": None}
    if _RUNTIME in labels:
        return {"is_dev": False, "is_optional": False, "declared_scope": None}

    sections = {section for section, _group in labels}
    dev = _DEV_SECTION in sections
    optional = _OPTIONAL_SECTION in sections
    return {
        "is_dev": dev,
        "is_optional": None if dev and optional else optional,
        "declared_scope": ",".join(sorted(f"{s}:{g}" for s, g in labels)),
    }


def _requirements_type(direct):
    """direct > optional > dev > resolved, by the edges naming it directly.

    An extra outranks a dev group because it is published metadata that
    reaches the project's own users; a dependency group does not.
    """
    if _RUNTIME in direct:
        return "direct"
    sections = {label[0] for label in direct}
    if _OPTIONAL_SECTION in sections:
        return "optional"
    if _DEV_SECTION in sections:
        return "dev"
    return "resolved"


def extract_uv_lock(path):
    """Parse a uv.lock; return get_package_template-shaped records.

    Args:
        path: Path to a uv.lock on disk.

    Returns:
        list[dict]: one record per distinct third-party ``name@version``,
        with ``ecosystem="pypi"``, ``requirements_type`` ("direct" /
        "optional" / "dev" / "resolved") and the install-state columns.
        [] for a lockfile that locks nothing but the project itself.

    Raises:
        UnreadableManifest: the file is missing, unreadable, not UTF-8, not
            TOML, declares a ``version`` other than 1, or holds a ``package``
            entry or dependency edge this parser cannot interpret.
    """
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except FileNotFoundError as e:
        raise UnreadableManifest(f"{path}: file not found") from e
    except OSError as e:
        raise UnreadableManifest(f"{path}: could not read: {e}") from e

    try:
        # utf-8-sig, because uv reads a BOM'd uv.lock: refusing one would
        # report a lockfile uv installs from as broken.
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise UnreadableManifest(f"{path}: not UTF-8: {e}") from e

    try:
        doc = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise UnreadableManifest(f"{path}: invalid TOML: {e}") from e

    _check_format(path, doc)

    entries = doc.get("package", [])
    if not isinstance(entries, list):
        raise UnreadableManifest(
            f"{path}: `package` is {type(entries).__name__}, not an array of tables")
    nodes = [_node(path, entry) for entry in entries]

    labels, direct = _walk(path, nodes)

    # Grouped by name@version, the dependency_data primary key, so two
    # entries asserting the same identity (say from two sources) merge
    # rather than one row silently replacing the other.
    grouped = {}
    for i, node in enumerate(nodes):
        if node["first_party"]:
            continue
        if not node["version"]:
            logger.warning(
                "%s: package %r has no version and cannot be recorded",
                path, node["name"])
            continue
        key = (node["name"], node["version"])
        found = grouped.setdefault(key, (set(), set()))
        found[0].update(labels[i])
        found[1].update(direct[i])

    records = []
    for (name, version), (reached, named) in grouped.items():
        record = _template()
        record["package_name"] = name
        record["package_version"] = version
        record["ecosystem"] = "pypi"
        record["requirements_type"] = _requirements_type(named)
        record.update(_install_state(reached))
        records.append(record)

    return records
