"""Tests for the version-aware file_metadata rebuild guard.

needs_metadata_rebuild() decides whether file_metadata must be rebuilt for a
repo, given what was recorded at the last successful sync (repos provenance
columns) vs the current HEAD + panopticas/scc versions. Rebuild when the HEAD
moved OR a tool version changed OR nothing was recorded yet OR force.
"""
from kospex_core import needs_metadata_rebuild

_CURRENT = {"hash": "h2", "panopticas_version": "0.0.16", "scc_version": "3.7.0"}


def test_force_always_rebuilds():
    recorded = dict(_CURRENT)
    needed, reason = needs_metadata_rebuild(recorded, _CURRENT, force=True)
    assert needed is True
    assert "force" in reason.lower()


def test_up_to_date_skips():
    recorded = dict(_CURRENT)
    needed, reason = needs_metadata_rebuild(recorded, _CURRENT)
    assert needed is False


def test_never_recorded_rebuilds():
    recorded = {"hash": None, "panopticas_version": None, "scc_version": None}
    needed, reason = needs_metadata_rebuild(recorded, _CURRENT)
    assert needed is True


def test_head_moved_rebuilds():
    recorded = {**_CURRENT, "hash": "h1"}
    needed, reason = needs_metadata_rebuild(recorded, _CURRENT)
    assert needed is True
    assert "hash" in reason.lower() or "head" in reason.lower()


def test_panopticas_bump_rebuilds_and_names_versions():
    recorded = {**_CURRENT, "panopticas_version": "0.0.15"}
    needed, reason = needs_metadata_rebuild(recorded, _CURRENT)
    assert needed is True
    assert "panopticas" in reason.lower()
    assert "0.0.15" in reason and "0.0.16" in reason  # old -> new for the audit log


def test_scc_bump_rebuilds():
    recorded = {**_CURRENT, "scc_version": "3.6.0"}
    needed, reason = needs_metadata_rebuild(recorded, _CURRENT)
    assert needed is True
    assert "scc" in reason.lower()


def test_versions_compared_as_opaque_strings():
    # Pre-release / suffixed / non-numeric versions are handled by string
    # equality — no semver parsing. A change to/from a suffix rebuilds...
    cur = {"hash": "h", "panopticas_version": "0.0.16-alpha", "scc_version": "3.7.0"}
    rec = {"hash": "h", "panopticas_version": "0.0.16", "scc_version": "3.7.0"}
    needed, reason = needs_metadata_rebuild(rec, cur)
    assert needed is True
    assert "0.0.16-alpha" in reason

    # ...and identical suffixed strings are treated as unchanged.
    needed2, _ = needs_metadata_rebuild(dict(cur), cur)
    assert needed2 is False
