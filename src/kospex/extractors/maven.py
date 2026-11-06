"""Extractor for Maven pom.xml files.

Returns one record per ``<dependency>`` in the project's own
``<dependencies>``, plus one per BOM imported in its
``<dependencyManagement>``, shaped to
KospexDependencies.get_package_template() so ``krunner osi`` consumes the
records unchanged. Package names are ``groupId:artifactId``, which is how
deps.dev names a Maven package.

Pure: no DB, no CLI, no network, no deps.dev enrichment.

A dependency version is resolved in two tiers, and only these two:

1. From the file itself: ``<properties>`` and the built-in ``${project.*}``
   and ``${project.parent.*}`` values, which ``<parent>`` answers without the
   parent being read.
2. From parent poms inside the same repository, followed through
   ``<relativePath>`` (default ``../pom.xml``) exactly as Maven would: their
   properties are inherited and their ``<dependencyManagement>`` supplies
   versions the child omits.

A parent outside the repository, or one only in a Maven repository, and every
imported BOM, are not read. A version they would have supplied is recorded as
"" -- the dependency is still declared, since Maven refuses to build a pom
whose unversioned dependency nothing manages -- and enrichment reports it as
``no_version``. A ``${...}`` that cannot be resolved is kept verbatim, so it is
reported as ``unresolved_spec`` and the row still names the property.

A missing, unreadable or uninterpretable file raises UnreadableManifest rather
than returning [] -- see that class. "Uninterpretable" includes a DOCTYPE, a
root element that is not ``<project>`` and a dependency missing its groupId or
artifactId. [] is reserved for a pom that declares no dependency, which a
parent or aggregator pom legitimately does. A *parent* that cannot be read
never raises: the file being scanned was read, and the parent costs only the
versions it would have supplied.

Elements are matched by local name, ignoring the namespace. Every pom.xml on
the development estate declares ``xmlns="http://maven.apache.org/POM/4.0.0"``,
but it is optional, and matching either spelling only would report the other
as declaring nothing.

Per-format notes and the measurements behind these decisions are in
docs/dependency-details.md.
"""

import os
import re
import xml.etree.ElementTree as ET

from kospex.extractors.errors import UnreadableManifest
from kospex_utils import get_kospex_logger

logger = get_kospex_logger("extractors.maven")

# A chain deeper than this is not a real project layout. The cycle check below
# is what stops a loop; this bounds a long acyclic chain.
_MAX_PARENT_DEPTH = 10

# Property expansion is CWE-776's mechanism without a DTD: `<p1>${p0}${p0}</p1>`
# doubles at each level, so thirty levels reach a billion copies. Properties
# come from cloned third-party repositories. Past either bound the declared
# text is kept instead, which is what an unresolvable property gets anyway.
_MAX_INTERPOLATION_PASSES = 10
_MAX_INTERPOLATED_LENGTH = 1024

_PROPERTY = re.compile(r"\$\{([^}]+)\}")


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
        # Install state (#227). None means this format cannot say.
        "is_dev": None,
        "is_optional": None,
        "runs_install_script": None,
        "declared_scope": None,
    }


