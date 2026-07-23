"""End-to-end clone test — performs a REAL HTTPS clone of a public repo.

Gated: skipped unless KOSPEX_RUN_NETWORK_TESTS is set AND git is on PATH, so
the default (offline / CI) suite never runs it. Run it deliberately with:

    KOSPEX_RUN_NETWORK_TESTS=1 .venv/bin/python -m pytest \
        tests/test_clone_repo_integration.py -v

It exists to cover the one thing the unit tests cannot: git actually accepting
the constructed argv and writing the repo to the computed destination. It is
the runnable form of the SSH acceptance check that could not run without a key.
"""
import os
import shutil

import pytest

from kospex.habitat_config import HabitatConfig
from kospex_git import KospexGit

pytestmark = pytest.mark.integration

_SKIP_REASON = "set KOSPEX_RUN_NETWORK_TESTS=1 and ensure git is on PATH to run"


@pytest.mark.skipif(
    not os.environ.get("KOSPEX_RUN_NETWORK_TESTS") or shutil.which("git") is None,
    reason=_SKIP_REASON,
)
def test_clone_repo_https_end_to_end(tmp_path):
    """A real HTTPS clone lands at the computed path and is a git repo."""
    config = HabitatConfig.get_instance()
    with config.with_overrides(KOSPEX_CODE=str(tmp_path)):
        result = KospexGit().clone_repo("https://github.com/kospex/panopticas.git")

    expected = tmp_path / "github.com" / "kospex" / "panopticas"
    assert result == str(expected)
    assert expected.is_dir()
    assert (expected / ".git").is_dir(), "clone did not produce a git working copy"
