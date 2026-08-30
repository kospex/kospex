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
they do not declare a dependency. On a missing or unreadable file we log a
warning and return [] (extractors package convention).
"""

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
    }


def parse_gomod(path):
    """Parse a go.mod file into ``{module, version, indirect}`` dicts.

    The single implementation of go.mod parsing.
    ``KospexDependencies.parse_go_mod_from_file()`` delegates here rather than
    keeping its own copy — two parsers agreeing today is not the same as two
    parsers staying in agreement.
    """
    results = []

    try:
        with open(path, "r") as handle:
            lines = handle.readlines()
    except FileNotFoundError:
        logger.warning("go.mod not found: %s", path)
        return []
    except Exception as exc:  # unreadable, encoding, permissions
        logger.warning("Could not read go.mod %s: %s", path, exc)
        return []

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
    and requirements_type ("direct" or "indirect") populated. Empty list on a
    missing, unreadable or dependency-free file.
    """
    records = []

    for item in parse_gomod(path):
        record = _template()
        record["package_name"] = item["module"]
        record["package_version"] = item["version"]
        record["requirements_type"] = "indirect" if item["indirect"] else "direct"
        records.append(record)

    return records
