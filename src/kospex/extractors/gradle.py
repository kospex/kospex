"""Extractors for Gradle version catalogs and build files.

Changes/202611-gradle-dependency-parsing.md is the design. A catalog
(``gradle/libs.versions.toml``, phase 1) is where a Gradle build sets the
versions its modules use; a build file (``build.gradle``,
``build.gradle.kts``, phase 2) decides which of them a module actually uses.
``extract_build_gradle`` is at the end of this module; what follows describes
the catalog reader.

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

import bisect
import functools
import os
import re
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


# ---------------------------------------------------------------------------
# Build files: build.gradle (Groovy) and build.gradle.kts (Kotlin), phase 2.
#
# A build file is a program, and Gradle isn't run (see the design doc). The
# reader recognises the declaration forms measured on the development estate
# (98.7% of package declarations) and counts the rest as unreadable, logging
# each one. A file with declarations and none readable raises (decision 3).
# ---------------------------------------------------------------------------

# Limits on the work a build file can cause (CWE-400). Every file read here
# comes from a cloned third-party repository. Measured on 1,495 real build
# files: the largest is 36 KB, braces nest at most 8 deep, no string holds more
# than 4 references and no assignment is longer than 125 characters.
_MAX_FILE_BYTES = 1024 * 1024
_MAX_NESTING = 32
# As maven.py's: `"$a$a$a"` where each $a is `"$b$b$b"` multiplies at each level.
_MAX_INTERPOLATED_LENGTH = 1024

_SETTINGS_FILES = ("settings.gradle", "settings.gradle.kts")
_BUILD_FILES = ("build.gradle", "build.gradle.kts")
_DEFAULT_CATALOG = os.path.join("gradle", "libs.versions.toml")

# Statements in a dependencies { } block that aren't declarations.
_NOT_DECLARATIONS = frozenset({
    "val", "var", "def", "final", "exclude", "because", "println", "logger",
    "components", "modules", "attributesSchema", "artifactTypes", "registerTransform",
})
_CONTROL_FLOW = frozenset({"if", "else", "for", "while", "when", "try", "catch", "finally"})

# Configuration names that only build or run tests: test*, androidTest*,
# testFixtures*, and a variant- or tool-prefixed form (debugTestImplementation,
# kspAndroidTest, kaptTest). compileOnly / kapt / ksp / annotationProcessor are
# build-time but not tests, and stay False, as the pom parser treats `provided`.
_TEST_CONFIGURATION = re.compile(
    r"^(?:test|androidTest|testFixtures)|[a-z](?:Test|AndroidTest|UnitTest|TestFixtures)(?=[A-Z]|$)")
_TEST_SOURCE_SET = re.compile(r"(?:^test|Test)$")

_NOT_A_PACKAGE = re.compile(r"^(files|fileTree|gradleApi|gradleTestKit|localGroovy)\s*\(")
_FIRST_PARTY = re.compile(
    r"^(?:project\s*\(|projects\.|testFixtures\s*\(\s*(?:project\s*\(|projects\.))")
_WRAPPER = re.compile(r"^(platform|enforcedPlatform|testFixtures)\s*\((.*)\)$", re.S)
_KOTLIN_MODULE = re.compile(
    r"""^kotlin\s*\(\s*["']([\w.\-]+)["']\s*(?:,\s*(.+?))?\s*\)$""", re.S)
_STRING = re.compile(r'''^(?:"""(.*)"""|"([^"]*)"|'([^']*)')$''', re.S)
_COORDINATE_PART = re.compile(r"^[\w.\-]+$")
_CATALOG_REFERENCE = re.compile(r"^(\w+)\.([\w.]+?)(?:\.get\(\))?$")
_MAP_KEY = re.compile(r"""^(\w+)\s*[:=]\s*(.+)$""", re.S)
_CALL = re.compile(r"""^(?:"(\w+)"|(\w+))(?:\s*(\()|\s+(?=["'\w\[])|\s*$)""")
_NUMBER = re.compile(r"^\d[\w.\-]*$")
_INTERPOLATION = re.compile(r"\$\{([^}]*)\}|\$([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)")
_SCOPE_PREFIXES = ("rootProject.ext.", "rootProject.extra.", "project.ext.",
                   "project.extra.", "ext.", "extra.", "rootProject.", "project.")
_PROPERTY_CALL = re.compile(
    r"""^(?:project\.|rootProject\.|providers\.)?(?:property|findProperty|gradleProperty)"""
    r"""\(\s*["']([\w.\-]+)["']\s*\)(?:\.get\(\)|\.orNull|\.toString\(\))*$""")
_EXTRA_INDEX = re.compile(r"""^(?:rootProject\.|project\.)?(?:extra|ext)\[\s*["'](\w+)["']\s*\]$""")

_ASSIGNMENTS = (
    re.compile(r"""(?:^|\n)[ \t]*(?:val|var|def|final\s+\w+|String)\s+(\w+)\s*(?::\s*\w+\s*)?=\s*(.+)"""),
    re.compile(r"""(?:^|\n)[ \t]*(?:rootProject\.|project\.)?ext\.(\w+)\s*=\s*(.+)"""),
    re.compile(r"""(?:^|\n)[ \t]*(?:rootProject\.|project\.)?extra\[\s*["'](\w+)["']\s*\]\s*=\s*(.+)"""),
    re.compile(r"""(?:^|\n)[ \t]*set\(\s*["'](\w+)["']\s*,\s*(.+?)\)[ \t]*(?=\n|$)"""),
    re.compile(r"""(?:^|\n)[ \t]*val\s+(\w+)\s+by\s+extra\(\s*(.+?)\)[ \t]*(?=\n|$)"""),
)
_BLOCK_ASSIGNMENT = re.compile(r"""(?:^|\n)[ \t]*(\w+)\s*=\s*(.+)""")


# -- scanning ---------------------------------------------------------------

def _skip_string(text, i):
    """Index just past the string literal starting at text[i]."""
    for quote in ('"""', "'''"):
        if text.startswith(quote, i):
            end = text.find(quote, i + 3)
            return len(text) if end < 0 else end + 3
    quote, j, n = text[i], i + 1, len(text)
    while j < n and text[j] != quote and text[j] != "\n":
        j += 2 if text[j] == "\\" else 1
    return j + 1


def _strip_comments(text):
    """Blank out // and /* */ comments, keeping strings, offsets and lines."""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c in "\"'":
            j = _skip_string(text, i)
            out.append(text[i:j])
            i = j
        elif text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i))
            i = j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append(re.sub(r"[^\n]", " ", text[i:j]))
            i = j
        else:
            out.append(c)
            i += 1
    return "".join(out)


