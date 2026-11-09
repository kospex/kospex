"""Extractor for Gradle version catalogs (``gradle/libs.versions.toml``).

Phase 1 of the Gradle design, changes/202611-gradle-dependency-parsing.md.
A catalog is where a Gradle build sets the versions its modules use; a build
file decides which of them a module actually uses. Build files
(``build.gradle``, ``build.gradle.kts``) are phase 2 and not read here.

Returns one record per ``[libraries]`` entry and one per ``[plugins]`` entry,
shaped to KospexDependencies.get_package_template() so ``krunner osi``
consumes the records unchanged. ``requirements_type`` is ``version-catalog``,
which has its own package_use: a catalog row is checked for advisories like
any other, but it is not a direct dependency, and build-file rows will be.
``[bundles]`` only group libraries and produce no rows.

Package names are ``groupId:artifactId``, as deps.dev names a Maven package.
A plugin is named by its marker artifact, ``<id>:<id>.gradle.plugin``, which is
how Gradle resolves a plugin id from a Maven repository.

``ecosystem`` is ``gradle`` while ``package_type`` stays ``maven`` (deps.dev's
system). Both write paths classify the version by ``ecosystem`` when it says
``gradle``, because a bare Gradle version is a minimum, not Maven's soft pin.

A version is the one Gradle would require: ``strictly``, else ``require``,
else ``prefer``, from a plain string, an inline rich version or a
``version.ref`` into ``[versions]``. A library with no version (a platform
supplies it) and the refreshVersions placeholder ``_`` are recorded as "",
which enrichment reports as ``no_version``.

Pure: no DB, no CLI, no network. A missing, unreadable or uninterpretable file
raises UnreadableManifest rather than returning [] -- see that class. Gradle
refuses a whole catalog over one bad entry (an unknown ``version.ref``, a
library with no module), so this does too. [] is reserved for a catalog that
declares no library or plugin.

Per-format notes are in docs/dependency-details.md.
"""

import tomllib

from kospex.extractors.errors import UnreadableManifest
from kospex_utils import get_kospex_logger

logger = get_kospex_logger("extractors.gradle")

# The refreshVersions plugin's placeholder: the version is in
# versions.properties, which this does not read.
_REFRESH_VERSIONS_PLACEHOLDER = "_"

# Rich-version keys, in the order Gradle lets them decide the version.
_RICH_ORDER = ("strictly", "require", "prefer")
_RICH_KEYS = frozenset(_RICH_ORDER + ("reject", "rejectAll"))


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


def _read(path):
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except FileNotFoundError as e:
        raise UnreadableManifest(f"{path}: file not found") from e
    except OSError as e:
        raise UnreadableManifest(f"{path}: could not read: {e}") from e
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise UnreadableManifest(f"{path}: not UTF-8: {e}") from e
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise UnreadableManifest(f"{path}: invalid TOML: {e}") from e


def _section(path, doc, name):
    section = doc.get(name, {})
    if not isinstance(section, dict):
        raise UnreadableManifest(
            f"{path}: [{name}] is {type(section).__name__}, not a table")
    return section


def _rich(path, where, value):
    """The version a plain or rich version selects; "" when it selects none."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and value and set(value) <= _RICH_KEYS:
        for key in _RICH_ORDER:
            if isinstance(value.get(key), str):
                return value[key]
        return ""      # only reject / rejectAll
    raise UnreadableManifest(f"{path}: {where} has an unreadable version: {value!r}")


def _version(path, where, value, versions):
    """Resolve an entry's ``version``: absent, plain, rich or ``ref``."""
    if value is None:
        return ""
    if isinstance(value, dict) and "ref" in value:
        ref = value["ref"]
        if len(value) != 1 or not isinstance(ref, str) or ref not in versions:
            raise UnreadableManifest(f"{path}: {where} refers to unknown version {ref!r}")
        return _rich(path, f"[versions] {ref}", versions[ref])
    return _rich(path, where, value)


