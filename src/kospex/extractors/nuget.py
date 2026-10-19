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
dropped.

A missing, unreadable or uninterpretable file raises UnreadableManifest rather
than returning [] — see that class. "Uninterpretable" includes the DOCTYPE
refusal below and a root element that is not ``<Project>``. [] is reserved for
a project file that declares no package: 4 of 9 .csproj files on a development
estate are legitimately in that state.

``PackageReference`` is matched ignoring any XML namespace. SDK-style project
files — the only kind that uses ``PackageReference`` — omit ``xmlns``, and
none on the development estate carries one, but a namespace-qualified
document is legal and a namespace-blind ``findall`` would have found nothing
in it and reported the project as declaring nothing.
"""

import xml.etree.ElementTree as ET

from kospex.extractors.errors import UnreadableManifest
from kospex_utils import get_kospex_logger

logger = get_kospex_logger("extractors.nuget")


def _template():
    """Local copy of KospexDependencies.get_package_template()'s shape.

    Duplicated (not imported) to keep this extractor pure — it must not depend
    on the KospexDependencies / DB stack. A contract test
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


def _package_references(root):
    """Every <PackageReference> element, whatever namespace it carries.

    `root.findall(".//PackageReference")` matches only unqualified tags, so a
    project file declaring `xmlns` would yield nothing and be reported as
    declaring no packages. Comparing the local name instead gates on the
    element rather than on its spelling.
    """
    return [
        element for element in root.iter()
        if element.tag.rsplit("}", 1)[-1] == "PackageReference"
    ]


def extract_csproj(path):
    """Parse a .csproj file into dependency records.

    Returns a list of template-shaped dicts with package_name, package_version
    and requirements_type ("direct") populated. Empty list for a readable
    project file that declares no package.

    Raises:
        UnreadableManifest: the file is missing, unreadable, not well-formed
            XML, carries a DOCTYPE, or is not an MSBuild project.
    """
    try:
        with open(path, "r") as handle:
            xml_data = handle.read()
    except FileNotFoundError as exc:
        raise UnreadableManifest(f"{path}: file not found") from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise UnreadableManifest(f"{path}: could not read: {exc}") from exc

    # CWE-776: uncontrolled entity expansion. Manifests come from cloned
    # third-party repositories, so this input is attacker-influenced.
    # ElementTree refuses external entities but does expand internal ones —
    # four nested levels reach 10,000 characters, nine or ten reach
    # gigabytes and exhaust memory mid-scan. Internal entities must be
    # declared in a DTD, and no legitimate MSBuild project file carries a
    # DOCTYPE, so refusing it removes the class outright. Checked before
    # parsing: filtering afterwards would mean the expansion already ran —
    # which is also why this check sits outside the read's try/except, where
    # a broad `except` would have swallowed the refusal it raises.
    #
    # Raised rather than returned: a refusal is not a reading. Returning []
    # recorded the guard's own outcome as a project with no NuGet packages, so
    # a hostile file was refused correctly and then reported as a clean scan.
    if "<!DOCTYPE" in xml_data:
        raise UnreadableManifest(
            f"{path}: refusing a .csproj carrying a DOCTYPE declaration "
            f"(entity expansion)")

    try:
        root = ET.fromstring(xml_data)
    except ET.ParseError as exc:
        raise UnreadableManifest(f"{path}: invalid XML: {exc}") from exc

    # An MSBuild project file has a <Project> root. Well-formed XML with any
    # other root is not one, and the PackageReference search below would
    # simply find nothing in it — so any XML file under a .csproj name would
    # report as a project declaring no packages. All 9 .csproj files on a
    # development estate have a <Project> root.
    root_tag = root.tag.rsplit("}", 1)[-1]
    if root_tag != "Project":
        raise UnreadableManifest(
            f"{path}: root element is <{root_tag}>, not <Project>")

    records = []

    for pkg in _package_references(root):
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