_PAIRS = {"{": "}", "(": ")", "[": "]"}
_NON_SPACE = re.compile(r"\S")


def _close(text, i):
    """Index of the bracket that closes text[i]; len(text) if it never does."""
    stack, j, n = [_PAIRS[text[i]]], i + 1, len(text)
    while j < n:
        c = text[j]
        if c in "\"'":
            j = _skip_string(text, j)
            continue
        if c in _PAIRS:
            stack.append(_PAIRS[c])
        elif c == stack[-1]:
            stack.pop()
            if not stack:
                return j
        j += 1
    return n


def _statements(text, start, end):
    """(offset, statement) for each top-level statement of text[start:end].

    A statement ends at a newline or `;` outside brackets, so a call keeps its
    trailing closure (`implementation("x") { exclude(...) }`). A line ending in
    `,`, `+` or `.`, or a next line starting with `.`, continues it.

    Linear in the block's length: nothing is sliced per newline. `last` is the
    statement's last non-space character so far, and `ahead` caches where the
    next non-space character is, so a run of blank lines is scanned once."""
    out, i, begin = [], start, None
    last, ahead = "", -1

    def _emit(stop):
        if begin is not None:
            stmt = text[begin:stop].strip()
            if stmt:
                out.append((begin, stmt))

    def _last_non_space(chunk_start, stop):
        j = stop - 1
        while j > chunk_start and text[j].isspace():
            j -= 1
        return text[j]

    while i < end:
        c = text[i]
        if c in "\"'":
            begin = i if begin is None else begin
            chunk, i = i, min(_skip_string(text, i), end)
            last = _last_non_space(chunk, i)
        elif c in _PAIRS:
            begin = i if begin is None else begin
            chunk, i = i, min(_close(text, i), end - 1) + 1
            last = _last_non_space(chunk, i)
        elif c in "\n;":
            if c == "\n" and begin is not None:
                if last in ",+.":
                    i += 1
                    continue
                if ahead <= i:
                    m = _NON_SPACE.search(text, i + 1, end)
                    ahead = m.start() if m else end
                if text.startswith(".", ahead, end) or text.startswith("?.", ahead, end):
                    i += 1
                    continue
            _emit(i)
            begin, last = None, ""
            i += 1
        else:
            if not c.isspace():
                begin = i if begin is None else begin
                last = c
            i += 1
    _emit(end)
    return out