def _coordinates(path, alias, entry):
    """(group, artifact, version-or-None) of a ``[libraries]`` entry."""
    where = f"library {alias!r}"
    if isinstance(entry, str):
        parts = entry.split(":")
        if len(parts) in (2, 3) and all(parts[:2]):
            return parts[0], parts[1], (parts[2] if len(parts) == 3 else None)
    elif isinstance(entry, dict):
        module = entry.get("module")
        if isinstance(module, str):
            parts = module.split(":")
            if len(parts) == 2 and all(parts):
                return parts[0], parts[1], entry.get("version")
        group, name = entry.get("group"), entry.get("name")
        if module is None and isinstance(group, str) and isinstance(name, str) \
                and group and name:
            return group, name, entry.get("version")
    raise UnreadableManifest(f"{path}: {where} is not group:artifact: {entry!r}")


def _plugin(path, alias, entry):
    """(plugin id, version-or-None) of a ``[plugins]`` entry."""
    if isinstance(entry, str) and entry:
        plugin_id, _, version = entry.partition(":")
        if plugin_id:
            return plugin_id, (version or None)
    elif isinstance(entry, dict) and isinstance(entry.get("id"), str) and entry["id"]:
        return entry["id"], entry.get("version")
    raise UnreadableManifest(f"{path}: plugin {alias!r} has no id: {entry!r}")


def _record(name, version):
    """A record without requirements_type, which the caller assigns as a
    literal: tests/test_requirements_type_mapping.py finds the values a parser
    emits by reading its source, and cannot see one passed through a variable."""
    record = _template()
    record["package_name"] = name
    record["package_version"] = version
    record["ecosystem"] = "gradle"
    return record


def _placeholder(path, alias, version):
    if version == _REFRESH_VERSIONS_PLACEHOLDER:
        logger.info("%s: %s uses the refreshVersions placeholder `_`; "
                    "its version is in versions.properties, which is not read",
                    path, alias)
        return ""
    return version


def extract_version_catalog(path):
    """Parse a Gradle version catalog into dependency records.

    Returns template-shaped dicts with package_name, package_version,
    requirements_type (``version-catalog``), ecosystem (``gradle``),
    declared_scope and is_dev populated. Empty list for a catalog with no
    ``[libraries]`` or ``[plugins]`` entry.

    Raises:
        UnreadableManifest: the file is missing, unreadable, not UTF-8, not
            TOML, has a section that is not a table, or has an entry Gradle
            would refuse (no module, an unknown ``version.ref``, an unreadable
            version).
    """
    doc = _read(path)
    versions = _section(path, doc, "versions")
    libraries = _section(path, doc, "libraries")
    plugins = _section(path, doc, "plugins")

    records = []
    seen = set()

    def _add(record):
        # (name, version) is in dependency_data's primary key, so two aliases
        # for the same artifact at the same version are one row.
        key = (record["package_name"], record["package_version"])
        if key not in seen:
            seen.add(key)
            records.append(record)

    for alias, entry in libraries.items():
        group, artifact, declared = _coordinates(path, alias, entry)
        version = _version(path, f"library {alias!r}", declared, versions)
        record = _record(f"{group}:{artifact}", _placeholder(path, alias, version))
        record["requirements_type"] = "version-catalog"
        # A catalog doesn't say whether a library is for tests; is_dev and
        # declared_scope stay None until a build file says how it is used.
        _add(record)

    for alias, entry in plugins.items():
        plugin_id, declared = _plugin(path, alias, entry)
        version = _version(path, f"plugin {alias!r}", declared, versions)
        record = _record(f"{plugin_id}:{plugin_id}.gradle.plugin",
                         _placeholder(path, alias, version))
        record["requirements_type"] = "version-catalog"
        # Build tooling, not the product: the same rule as a build file's
        # plugins { } and buildscript classpath.
        record["declared_scope"] = "plugin"
        record["is_dev"] = True
        _add(record)

    return records
