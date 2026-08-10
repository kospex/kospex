"""Guards the panopticas tag contract kospex actually depends on.

`pyproject.toml` declares `panopticas>=0.0.18` — a floor, not an exact pin, so
a kospex install can pick up any newer panopticas without a kospex release.
That is deliberate (it decouples the release cadences and avoids resolution
clashes), but it removes the version guard that an exact pin provided.

The risk it removes protection from is specific and **silent**: kospex caches
panopticas tags as `file_metadata.tech_type` and queries them with
`tech_type LIKE '%|tag|%'`. If panopticas stops emitting a tag kospex matches
on, those queries return *empty* rather than raising — dependency inventories
quietly report nothing instead of failing.

Panopticas' own test suite cannot catch this: it has no idea which of its tags
kospex depends on. So the assertion belongs here.

Scope: as of panopticas 0.0.18, kospex matches on exactly **one** tag,
`dependencies`, in two places in `kospex_query.py`. Keep this file in step with
that — if kospex starts querying another tag literal, add it to REQUIRED_TAGS.
"""
import panopticas.core as panopticas_core
import pytest

# The only panopticas tag literals kospex matches on. Sourced by grepping for
# `%|...|%` across the kospex modules; extend when a new literal is introduced.
REQUIRED_TAGS = ["dependencies"]

# Files that must carry each required tag for kospex's dependency inventory to
# work. These are the manifests kospex's own extractor registry treats as
# package files, so a regression here breaks `get_dependency_files()`.
DEPENDENCY_MANIFESTS = [
    "pyproject.toml",
    "requirements.txt",
    "package.json",
    "go.mod",
    "pom.xml",
    "setup.py",   # added in panopticas 0.0.18
    "setup.cfg",  # added in panopticas 0.0.18
]


@pytest.mark.parametrize("filename", DEPENDENCY_MANIFESTS)
def test_dependency_manifests_still_carry_the_dependencies_tag(filename):
    """Each known manifest must still be tagged `dependencies`.

    Fails if a panopticas upgrade renames or drops the tag, which would
    otherwise make `tech_type LIKE '%|dependencies|%'` silently return nothing.
    """
    tags = panopticas_core.get_filename_metatypes(filename)
    assert "dependencies" in tags, (
        f"panopticas no longer tags {filename!r} as 'dependencies' "
        f"(got {tags!r}). kospex queries tech_type LIKE '%|dependencies|%', "
        f"which will now return empty rather than error."
    )


@pytest.mark.parametrize("tag", REQUIRED_TAGS)
def test_required_tag_is_still_emitted_somewhere(tag):
    """Sanity check that the tag vocabulary still contains what kospex queries."""
    emitted = set()
    for filename in DEPENDENCY_MANIFESTS:
        emitted.update(panopticas_core.get_filename_metatypes(filename))
    assert tag in emitted, (
        f"panopticas emits no {tag!r} tag for any known dependency manifest; "
        f"kospex's tech_type queries for it will return empty."
    )