def _brace_groups(stmt):
    """(head, body_start, body_end) for each top-level { } in a statement."""
    groups, i, prev, n = [], 0, 0, len(stmt)
    while i < n:
        c = stmt[i]
        if c in "\"'":
            i = _skip_string(stmt, i)
        elif c in "([":
            i = _close(stmt, i) + 1
        elif c == "{":
            j = _close(stmt, i)
            groups.append((stmt[prev:i].strip(), i + 1, j))
            prev = i = j + 1
        else:
            i += 1
    return groups


def _split_arguments(text):
    """Top-level comma-separated arguments."""
    out, depth, begin, i, n = [], 0, 0, 0, len(text)
    while i < n:
        c = text[i]
        if c in "\"'":
            i = _skip_string(text, i)
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "," and depth == 0:
            out.append(text[begin:i].strip())
            begin = i + 1
        i += 1
    out.append(text[begin:].strip())
    return [a for a in out if a]


def _string_value(match):
    """The contents of whichever quote form _STRING matched."""
    return next(g for g in match.groups() if g is not None)


# -- the build's context: properties, assignments, catalogs -----------------

def _too_large(path):
    return UnreadableManifest(f"{path}: larger than {_MAX_FILE_BYTES} bytes; not read")


def _read_text(path):
    try:
        with open(path, "rb") as f:
            if os.fstat(f.fileno()).st_size > _MAX_FILE_BYTES:
                raise _too_large(path)
            raw = f.read(_MAX_FILE_BYTES + 1)
    except FileNotFoundError as e:
        raise UnreadableManifest(f"{path}: file not found") from e
    except OSError as e:
        raise UnreadableManifest(f"{path}: could not read: {e}") from e
    if len(raw) > _MAX_FILE_BYTES:      # grew since the stat, or not a regular file
        raise _too_large(path)
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise UnreadableManifest(f"{path}: not UTF-8: {e}") from e


def _properties_file(path):
    """A gradle.properties file as a dict; {} if absent or unreadable."""
    found = {}
    try:
        if os.path.getsize(path) > _MAX_FILE_BYTES:
            logger.info("Not reading %s: larger than %d bytes", path, _MAX_FILE_BYTES)
            return found
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or line[0] in "#!":
                    continue
                m = re.match(r"([^=:\s]+)\s*[=:]\s*(.*)", line)
                if m:
                    found[m.group(1)] = m.group(2).strip()
    except OSError:
        pass
    return found


def _assignments(text):
    """{name: right-hand side} for simple variable and extra-property assignments."""
    found = {}
    for pattern in _ASSIGNMENTS:
        for name, value in pattern.findall(text):
            found.setdefault(name, value.strip())
    read_to = -1
    for m in re.finditer(r"(?<![\w.])ext\s*\{", text):
        brace = m.end() - 1
        if brace < read_to:
            # Inside an ext { } already read, whose scan covered these lines.
            # Reading it again made nested blocks quadratic or worse.
            continue
        read_to = _close(text, brace)
        body = text[brace + 1:read_to]
        for name, value in _BLOCK_ASSIGNMENT.findall(body):
            found.setdefault(name, value.strip())
    return found


def _build_root(directory):
    """The directories from `directory` up to the build root, nearest first.

    The build root is the nearest directory with a settings file, or failing
    that a repository root (`.git`). With neither, only `directory` itself is
    used, so nothing outside the checkout is read."""
    chain, d = [], directory
    while True:
        chain.append(d)
        if any(os.path.isfile(os.path.join(d, f)) for f in _SETTINGS_FILES) \
                or os.path.exists(os.path.join(d, ".git")):
            return chain
        parent = os.path.dirname(d)
        if parent == d:
            return [directory]
        d = parent


def _repository_root(directory):
    """The nearest directory at or above `directory` with `.git`; else itself."""
    d = directory
    while not os.path.exists(os.path.join(d, ".git")):
        parent = os.path.dirname(d)
        if parent == d:
            return directory
        d = parent
    return d


def _accessor(alias):
    """A catalog alias as its Kotlin/Groovy accessor: -, _ and . all become `.`."""
    return re.sub(r"[-_.]", ".", alias)


@functools.lru_cache(maxsize=64)
def _cached_assignments(path, _mtime_ns):
    return _assignments(_strip_comments(_read_text(path)))


def _file_assignments(path):
    """A root build file's assignments, cached as the catalog is."""
    try:
        return _cached_assignments(path, os.stat(path).st_mtime_ns)
    except (OSError, UnreadableManifest):
        return {}


