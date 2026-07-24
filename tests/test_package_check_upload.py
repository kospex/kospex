"""Regression test for POST /package-check/upload.

Since the resolution-status work, an unresolved dependency carries an
explicit `versions_behind: None` (plus a `resolution` failure category)
instead of simply omitting the `versions_behind` key. The status-classification
loop in kweb2.package_check_upload used `item.get("versions_behind", 0)`,
which only falls back to 0 when the key is *absent* - with the key present
and set to None, `None > 6` raised TypeError, which the endpoint's blanket
`except Exception` turned into an HTTP 500. Any uploaded manifest containing
a single unresolved dependency (git/URL dep, `workspace:*`, `*`, `latest`,
a typo'd/private package, or anything deps.dev can't resolve) 500'd the
entire response.

The fix coalesces versions_behind/advisories to 0 before comparing, and
additionally surfaces an honest status label for unresolved items instead of
letting them silently read as "Current". Both the pure classification logic
and the full endpoint round-trip are exercised here.
"""
import kweb2
from kweb2 import UNRESOLVED_STATUS_LABELS, _classify_upload_status


# ---------------------------------------------------------------------------
# Unit tests against the pure helper (no server, no DB)
# ---------------------------------------------------------------------------

def test_unresolved_item_gets_honest_label_not_current():
    item = {
        "package_name": "left-pad",
        "package_version": "^1.0",
        "package_type": "npm",
        "versions_behind": None,
        "resolution": "unresolved_spec",
    }
    status = _classify_upload_status(item)  # must not raise
    assert status == UNRESOLVED_STATUS_LABELS["unresolved_spec"]
    assert status != "Current"


def test_all_failure_resolutions_produce_their_label_and_do_not_crash():
    for resolution, label in UNRESOLVED_STATUS_LABELS.items():
        item = {
            "package_name": "x",
            "package_version": "?",
            "package_type": "npm",
            "versions_behind": None,
            "resolution": resolution,
        }
        assert _classify_upload_status(item) == label


def test_advisories_win_over_unresolved_label():
    item = {
        "package_name": "vulnerable-and-unresolved",
        "package_version": "?",
        "package_type": "npm",
        "versions_behind": None,
        "resolution": "package_not_found",
        "advisories": 2,
    }
    assert _classify_upload_status(item) == "Vulnerable"


def test_resolved_behind_item_keeps_numeric_tier():
    outdated = {"package_name": "a", "versions_behind": 10, "resolution": "resolved"}
    behind = {"package_name": "b", "versions_behind": 3, "resolution": "resolved"}
    current = {"package_name": "c", "versions_behind": 0, "resolution": "resolved"}
    assert _classify_upload_status(outdated) == "Outdated"
    assert _classify_upload_status(behind) == "Behind"
    assert _classify_upload_status(current) == "Current"


def test_legacy_row_without_resolution_key_is_unchanged():
    # Pre-this-branch rows never had a `resolution` key at all.
    legacy_outdated = {"package_name": "a", "versions_behind": 10}
    legacy_current = {"package_name": "b", "versions_behind": 0}
    assert _classify_upload_status(legacy_outdated) == "Outdated"
    assert _classify_upload_status(legacy_current) == "Current"


def test_legacy_row_missing_versions_behind_key_entirely():
    # Belt-and-braces: the very old failure-path dict had no key at all.
    assert _classify_upload_status({"package_name": "a"}) == "Current"


# ---------------------------------------------------------------------------
# Full endpoint round-trip via FastAPI TestClient (pattern already used in
# tests/test_repo_org_header.py and tests/test_kweb_help.py)
# ---------------------------------------------------------------------------

import pytest  # noqa: E402


@pytest.fixture(scope="module")
def client():
    pytest.importorskip("httpx", reason="httpx required for FastAPI TestClient")
    from fastapi.testclient import TestClient

    return TestClient(kweb2.app)


def test_upload_endpoint_does_not_500_on_unresolved_dependency(client, monkeypatch):
    fake_results = [
        {
            "package_name": "some-git-dep",
            "package_version": "workspace:*",
            "package_type": "npm",
            "versions_behind": None,
            "resolution": "unresolved_spec",
        },
        {
            "package_name": "requests",
            "package_version": "2.0.0",
            "package_type": "pypi",
            "versions_behind": 1,
            "resolution": "resolved",
            "advisories": 0,
        },
    ]

    def fake_assess(self, filename, **kwargs):
        return fake_results

    monkeypatch.setattr(
        "kospex_dependencies.KospexDependencies.assess", fake_assess
    )

    resp = client.post(
        "/package-check/upload",
        files={"file": ("package.json", b'{"dependencies": {}}', "application/json")},
    )

    assert resp.status_code == 200  # pre-fix this was a 500 (TypeError: '>' not supported)
    body = resp.json()
    by_name = {row["package_name"]: row for row in body}

    assert by_name["some-git-dep"]["status"] == "Unresolved spec"
    assert by_name["some-git-dep"]["status"] != "Current"
    assert by_name["requests"]["status"] == "Current"


# ---------------------------------------------------------------------------
# Security regression: upload filename path traversal (deploy-kospex#8 item #10
# is the clone half; this is item #2 — the unauthenticated upload route joined
# tempfile.mkdtemp() with the raw client filename, so an absolute or ".." name
# wrote (then deleted) outside the temp dir. CWE-22.
# ---------------------------------------------------------------------------
import os.path  # noqa: E402
import tempfile as _tempfile  # noqa: E402
from pathlib import Path  # noqa: E402


def test_upload_filename_with_traversal_stays_inside_temp_dir(client, monkeypatch):
    """A "../" in the client filename must not let the write escape the temp dir."""
    created = {}
    real_mkdtemp = _tempfile.mkdtemp

    def recording_mkdtemp(*a, **k):
        d = real_mkdtemp(*a, **k)
        created["dir"] = d
        return d

    monkeypatch.setattr(_tempfile, "mkdtemp", recording_mkdtemp)

    captured = {}

    def fake_assess(self, filename, **kwargs):
        captured["path"] = filename
        return []

    monkeypatch.setattr("kospex_dependencies.KospexDependencies.assess", fake_assess)

    resp = client.post(
        "/package-check/upload",
        files={"file": ("../pwned.json", b"{}", "application/json")},
    )

    assert resp.status_code == 200
    assert "path" in captured, "assess was not reached"
    written = Path(captured["path"]).resolve()
    tempdir = Path(created["dir"]).resolve()
    assert written.is_relative_to(tempdir), (
        f"upload escaped temp dir: {written} is not under {tempdir}")
    assert written.name == "pwned.json", "the basename must be preserved for parser dispatch"


def test_upload_rejects_dot_dot_filename(client, monkeypatch):
    """A filename of "..' must be rejected outright, not written to the parent dir."""
    def exploding_assess(self, filename, **kwargs):
        raise AssertionError("assess must not be reached for a rejected filename")

    monkeypatch.setattr("kospex_dependencies.KospexDependencies.assess", exploding_assess)

    resp = client.post(
        "/package-check/upload",
        files={"file": ("..", b"{}", "application/json")},
    )

    assert resp.status_code == 400
    # Pin the rejection to the filename sanitiser, not any incidental 400.
    assert resp.json()["detail"] == "Invalid filename"
