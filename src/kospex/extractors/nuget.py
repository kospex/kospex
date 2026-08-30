"""Extractor for NuGet .csproj project files.

Returns one record per ``<PackageReference>``, shaped to
KospexDependencies.get_package_template() so ``krunner osi`` consumes the
records unchanged.

Pure: no DB, no CLI, no deps.dev enrichment. Both scan paths enrich centrally
after extraction, which is what lets them share this and stay in parity — see
changes/2026-07-17-extractor-registry-classifier-design.md (sub-project C).

This parsing previously lived inside ``nuget_assess()``, which built the records,
printed them as a table and then fell off the end without returning them — so
NuGet dependencies were never persisted by any path (#107).

A ``PackageReference`` without a ``Version`` attribute is still a declared
dependency (Central Package Management moves the version to
Directory.Packages.props), so it is recorded with an empty version rather than
dropped. On a missing or malformed file we log a warning and return []
(extractors package convention).
"""

import xml.etree.ElementTree as ET

from kospex_utils import get_kospex_logger

logger = get_kospex_logger("extractors.nuget")


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


def extract_csproj(path):
    """Parse a .csproj file into dependency records.

    Returns a list of template-shaped dicts with package_name, package_version
    and requirements_type ("direct") populated. Empty list on a missing,
    malformed or reference-free file.
    """
    try:
        with open(path, "r") as handle:
            xml_data = handle.read()

        # CWE-776: uncontrolled entity expansion. Manifests come from cloned
        # third-party repositories, so this input is attacker-influenced.
        # ElementTree refuses external entities but does expand internal ones —
        # four nested levels reach 10,000 characters, nine or ten reach
        # gigabytes and exhaust memory mid-scan. Internal entities must be
        # declared in a DTD, and no legitimate MSBuild project file carries a
        # DOCTYPE, so refusing it removes the class outright. Checked before
        # parsing: filtering afterwards would mean the expansion already ran.
        if "<!DOCTYPE" in xml_data:
            logger.warning(
                "Refusing .csproj with a DOCTYPE declaration (entity expansion): %s",
                path,
            )
            return []

        root = ET.fromstring(xml_data)
    except FileNotFoundError:
        logger.warning(".csproj not found: %s", path)
        return []
    except ET.ParseError as exc:
        logger.warning("Could not parse .csproj %s: %s", path, exc)
        return []
    except Exception as exc:  # unreadable, encoding, permissions
        logger.warning("Could not read .csproj %s: %s", path, exc)
        return []

    records = []

    for pkg in root.findall(".//PackageReference"):
        name = pkg.attrib.get("Include")
        if not name:
            # An Update= or Remove= reference adjusts an existing entry rather
            # than declaring one.
            continue

        record = _template()
        record["package_name"] = name
        # Absent under Central Package Management, where the version lives in
        # Directory.Packages.props. Still a declared dependency.
        record["package_version"] = pkg.attrib.get("Version", "")
        record["requirements_type"] = "direct"
        records.append(record)

    return records