@functools.lru_cache(maxsize=64)
def _catalog(path, _mtime_ns):
    """{"libraries", "bundles", "plugins", "versions"} accessors of a catalog.

    Cached by modification time: krunner reads every build file of a repository
    in turn, and androidx has over a thousand sharing one catalog."""
    doc = _read(path)
    versions = _section(path, doc, "versions")
    libraries, plugins, resolved_versions = {}, {}, {}
    for alias, value in versions.items():
        try:
            resolved_versions[_accessor(alias)] = _rich(path, f"[versions] {alias}", value)
        except UnreadableManifest:
            continue
    for alias, entry in _section(path, doc, "libraries").items():
        group, artifact, declared = _coordinates(path, alias, entry)
        version = _version(path, f"library {alias!r}", declared, versions)
        libraries[_accessor(alias)] = (f"{group}:{artifact}",
                                       _placeholder(path, alias, version))
    for alias, entry in _section(path, doc, "plugins").items():
        plugin_id, declared = _plugin(path, alias, entry)
        version = _version(path, f"plugin {alias!r}", declared, versions)
        plugins[_accessor(alias)] = (plugin_id, _placeholder(path, alias, version))
    bundles = {}
    for alias, members in _section(path, doc, "bundles").items():
        if isinstance(members, list):
            bundles[_accessor(alias)] = [_accessor(m) for m in members if isinstance(m, str)]
    return {"libraries": libraries, "bundles": bundles, "plugins": plugins,
            "versions": resolved_versions}


def _load_catalog(path):
    try:
        return _catalog(path, os.stat(path).st_mtime_ns)
    except (OSError, UnreadableManifest) as e:
        logger.warning("Gradle catalog %s is unreadable, so references to it are "
                       "not resolved: %s", path, e)
        return None


def _side_file(path, repository):
    """`path` resolved through symlinks, if it is a file inside `repository`.

    Every file the build reader opens besides the build file itself comes
    from a cloned third-party repository, and values read from it end up in
    versions sent to deps.dev. A symlink (or a `..` path) to a file outside
    the repository, such as the user's ~/.gradle/gradle.properties, is
    refused rather than followed (CWE-59), as maven.py refuses a parent pom
    outside the repository."""
    real = os.path.realpath(path)
    if not os.path.isfile(real):
        return None
    if os.path.commonpath([real, repository]) != repository:
        logger.info("Not reading %s: it resolves to %s, outside the repository %s",
                    path, real, repository)
        return None
    return real


def _catalogs(chain, repository):
    """{accessor name: catalog} for the build containing chain[0].

    `libs` is gradle/libs.versions.toml beside the build's settings file, as
    Gradle reads it: a subdirectory's own catalog applies only when that
    subdirectory is a build of its own, with its own settings file. A settings
    file's versionCatalogs { create("x") { from(files("...")) } } adds or
    replaces a catalog when the path is a literal inside the repository."""
    found = {}
    root = chain[-1]
    default = _side_file(os.path.join(root, _DEFAULT_CATALOG), repository)
    if default:
        found["libs"] = default
    # An included build (buildSrc, build-logic) usually points at the main
    # build's catalog with "../gradle/libs.versions.toml": allowed while it
    # stays inside the repository.
    for name in _SETTINGS_FILES:
        settings = _side_file(os.path.join(root, name), repository)
        if not settings:
            continue
        try:
            text = _strip_comments(_read_text(settings))
        except UnreadableManifest:
            continue
        for m in re.finditer(
                r"""create\(\s*["'](\w+)["']\s*\)\s*\{[^{}]*?from\(\s*files\(\s*["']([^"'$]+)["']""",
                text):
            target = _side_file(os.path.join(root, m.group(2)), repository)
            if target:
                found[m.group(1)] = target
    loaded = {}
    for name, path in found.items():
        catalog = _load_catalog(path)
        if catalog is not None:
            loaded[name] = catalog
    return loaded


