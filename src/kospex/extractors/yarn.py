"""Extractor for yarn.lock lockfiles.

Returns one record per distinct installed ``name@version``, shaped to
KospexDependencies.get_package_template() so ``krunner osi`` consumes the
records unchanged.

Two incompatible formats share the file name, and neither has a grammar to
parse against:

- v1 (Yarn Classic) -- a custom, indentation-based format, described on the
  Classic docs page but not specified. Identified by its ``# yarn lockfile v1``
  header comment.
- Berry (Yarn 2+) -- YAML, with a ``__metadata.version`` that moves between
  Yarn releases. No published format spec at all: the format is whatever Yarn
  writes.

So every gate here was measured against every real yarn.lock on a development
estate first (156 files; see docs/dependency-details.md), and a file outside
what was measured raises rather than being guessed at.

What neither format states, this parser does not claim. A lockfile is the
install set, not the declared set: v1 has no dev or optional flag at all, and
a Berry workspace entry's ``dependencies`` merges the project's dependencies
and devDependencies (measured: 385 devDependencies sat there). So every row is
``resolved`` and the install-state columns stay None, which means "the format
cannot say" -- not "no".

Identity follows the npm lockfile parser (npmlock._identities): every name and
version an entry asserts is recorded, because no field of a lockfile vouches
for another. Local packages (``link:``, ``file:``, ``workspace:``, ``portal:``)
have no registry identity and produce no record -- unless the entry shows it
was fetched or copied in like a package, so that one edit to a key cannot hide
a dependency.

Integrity hashes and checksums are dropped -- SCA does not consume them. A
missing, unreadable or uninterpretable file raises UnreadableManifest; [] is
reserved for a lockfile that was read and resolves nothing.
"""

import json
import re
from urllib.parse import unquote, urlparse

import yaml

from kospex.extractors.errors import UnreadableManifest
from kospex.extractors.npmlock import (
    _TARBALL_VERSION,
    _is_package_name,
    _lockfile_version,
    _looks_fetched,
    _name_from_resolved,
)
from kospex_utils import get_kospex_logger

logger = get_kospex_logger("extractors.yarn")

#: The comment yarn v1 writes at the top of every lockfile.
_V1_HEADER = "# yarn lockfile v1"

#: Berry `__metadata.version` values measured on real files. Berry publishes no
#: format spec, so an unmeasured version is refused, not guessed at.
_BERRY_VERSIONS = (6, 8, 10)

#: Descriptor protocols that name a local directory, not a registry package.
_LOCAL_PROTOCOLS = ("link:", "file:", "workspace:", "portal:")

#: The v1 grammar. Measured: every non-comment line of 111 real v1 files is one
#: of these four, so a line that is not is a file this parser has not understood.
_V1_FIELD = re.compile(r"^  ([A-Za-z]+) (.+)$")
_V1_SECTION = re.compile(r"^  ([A-Za-z]+):$")
_V1_SUBFIELD = re.compile(r'^    (?:"[^"]+"|\S+) \S.*$')
# One JSON-quoted descriptor. The two alternatives are disjoint, so a failed
# match is linear. Unquoted descriptors are split by _v1_descriptors() without
# a regex: `[^,"]+\s*(?:,|$)` overlaps on whitespace and backtracks
# quadratically on a long key line (CWE-1333).
_V1_QUOTED = re.compile(r'"(?:[^"\\]|\\.)*"')


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
        # Install state (#227). Neither yarn format states any of these, so
        # they stay None for every row -- see the module docstring.
        "is_dev": None,
        "is_optional": None,
        "runs_install_script": None,
        "declared_scope": None,
    }


def _split_descriptor(descriptor):
    """``name@range`` -> (name, range). The name may be scoped (``@scope/x``),
    so the separator is the first ``@`` after the first character."""
    at = descriptor.find("@", 1)
    if at < 0:
        return descriptor, ""
    return descriptor[:at], descriptor[at + 1:]


def _descriptor_names(descriptor):
    """Every package name one descriptor asserts: its own, and for an
    ``npm:`` alias (``foo@npm:bar@^1``) the target as well."""
    name, spec = _split_descriptor(descriptor)
    names = [name]
    if spec.startswith("npm:") and "@" in spec[len("npm:") + 1:]:
        target, _range = _split_descriptor(spec[len("npm:"):])
        names.append(target)
    return names


def _usable_names(candidates, where):
    """Valid npm package names from `candidates`, de-duplicated, in order.

    A value that cannot be an npm name becomes neither a primary key nor a CSV
    cell. Logged when the entry asserts more than one identity, as npmlock does.
    """
    names = []
    for candidate in candidates:
        if _is_package_name(candidate) and candidate not in names:
            names.append(candidate)
    if len(names) > 1:
        logger.warning(
            "%s asserts more than one package identity %r; recording each -- "
            "no field of a lockfile corroborates another", where, names)
    return names