def _local(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _children(element, name):
    if element is None:
        return []
    return [child for child in element if _local(child.tag) == name]


def _child(element, name):
    found = _children(element, name)
    return found[0] if found else None


def _text(element, name):
    """Stripped text of a direct child, or None when the child is absent."""
    found = _child(element, name)
    if found is None:
        return None
    return (found.text or "").strip()


def _read(path):
    """Parse a pom.xml to its <project> root. Raises UnreadableManifest."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            xml_data = handle.read()
    except FileNotFoundError as exc:
        raise UnreadableManifest(f"{path}: file not found") from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise UnreadableManifest(f"{path}: could not read: {exc}") from exc

    # CWE-776: uncontrolled entity expansion. ElementTree refuses external
    # entities but expands internal ones, which must be declared in a DTD. No
    # pom.xml on the development estate carries a DOCTYPE, so refusing it
    # removes the class outright. Checked before parsing -- filtering
    # afterwards means the expansion already ran -- and outside the read's
    # try/except, where a broad `except` would swallow the refusal.
    if "<!DOCTYPE" in xml_data:
        raise UnreadableManifest(
            f"{path}: refusing a pom.xml carrying a DOCTYPE declaration "
            f"(entity expansion)")

    try:
        root = ET.fromstring(xml_data)
    except ET.ParseError as exc:
        raise UnreadableManifest(f"{path}: invalid XML: {exc}") from exc

    # Any XML file under a pom.xml name would otherwise report as a project
    # declaring no dependencies. All 14 on the development estate have a
    # <project> root.
    root_tag = _local(root.tag)
    if root_tag != "project":
        raise UnreadableManifest(
            f"{path}: root element is <{root_tag}>, not <project>")
    return root


def _repo_root(path):
    """The nearest enclosing directory holding `.git`, or None.

    A `.git` file (a worktree or submodule) counts as well as a directory.
    """
    directory = os.path.dirname(os.path.realpath(path))
    while True:
        if os.path.exists(os.path.join(directory, ".git")):
            return directory
        parent = os.path.dirname(directory)
        if parent == directory:
            return None
        directory = parent


def _inside(path, root):
    return os.path.commonpath([path, root]) == root


def _parent_path(child_path, parent, repo_root):
    """Where Maven would look for the parent on disk, if it may be read.

    `<relativePath/>` (empty) is Maven's way to say "look only in a
    repository". The path comes from a cloned third-party repository, so it
    is resolved through symlinks and refused if it leaves `repo_root`.
    """
    relative = _text(parent, "relativePath")
    if relative is None:
        relative = "../pom.xml"
    if not relative:
        return None

    candidate = os.path.join(os.path.dirname(child_path), relative)
    if os.path.isdir(candidate):
        candidate = os.path.join(candidate, "pom.xml")
    candidate = os.path.realpath(candidate)
    if not _inside(candidate, repo_root):
        logger.info("Not following parent %s outside the repository %s",
                    candidate, repo_root)
        return None
    if not os.path.isfile(candidate):
        return None
    return candidate


def _coordinates(root):
    """(groupId, artifactId, version), inheriting from <parent> as Maven does."""
    parent = _child(root, "parent")
    group = _text(root, "groupId") or _text(parent, "groupId") or ""
    version = _text(root, "version") or _text(parent, "version") or ""
    return group, _text(root, "artifactId") or "", version


def _ancestors(path, root):
    """Parent roots inside the repository, nearest first.

    Stops at the first parent that is absent, outside the repository,
    unreadable, or not the project the child names -- Maven checks the
    coordinates and falls back to a repository when the file at relativePath
    is some other project, and so does this.
    """
    repo_root = _repo_root(path)
    if repo_root is None:
        return []

    chain = []
    seen = {os.path.realpath(path)}
    current_path, current = os.path.realpath(path), root
    while len(chain) < _MAX_PARENT_DEPTH:
        parent = _child(current, "parent")
        if parent is None:
            break
        parent_path = _parent_path(current_path, parent, repo_root)
        if parent_path is None or parent_path in seen:
            break
        try:
            parent_root = _read(parent_path)
        except UnreadableManifest as exc:
            logger.warning("Could not read parent pom %s: %s", parent_path, exc)
            break

        group, artifact, version = _coordinates(parent_root)
        wanted = (_text(parent, "groupId") or "", _text(parent, "artifactId") or "",
                  _text(parent, "version") or "")
        if (group, artifact) != wanted[:2] or (wanted[2] and version != wanted[2]):
            logger.info("%s is not the parent %s named by %s",
                        parent_path, ":".join(wanted), current_path)
            break

        chain.append(parent_root)
        seen.add(parent_path)
        current_path, current = parent_path, parent_root
    return chain


def _root_first(root, ancestors):
    """The chain from the furthest ancestor down to the child, so that a
    nearer declaration overwrites a further one."""
    return list(reversed(ancestors)) + [root]


def _properties(root, ancestors):
    """Effective properties: ancestors first, so the child's override theirs.

    The built-in `project.*` values are the child's, because Maven
    interpolates after inheritance -- `${project.version}` in a parent's
    <dependencyManagement> means the version of the project being built.
    """
    properties = {}
    for model in _root_first(root, ancestors):
        for prop in _child(model, "properties") or []:
            properties[_local(prop.tag)] = (prop.text or "").strip()

    group, artifact, version = _coordinates(root)
    properties.update({
        "project.groupId": group,
        "project.artifactId": artifact,
        "project.version": version,
    })
    parent = _child(root, "parent")
    if parent is not None:
        for name in ("groupId", "artifactId", "version"):
            value = _text(parent, name)
            if value:
                properties[f"project.parent.{name}"] = value
    return properties


def _expand_once(text, properties):
    """One substitution pass, or None once it would pass the length cap.

    The running length is checked before each piece is appended, so the cap
    bounds memory as well as the result. Checking after a `re.sub` let a
    single pass -- many references to one large property -- allocate the
    whole expansion first.
    """
    pieces, length, position = [], 0, 0
    for match in _PROPERTY.finditer(text):
        replacement = properties.get(match.group(1), match.group(0))
        length += (match.start() - position) + len(replacement)
        if length > _MAX_INTERPOLATED_LENGTH:
            return None
        pieces.append(text[position:match.start()])
        pieces.append(replacement)
        position = match.end()
    length += len(text) - position
    if length > _MAX_INTERPOLATED_LENGTH:
        return None
    pieces.append(text[position:])
    return "".join(pieces)


def _interpolate(value, properties):
    """Expand ${...} references; an unknown one is left as written."""
    if not value or "${" not in value:
        return value
    if len(value) > _MAX_INTERPOLATED_LENGTH:
        logger.warning("Declared value of %d characters exceeds %d; not "
                       "expanding it", len(value), _MAX_INTERPOLATED_LENGTH)
        return value
    result = value
    for _ in range(_MAX_INTERPOLATION_PASSES):
        expanded = _expand_once(result, properties)
        if expanded is None:
            logger.warning("Property expansion of %r exceeds %d characters; "
                           "keeping the declared text", value[:80],
                           _MAX_INTERPOLATED_LENGTH)
            return value
        if expanded == result:
            return expanded
        result = expanded
    return result


def _key(dependency, properties):
    """Maven's management key: groupId, artifactId, type and classifier."""
    return (
        _interpolate(_text(dependency, "groupId") or "", properties),
        _interpolate(_text(dependency, "artifactId") or "", properties),
        _text(dependency, "type") or "jar",
        _text(dependency, "classifier") or "",
    )


def _managed_versions(root, ancestors, properties):
    """Versions from <dependencyManagement>, nearest declaration winning."""
    managed = {}
    for model in _root_first(root, ancestors):
        section = _child(_child(model, "dependencyManagement"), "dependencies")
        for dependency in _children(section, "dependency"):
            version = _text(dependency, "version")
            if version:
                managed[_key(dependency, properties)] = _interpolate(version, properties)
    return managed


def _dependencies(path, section):
    """The <dependency> entries of a <dependencies> block, validated."""
    entries = _children(_child(section, "dependencies"), "dependency")
    for dependency in entries:
        for name in ("groupId", "artifactId"):
            if not _text(dependency, name):
                # Maven refuses such a pom. Skipping the entry would report
                # the file as declaring fewer dependencies than it does.
                raise UnreadableManifest(
                    f"{path}: a <dependency> has no {name}")
    return entries


def _record(name, version, scope):
    """A record without requirements_type, which each caller assigns as a
    literal: tests/test_requirements_type_mapping.py finds the values a parser
    emits by reading its source, and cannot see one passed through a variable."""
    record = _template()
    record["package_name"] = name
    record["package_version"] = version
    record["ecosystem"] = "maven"
    # Raw, per migration 0009. Absent stays None even though Maven reads it as
    # compile: the file did not say `compile`.
    record["declared_scope"] = scope
    return record


def extract_pom(path):
    """Parse a pom.xml file into dependency records.

    Returns template-shaped dicts with package_name (``groupId:artifactId``),
    package_version, requirements_type (``direct``, or ``bom`` for an
    imported BOM), declared_scope and is_dev populated. Empty list for a
    readable pom that declares no dependency.

    Raises:
        UnreadableManifest: the file is missing, unreadable, not well-formed
            XML, carries a DOCTYPE, is not a Maven project, or declares a
            dependency with no groupId or artifactId.
    """
    root = _read(path)
    ancestors = _ancestors(path, root)
    properties = _properties(root, ancestors)
    managed = _managed_versions(root, ancestors, properties)

    records = []
    for dependency in _dependencies(path, root):
        key = _key(dependency, properties)
        version = _text(dependency, "version")
        if version:
            version = _interpolate(version, properties)
        else:
            version = managed.get(key, "")
        scope = _text(dependency, "scope") or None

        record = _record(f"{key[0]}:{key[1]}", version, scope)
        record["requirements_type"] = "direct"
        # Only `test` keeps a dependency out of what ships; see
        # docs/dependency-flags.md for why provided/runtime are not "dev".
        # <optional> is not is_optional: in Maven it means "not passed on to
        # consumers", not "may not install".
        record["is_dev"] = scope == "test"
        records.append(record)

    # An imported BOM is where missing versions come from, so it is the one
    # management entry recorded. It is not a dependency of the project, so
    # is_dev stays None.
    for dependency in _dependencies(path, _child(root, "dependencyManagement")):
        if _text(dependency, "scope") != "import":
            continue
        key = _key(dependency, properties)
        # Maven imports only `type=pom`; any other type gets a model warning
        # and stays an ordinary management entry, which is not a row.
        if key[2] != "pom":
            logger.warning("%s: %s:%s has scope import but type %s, not pom; "
                           "Maven does not import it", path, key[0], key[1], key[2])
            continue
        version = _interpolate(_text(dependency, "version") or "", properties)
        record = _record(f"{key[0]}:{key[1]}", version, "import")
        record["requirements_type"] = "bom"
        records.append(record)

    return records