class _Context:
    """What a build file's declarations are resolved against."""

    def __init__(self, path):
        self.path = path
        real_path = os.path.realpath(path)
        chain = _build_root(os.path.dirname(real_path))
        repository = _repository_root(chain[-1])
        self.catalogs = _catalogs(chain, repository)
        # Gradle reads gradle.properties from the build root and the project
        # directory, the project's winning. Assignments come from the root
        # build file (rootProject.ext) and then this file.
        self.properties = {}
        for d in reversed(chain):
            properties = _side_file(os.path.join(d, "gradle.properties"), repository)
            if properties:
                self.properties.update(_properties_file(properties))
        self.assigned = {}
        root = chain[-1]
        for name in _BUILD_FILES:
            root_build = _side_file(os.path.join(root, name), repository)
            if root_build and root_build != real_path:
                self.assigned.update(_file_assignments(root_build))
        # (expression, depth) -> value. Without it, "$b$b..." where each $b is
        # "$c$c..." looks every name up once per reference: k*k lookups.
        self._values = {}

    def add_assignments(self, text):
        self.assigned.update(_assignments(text))
        self._values.clear()

    def value(self, expression, depth=0):
        """The string an expression names, or None if it can't be worked out
        or is longer than _MAX_INTERPOLATED_LENGTH."""
        key = (expression, depth)
        if key not in self._values:
            found = self._value(expression, depth)
            if found is not None and len(found) > _MAX_INTERPOLATED_LENGTH:
                found = None
            self._values[key] = found
        return self._values[key]

    def _value(self, expression, depth):
        expression = expression.strip()
        if depth > 5 or not expression:
            return None
        m = _STRING.match(expression)
        if m:
            resolved, missing = self.interpolate(_string_value(m), depth + 1)
            return None if missing else resolved
        if _NUMBER.match(expression):
            return expression
        m = _PROPERTY_CALL.match(expression)
        if m:
            return self.properties.get(m.group(1))
        m = _EXTRA_INDEX.match(expression)
        if m:
            expression = m.group(1)
        expression = re.sub(r"(?:\.get\(\)|\.orNull|\.toString\(\))+$", "", expression)
        catalog, found, accessor = expression.partition(".versions.")
        if found and catalog in self.catalogs:
            return self.catalogs[catalog]["versions"].get(accessor)
        keys = [expression] + [expression[len(p):] for p in _SCOPE_PREFIXES
                               if expression.startswith(p)]
        for key in keys:
            if key in self.assigned:
                return self.value(self.assigned[key], depth + 1)
            if key in self.properties:
                return self.properties[key]
        return None

    def interpolate(self, text, depth=0):
        """(text with $x / ${x} filled in, [names not found]).

        If filling in would make the text longer than _MAX_INTERPOLATED_LENGTH,
        the text comes back as written, with the name that crossed the limit
        counted as not found."""
        missing, too_long = [], []
        room = [_MAX_INTERPOLATED_LENGTH - len(text)]

        def _fill(m):
            if too_long:
                return m.group(0)
            name = m.group(1) if m.group(1) is not None else m.group(2)
            value = self.value(name, depth + 1)
            if value is None and m.group(2) and "." in name:
                # Kotlin's "$a.b" is $a followed by ".b"; Groovy's is a path.
                head, _, rest = name.partition(".")
                value = self.value(head, depth + 1)
                if value is not None:
                    value = f"{value}.{rest}"
            if value is None:
                missing.append(name)
                return m.group(0)
            room[0] -= len(value) - len(m.group(0))
            if room[0] < 0:
                too_long.append(name)
                return m.group(0)
            return value

        filled = _INTERPOLATION.sub(_fill, text)
        if too_long:
            logger.info("%s: filling in %s would be longer than %d characters; kept as "
                        "written", self.path, text[:100], _MAX_INTERPOLATED_LENGTH)
            return text, missing + too_long
        return filled, missing


# -- declarations -----------------------------------------------------------

def _clean_version(version):
    """A declared version as stored. `1.2!!` is Gradle's `strictly 1.2`,
    stored as `1.2`, as the catalog stores strictly (phase 1)."""
    return version.split("!!", 1)[0].strip()


def _coordinate(text, ctx):
    """(name, version) of a "g:a[:v[:classifier]][@ext]" string, or None.

    A name that can't be resolved is unreadable. A version that can't is kept
    as written, so enrichment reports `unresolved_spec`, as for `${revision}`
    in a pom."""
    resolved, _missing = ctx.interpolate(text)
    parts = resolved.split("@", 1)[0].split(":")
    if len(parts) not in (2, 3, 4) or not all(_COORDINATE_PART.match(p) for p in parts[:2]):
        return None
    if len(parts) == 2:
        return f"{parts[0]}:{parts[1]}", ""
    version = parts[2]
    if "$" in version:
        # Keep the declared text, not the partly filled one.
        declared = text.split("@", 1)[0].split(":")
        version = declared[2] if len(declared) >= 3 else version
    return f"{parts[0]}:{parts[1]}", _clean_version(version)