def _records(found):
    """[(name, version)] -> one template record per distinct pair, in order."""
    records, seen = [], set()
    for name, version in found:
        if (name, version) in seen:
            continue
        seen.add((name, version))
        record = _template()
        record["package_name"] = name
        record["package_version"] = version
        record["ecosystem"] = "npm"
        # Not "direct": see the module docstring. "resolved" maps to the
        # transitive package_use, which is the only claim a lockfile supports.
        record["requirements_type"] = "resolved"
        records.append(record)
    return records


# --- v1 ---------------------------------------------------------------------

def _v1_unquote(path, lineno, value):
    """v1 quotes string values JSON-style; only `integrity` is ever bare."""
    if not value.startswith('"'):
        return value
    try:
        result = json.loads(value)
    except json.JSONDecodeError as e:
        raise UnreadableManifest(f"{path}: line {lineno}: bad quoting: {e}") from e
    if not isinstance(result, str):
        raise UnreadableManifest(f"{path}: line {lineno}: value is not a string")
    return result


def _v1_descriptors(path, lineno, key_line):
    """``"a@^1", a@^1.2:`` -> ["a@^1", "a@^1.2"]."""
    body = key_line[:-1]
    descriptors, pos, end = [], 0, len(body)
    while pos < end:
        while pos < end and body[pos].isspace():
            pos += 1
        if pos < end and body[pos] == '"':
            match = _V1_QUOTED.match(body, pos)
            if not match:
                raise UnreadableManifest(f"{path}: line {lineno}: unreadable entry key")
            descriptors.append(_v1_unquote(path, lineno, match.group()))
            pos = match.end()
            while pos < end and body[pos].isspace():
                pos += 1
        else:
            comma = body.find(",", pos)
            stop = end if comma == -1 else comma
            token = body[pos:stop]
            if not token or '"' in token:
                raise UnreadableManifest(f"{path}: line {lineno}: unreadable entry key")
            descriptors.append(token.strip())
            pos = stop
        if pos < end:
            if body[pos] != ",":
                raise UnreadableManifest(f"{path}: line {lineno}: unreadable entry key")
            pos += 1
    if not descriptors or not all(descriptors):
        raise UnreadableManifest(f"{path}: line {lineno}: unreadable entry key")
    return descriptors


def _v1_entries(path, text):
    """Parse v1 into [(descriptors, fields)], refusing any line outside the grammar."""
    entries, current, in_section = [], None, False
    for lineno, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.startswith("#"):
            continue
        if not line[0].isspace():
            if not line.endswith(":"):
                raise UnreadableManifest(f"{path}: line {lineno}: unreadable entry key")
            current = (_v1_descriptors(path, lineno, line), {})
            entries.append(current)
            in_section = False
        elif current is None:
            raise UnreadableManifest(f"{path}: line {lineno}: field before any entry")
        elif match := _V1_SECTION.match(line):
            in_section = True        # dependencies / optionalDependencies: ranges, not installs
        elif match := _V1_FIELD.match(line):
            current[1][match.group(1)] = _v1_unquote(path, lineno, match.group(2))
            in_section = False
        elif _V1_SUBFIELD.match(line) and in_section:
            continue
        else:
            raise UnreadableManifest(
                f"{path}: line {lineno}: not part of the yarn v1 format")
    return entries


def _v1_versions(fields):
    """The declared version, then the one in the tarball URL if it differs."""
    versions = []
    if fields.get("version"):
        versions.append(fields["version"])
    url = fields.get("resolved")
    if url:
        try:
            match = _TARBALL_VERSION.search(urlparse(url).path or "")
        except ValueError:
            match = None
        if match and match.group("version") not in versions:
            versions.append(match.group("version"))
    if len(versions) > 1:
        logger.warning(
            "entry claims more than one version %r; recording each -- the "
            "declared version and the tarball URL disagree", versions)
    return versions


def _extract_v1(path, text):
    found = []
    for descriptors, fields in _v1_entries(path, text):
        resolved = fields.get("resolved")
        local = all(_split_descriptor(d)[1].startswith(_LOCAL_PROTOCOLS)
                    for d in descriptors)
        # Measured: the 12 real local entries have no `resolved`. One that has
        # a URL was fetched, whatever its key says, so it is recorded.
        if local and not _looks_fetched({"resolved": resolved}):
            continue

        candidates = [n for d in descriptors for n in _descriptor_names(d)]
        candidates.append(_name_from_resolved(resolved))
        names = _usable_names(candidates, f"{path}: {descriptors[0]!r}")
        versions = _v1_versions(fields)
        if not names or not versions:
            # package_name and package_version are both in the primary key.
            logger.warning("%s: entry %r has no usable %s; it cannot be recorded",
                           path, descriptors[0], "name" if not names else "version")
            continue
        found.extend((name, version) for name in names for version in versions)
    return _records(found)


# --- Berry ------------------------------------------------------------------

