"""Extractor for go.mod module files.

Returns one record per required module, shaped to
KospexDependencies.get_package_template() so ``krunner osi`` consumes the
records unchanged.

Pure: no DB, no CLI, no deps.dev enrichment. Both scan paths enrich centrally
after extraction, which is what lets them share this and stay in parity — see
changes/2026-07-17-extractor-registry-classifier-design.md (sub-project C).

go.mod declares requirements in two equally valid forms — grouped in a
``require ( ... )`` block, or one per line as ``require <module> <version>``.
Both are read. ``// indirect`` modules are recorded with
``requirements_type="indirect"``, which the scanners map to
PACKAGE_USE_TRANSITIVE; dropping them meant transitive Go dependencies never
reached the database at all.

``exclude`` / ``replace`` / ``retract`` directives are deliberately not read —
they do not declare a dependency. A missing, unreadable file, or one with no
``module`` directive, raises UnreadableManifest rather than returning [] — see
that class. [] is reserved for a valid go.mod that requires nothing, which is
the normal shape of a small Go library: 42 of 188 go.mod files on a
development estate declare no ``require`` at all, the smallest of them 27
bytes.
"""

from kospex.extractors.errors import UnreadableManifest
from kospex_utils import get_kospex_logger

logger = get_kospex_logger("extractors.gomod")


def _template():
    """Local copy of KospexDependencies.get_package_template()'s shape.

    Duplicated (not imported) to keep this extractor pure — it must not depend
    on the KospexDependencies / DB stack. A contract test
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
        # Install state (#227). None means this format cannot say: neither
        # go.mod nor a .csproj records dev/optional/install-script state the way
        # a lockfile does, and NULL is how "we did not look" is distinguished
        # from "we looked and it is not". Present so every extractor emits the
        # same keys -- write_dict_to_csv takes its header from row 0 alone.
        "is_dev": None,
        "is_optional": None,
        "runs_install_script": None,
        "declared_scope": None,
    }


def parse_gomod(path):
    """Parse a go.mod file into ``{module, version, indirect}`` dicts.

    The single implementation of go.mod parsing.
    ``KospexDependencies.parse_go_mod_from_file()`` delegates here rather than
    keeping its own copy — two parsers agreeing today is not the same as two
    parsers staying in agreement. That method is public, so the raising
    contract has to hold here rather than only in ``extract_gomod``.

    Raises:
        UnreadableManifest: the file is missing, unreadable, or declares no
            ``module``.
    """
    results = []

    try:
        with open(path, "r") as handle:
            lines = handle.readlines()
    except FileNotFoundError as exc:
        raise UnreadableManifest(f"{path}: file not found") from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise UnreadableManifest(f"{path}: could not read: {exc}") from exc

    # A BOM is checked BEFORE the `module` gate below, and is not stripped.
    #
    # Go refuses a BOM-prefixed go.mod too: `golang.org/x/mod/modfile` has no
    # BOM handling anywhere, and its `ModulePath` tests
    # `bytes.HasPrefix(line, moduleStr)` — defeated by a leading BOM in exactly
    # the way the gate below was. So the file genuinely does not build and
    # `parse_error` is right. Unlike package.json, where npm reads a BOM and so
    # must we.
    #
    # The check exists to say WHY. Without it the gate below reports "no
    # `module` directive" about a file whose first directive is `module`, which
    # sends the reader looking for a defect that is not there.
    if lines and lines[0].startswith("\ufeff"):
        raise UnreadableManifest(
            f"{path}: starts with a UTF-8 BOM, which go.mod does not permit — "
            f"the `go` tool cannot read this file either")

    # `module` is required by the go.mod grammar, so a file lacking it is not a
    # go.mod. Without this check go.mod is the only format here with no
    # structural gate at all — every other line it does not understand is
    # skipped — so arbitrary text under the name would read as a module that
    # requires nothing. All 188 go.mod files on a development estate declare
    # it, so nothing real is reclassified by requiring it. The block form
    # `module (` satisfies the same prefix.
    if not any(line.strip().startswith("module ") for line in lines):
        raise UnreadableManifest(f"{path}: no `module` directive, so not a go.mod")

    in_require_block = False

    for line in lines:
        trimmed = line.strip()

        if trimmed == "require (":
            in_require_block = True
            continue

        if trimmed == ")" and in_require_block:
            in_require_block = False
            continue

        # Only `require` lines are read. Matching the keyword rather than
        # parsing every non-block line is what keeps exclude / replace /
        # retract out.
        if in_require_block:
            parts = trimmed.split()
        elif trimmed.startswith("require "):
            parts = trimmed.split()[1:]
        else:
            continue

        # A bare comment inside the block splits into two or more parts and
        # would otherwise be recorded as a module named "//".
        if not parts or parts[0].startswith("//"):
            continue

        if len(parts) < 2:
            continue

        results.append({
            "module": parts[0],
            "version": parts[1],
            "indirect": "indirect" in parts,
        })

    return results


def extract_gomod(path):
    """Parse a go.mod file into dependency records.

    Returns a list of template-shaped dicts with package_name, package_version
    and requirements_type ("direct" or "indirect") populated. Empty list for a
    valid go.mod that requires nothing.

    Raises:
        UnreadableManifest: propagated from ``parse_gomod`` — the file is
            missing, unreadable, or is not a go.mod.
    """
    records = []

    for item in parse_gomod(path):
        record = _template()
        record["package_name"] = item["module"]
        record["package_version"] = item["version"]
        record["requirements_type"] = "indirect" if item["indirect"] else "direct"
        records.append(record)

    return records