def _map_notation(arguments, ctx):
    """(name, version) from group: / name: / version: arguments, or None."""
    fields = {}
    for argument in arguments:
        m = _MAP_KEY.match(argument)
        if not m:
            return None
        fields[m.group(1)] = m.group(2)
    if "group" not in fields or "name" not in fields:
        return None
    group, name = ctx.value(fields["group"]), ctx.value(fields["name"])
    if not group or not name or not _COORDINATE_PART.match(group) \
            or not _COORDINATE_PART.match(name):
        return None
    version = ""
    if "version" in fields:
        version = ctx.value(fields["version"])
        if version is None:
            m = _STRING.match(fields["version"].strip())
            version = _string_value(m) if m else fields["version"]
    return f"{group}:{name}", _clean_version(version)


def _catalog_reference(argument, ctx):
    """[(name, version), ...] for libs.x / libs.bundles.x, or None."""
    m = _CATALOG_REFERENCE.match(argument)
    if not m or m.group(1) not in ctx.catalogs:
        return None
    catalog, accessor = ctx.catalogs[m.group(1)], m.group(2)
    if accessor.startswith("bundles."):
        members = catalog["bundles"].get(accessor[len("bundles."):])
        if members is None:
            return None
        return [catalog["libraries"][a] for a in members if a in catalog["libraries"]]
    if accessor in catalog["libraries"]:
        return [catalog["libraries"][accessor]]
    return None


def _notation(argument, ctx):
    """What one dependency notation is: ("rows", [(name, version)]),
    ("platform", wrapper, [(name, version)]), ("first-party",),
    ("not-a-package", kind) or ("unreadable",)."""
    argument = argument.strip()
    if _FIRST_PARTY.match(argument):
        return ("first-party",)
    m = _NOT_A_PACKAGE.match(argument)
    if m:
        return ("not-a-package", m.group(1))
    m = _WRAPPER.match(argument)
    if m:
        inner = _notation(m.group(2), ctx)
        if m.group(1) == "testFixtures" or inner[0] != "rows":
            return inner
        return ("platform", m.group(1), inner[1])
    m = _KOTLIN_MODULE.match(argument)
    if m:
        version = ctx.value(m.group(2)) if m.group(2) else ""
        return ("rows", [(f"org.jetbrains.kotlin:kotlin-{m.group(1)}",
                          _clean_version(version or ""))])
    m = _STRING.match(argument)
    if m:
        found = _coordinate(_string_value(m), ctx)
        return ("rows", [found]) if found else ("unreadable",)
    found = _catalog_reference(argument, ctx)
    if found is not None:
        return ("rows", found)
    if re.match(r"^[A-Za-z_][\w.]*$", argument):
        # A variable holding the coordinate: def sidecar = "g:a:1.0".
        value = ctx.value(argument)
        found = _coordinate(value, ctx) if value and ":" in value else None
        if found:
            return ("rows", [found])
    return ("unreadable",)


def _declaration(stmt):
    """(configuration, [argument text]) for a dependencies-block statement,
    or None when it isn't a call."""
    m = _CALL.match(stmt)
    if not m:
        return None
    configuration = m.group(1) or m.group(2)
    rest = stmt[m.end() - 1:] if m.group(3) else stmt[m.end():].strip()
    if rest.startswith("("):
        inner = rest[1:_close(rest, 0)]
    else:
        # Groovy: arguments without parentheses, up to a trailing closure.
        groups = _brace_groups(rest)
        inner = groups[0][0] if groups else rest
    arguments = _split_arguments(inner)
    if configuration == "add" and len(arguments) >= 2:
        name = _STRING.match(arguments[0])
        if name:
            return _string_value(name), arguments[1:2]
    return configuration, arguments


def _is_dev_configuration(configuration):
    return configuration == "classpath" or bool(_TEST_CONFIGURATION.search(configuration))


def _source_set(head):
    """The Kotlin source set or JVM test suite a block head names, or None."""
    head = head.strip()
    m = re.match(r"^(?:val|var)\s+(\w+)\s+by\s+(?:getting|creating|registering)\b", head) \
        or re.match(r"""^(?:getByName|named|create|maybeCreate|register)\s*(?:<[^>]*>)?\s*\(\s*["'](\w+)["']""", head) \
        or re.match(r"""^(?:sourceSets\.)?(\w+)$""", head) \
        or re.match(r"""^sourceSets\s*\[\s*["'](\w+)["']\s*\]$""", head)
    return m.group(1) if m else None