def _berry_resolution(path, key, resolution):
    """A resolution -> (name, protocol, version-it-names-or-None, extra names).

    ``npm:`` names its version. ``patch:`` wraps a URL-encoded npm descriptor
    (``fsevents@patch:fsevents@npm%3A2.3.3#...``), which is unwrapped -- the
    package installed is still that npm package at that version.
    """
    name, ref = _split_descriptor(resolution)
    if not name or not ref:
        raise UnreadableManifest(f"{path}: {key!r}: unreadable resolution {resolution!r}")
    protocol = ref.split(":", 1)[0] + ":" if ":" in ref else ""

    if protocol == "npm:":
        return name, protocol, ref[len("npm:"):], []
    if protocol == "patch:":
        inner = unquote(ref[len("patch:"):].split("#", 1)[0])
        inner_name, inner_ref = _split_descriptor(inner)
        if inner_ref.startswith("npm:"):
            return name, protocol, inner_ref[len("npm:"):].split("::", 1)[0], [inner_name]
        return name, protocol, None, [inner_name]
    return name, protocol, None, []


def _extract_berry(path, doc):
    metadata = doc.get("__metadata")
    raw_version = metadata.get("version") if isinstance(metadata, dict) else None
    version = _lockfile_version(raw_version)
    if version not in _BERRY_VERSIONS:
        raise UnreadableManifest(
            f"{path}: unsupported yarn __metadata.version {raw_version!r} -- "
            f"refusing to report this file as empty, because its packages are unread")

    found = []
    for key, entry in doc.items():
        if key == "__metadata":
            continue
        if not isinstance(entry, dict):
            raise UnreadableManifest(f"{path}: entry {key!r} is not a mapping")
        resolution = entry.get("resolution")
        if not isinstance(resolution, str):
            # Measured: all 13,273 real entries carry one.
            raise UnreadableManifest(f"{path}: entry {key!r} has no resolution")

        name, protocol, res_version, extra = _berry_resolution(path, key, resolution)
        if protocol in _LOCAL_PROTOCOLS:
            # Measured: all 317 real local entries are `linkType: soft`. A hard
            # link was copied in like a package, so it is recorded.
            if entry.get("linkType") == "soft":
                continue
            logger.warning("%s: %r is local but hard-linked; recording it", path, key)
        elif protocol not in ("npm:", "patch:"):
            logger.warning("%s: %r resolves through %r, which no measured file "
                           "used; recording its declared version", path, key, protocol)

        versions = [res_version] if res_version else []
        # YAML reads `version: 1.10` as the float 1.1, so only a string is
        # trusted; the resolution string is exact either way.
        declared = entry.get("version")
        if isinstance(declared, str) and declared and declared not in versions:
            versions.append(declared)
        if len(versions) > 1:
            logger.warning("%s: %r claims more than one version %r; recording each",
                           path, key, versions)

        key_descriptors = [d.strip() for d in str(key).split(",")]
        candidates = [name, *extra] + [n for d in key_descriptors for n in _descriptor_names(d)]
        names = _usable_names(candidates, f"{path}: {key!r}")
        if not names or not versions:
            logger.warning("%s: entry %r has no usable %s; it cannot be recorded",
                           path, key, "name" if not names else "version")
            continue
        found.extend((n, v) for n in names for v in versions)
    return _records(found)


# --- entry point ------------------------------------------------------------

def _is_v1(text):
    """Is the v1 header among the comments yarn writes before the first entry?"""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == _V1_HEADER:
            return True
        if stripped and not stripped.startswith("#"):
            return False
    return False


def extract_yarn_lock(path):
    """Parse a yarn.lock (v1 or Berry); return get_package_template-shaped records.

    Args:
        path: Path to a yarn.lock on disk.

    Returns:
        list[dict]: one record per distinct installed ``name@version``, with
        ``ecosystem="npm"`` and ``requirements_type="resolved"``. [] only for a
        lockfile that was read and resolves nothing -- a v1 file with only its
        header, or a Berry file holding only ``__metadata`` and workspaces.

    Raises:
        UnreadableManifest: the file is missing, unreadable, carries neither
            format's header, has a line or entry outside the measured format,
            or declares a Berry ``__metadata.version`` that has not been
            measured. Returning [] in those cases would record the file as
            `empty` -- read, and declaring nothing.
    """
    try:
        # utf-8-sig consumes a BOM if present, and is plain utf-8 otherwise.
        with open(path, encoding="utf-8-sig") as f:
            text = f.read()
    except FileNotFoundError as e:
        raise UnreadableManifest(f"{path}: file not found") from e
    except (OSError, UnicodeDecodeError) as e:
        raise UnreadableManifest(f"{path}: could not read: {e}") from e

    if _is_v1(text):
        return _extract_v1(path, text)

    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise UnreadableManifest(f"{path}: invalid YAML: {e}") from e

    if not isinstance(doc, dict) or "__metadata" not in doc:
        # One real yarn.lock is a comment-only test fixture. yarn always writes
        # one header or the other, so nothing can be concluded from a file with
        # neither -- the pnpm parser makes the same call for a 0-byte lockfile.
        raise UnreadableManifest(
            f"{path}: neither a yarn v1 header nor Berry __metadata, so not a lockfile")

    return _extract_berry(path, doc)