class _BuildReader:

    def __init__(self, path, text, ctx):
        self.path, self.text, self.ctx = path, text, ctx
        self.records, self.seen = [], {}
        self._newlines = None
        self.unreadable = 0

    # rows ------------------------------------------------------------------

    def _add(self, record):
        """One row per (name, version), the primary key's pair. When a package
        is declared twice, a product use wins over a test-only one."""
        key = (record["package_name"], record["package_version"])
        existing = self.seen.get(key)
        if existing is None:
            self.seen[key] = record
            self.records.append(record)
        elif existing["is_dev"] and record["is_dev"] is False:
            existing["is_dev"] = False
            existing["declared_scope"] = record["declared_scope"]

    def _line(self, offset):
        """The 1-based line of an offset. Counting newlines from the start for
        every logged line made a file of unreadable lines quadratic."""
        if self._newlines is None:
            self._newlines = [m.start() for m in re.finditer("\n", self.text)]
        return bisect.bisect_left(self._newlines, offset) + 1

    def _unreadable(self, offset, what):
        self.unreadable += 1
        logger.info("%s:%d: dependency kospex cannot read: %s", self.path,
                    self._line(offset), " ".join(what[:400].split())[:200])

    def _too_deep(self, start, depth):
        """A block nested past _MAX_NESTING is not read: each level re-scans its
        body, and Python's recursion limit is near 1,000. It counts as one
        unreadable declaration, so a file with nothing else raises."""
        if depth <= _MAX_NESTING:
            return False
        self._unreadable(start, f"a block nested more than {_MAX_NESTING} deep")
        return True

    # traversal -------------------------------------------------------------

    def walk(self, start, end, heads, depth=0):
        if self._too_deep(start, depth):
            return
        for offset, stmt in _statements(self.text, start, end):
            for head, body_start, body_end in _brace_groups(stmt):
                begin, finish = offset + body_start, offset + body_end
                if head == "dependencies" or head.endswith(".dependencies"):
                    self.dependencies(begin, finish, heads + [head], depth + 1)
                elif head == "plugins" and not heads:
                    self.plugins(begin, finish)
                else:
                    self.walk(begin, finish, heads + [head], depth + 1)

    @staticmethod
    def _source_set_of(heads):
        """The source set (or test suite) whose dependencies { } this is."""
        last = heads[-1]
        if last.endswith(".dependencies"):
            # commonMain.dependencies { }, sourceSets["jvmTest"].dependencies { }
            return _source_set(last[:-len(".dependencies")].split(".")[-1])
        if len(heads) >= 2 and any("sourceSets" in h or h == "suites" for h in heads[:-1]):
            return _source_set(heads[-2])
        return None

    def _scope(self, heads, configuration):
        """(declared_scope, is_dev) for a declaration under these block heads."""
        if "buildscript" in heads:
            return configuration, True
        source_set = self._source_set_of(heads)
        if source_set:
            return (f"{source_set}.{configuration}",
                    bool(_TEST_SOURCE_SET.search(source_set))
                    or _is_dev_configuration(configuration))
        return configuration, _is_dev_configuration(configuration)

    def dependencies(self, start, end, heads, depth=0):
        if self._too_deep(start, depth):
            return
        for offset, stmt in _statements(self.text, start, end):
            word = re.match(r"^(\w+)", stmt)
            word = word.group(1) if word else ""
            groups = _brace_groups(stmt)
            if word == "constraints":
                continue          # versions only, out of scope
            if word in _CONTROL_FLOW or (groups and groups[0][0] == word
                                         and word not in _NOT_DECLARATIONS):
                # if/else/for, or a bare block: what's inside is still a declaration.
                for _head, body_start, body_end in groups:
                    self.dependencies(offset + body_start, offset + body_end, heads, depth + 1)
                continue
            if word in _NOT_DECLARATIONS:
                continue
            found = _declaration(stmt)
            if found is None:
                if groups:
                    for _head, body_start, body_end in groups:
                        self.dependencies(offset + body_start, offset + body_end, heads, depth + 1)
                    continue
                self._unreadable(offset, stmt)
                continue
            configuration, arguments = found
            declared_scope, is_dev = self._scope(heads, configuration)
            if not arguments:
                self._unreadable(offset, stmt)
                continue
            if any((key := _MAP_KEY.match(a)) and key.group(1) in ("group", "name")
                   for a in arguments):
                arguments = [arguments]      # group: / name: / version: notation
            for argument in arguments:
                if isinstance(argument, str) and argument.startswith("{"):
                    continue     # Groovy's closure passed as an argument
                self._argument(offset, argument, declared_scope, is_dev)

    def _argument(self, offset, argument, declared_scope, is_dev):
        if isinstance(argument, list):
            found = _map_notation(argument, self.ctx)
            kind = ("rows", [found]) if found else ("unreadable",)
            argument = ", ".join(argument)
        else:
            kind = _notation(argument, self.ctx)
        if kind[0] == "first-party":
            return
        if kind[0] == "not-a-package":
            if kind[1] in ("files", "fileTree"):
                # A jar checked into the repository: no coordinates to look up (#398).
                logger.info("%s:%d: %s is a file dependency, not a package; no row",
                            self.path, self._line(offset), argument[:200])
            return
        if kind[0] == "unreadable":
            self._unreadable(offset, argument)
            return
        if kind[0] == "platform":
            for name, version in kind[2]:
                record = _record(name, version)
                record["requirements_type"] = "bom"
                # A platform supplies versions and puts nothing on the
                # classpath; is_dev stays None, as for a pom's imported BOM.
                record["declared_scope"] = kind[1]
                self._add(record)
            return
        for name, version in kind[1]:
            record = _record(name, version)
            record["requirements_type"] = "direct"
            record["declared_scope"] = declared_scope
            record["is_dev"] = is_dev
            self._add(record)

    def plugins(self, start, end):
        """A top-level plugins { }: a row for each plugin with a version."""
        for offset, stmt in _statements(self.text, start, end):
            stmt = re.sub(r"\s+apply\s*\(?\s*(?:false|true)\s*\)?\s*$", "", stmt)
            m = re.match(r"^alias\s*\(\s*(\w+)\.plugins\.([\w.]+?)(?:\.get\(\))?\s*\)$", stmt)
            if m:
                catalog = self.ctx.catalogs.get(m.group(1))
                found = catalog["plugins"].get(m.group(2)) if catalog else None
                if found is None:
                    logger.info("%s:%d: plugin alias kospex cannot resolve: %s", self.path,
                                self._line(offset), stmt[:200])
                    continue
                self._plugin_row(*found)
                continue
            m = re.match(r"""^(?:id\s*\(?\s*["']([\w.\-]+)["']\s*\)?|kotlin\s*\(\s*["']([\w.\-]+)["']\s*\))"""
                         r"""\s*(?:\.\s*version\s*\(\s*(.+?)\s*\)|version\s*\(?\s*(.+?)\s*\)?)?$""", stmt)
            if not m:
                continue        # core plugins (`java`, `kotlin-dsl`) and the rest
            plugin_id = m.group(1) or f"org.jetbrains.kotlin.{m.group(2)}"
            expression = m.group(3) or m.group(4)
            if not expression:
                continue        # no version: core, or set by settings / classpath
            version = self.ctx.value(expression)
            if version is None:
                logger.info("%s:%d: plugin %s has a version kospex cannot resolve: %s",
                            self.path, self._line(offset), plugin_id, expression[:100])
                continue
            self._plugin_row(plugin_id, version)

    def _plugin_row(self, plugin_id, version):
        record = _record(f"{plugin_id}:{plugin_id}.gradle.plugin", _clean_version(version))
        record["requirements_type"] = "direct"
        # Build tooling, not the product, as for a catalog's [plugins].
        record["declared_scope"] = "plugin"
        record["is_dev"] = True
        self._add(record)


def extract_build_gradle(path):
    """Parse a build.gradle or build.gradle.kts into dependency records.

    Returns template-shaped dicts with package_name (``groupId:artifactId``),
    package_version, requirements_type (``direct``, or ``bom`` for a
    ``platform(...)``), ecosystem (``gradle``), declared_scope and is_dev.
    ``project(...)`` and ``files(...)`` produce no row. Empty list for a
    build file with no package declarations.

    Versions come from the literal, from ``gradle.properties`` and simple
    assignments for ``$var``, or from the build's version catalog for
    ``libs.x``. Nothing is evaluated and nothing is fetched.

    Raises:
        UnreadableManifest: the file is missing, unreadable or not UTF-8, or
            it declares dependencies and kospex could read none of them
            (decision 3 of the design doc), so it is reported as
            ``parse_error`` rather than ``empty``.
    """
    text = _strip_comments(_read_text(path))
    ctx = _Context(path)
    ctx.add_assignments(text)
    reader = _BuildReader(path, text, ctx)
    reader.walk(0, len(text), [])
    if reader.unreadable and not reader.records:
        raise UnreadableManifest(
            f"{path}: declares {reader.unreadable} dependencies kospex cannot read, "
            "and none it can")
    return reader.records
