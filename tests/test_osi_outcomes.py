"""Classifying what happened to each dependency file — the #148 vocabulary.

Design: changes/202610-krunner-osi-next.md

Today a manifest producing zero dependency rows means any of several different
things and nothing records which. The two that matter most are `empty` (the file
genuinely declares nothing — fine) and `parse_error` (the parser raised and the
exception was swallowed — a real defect). They are indistinguishable in the
database and want opposite responses.

`dependency_data.resolution` already works this way for the *resolve* layer, with
six categories that make an unresolvable dependency reportable rather than
invisible. This is the same idea one layer earlier.
"""
import pytest

from kospex.osi_outcomes import (
    EMPTY,
    EXTRACTED,
    NOT_A_MANIFEST,
    PARSE_ERROR,
    UNCLASSIFIED,
    UNSUPPORTED,
    classify_outcome,
    repo_outcome,
)


# --- per-file classification -------------------------------------------------

@pytest.mark.parametrize("filename", [
    "requirements.txt", "pyproject.toml", "package.json", "go.mod", "app.csproj",
])
def test_a_parsed_file_with_rows_is_extracted(filename):
    assert classify_outcome(filename, row_count=3) == EXTRACTED


@pytest.mark.parametrize("filename", ["pyproject.toml", "package.json"])
def test_a_parsed_file_with_no_rows_is_empty(filename):
    """Textualize/rich's pyproject.toml has no [project] dependencies table.

    Genuinely declares nothing. Not a problem, and must be distinguishable from
    one that blew up.
    """
    assert classify_outcome(filename, row_count=0) == EMPTY


@pytest.mark.parametrize("filename", [
    "yarn.lock", "uv.lock", "package-lock.json", "build.gradle", "build.gradle.kts",
])
def test_a_recognised_manifest_with_no_parser_is_unsupported(filename):
    """Real manifests kospex can name but cannot yet read — sub-project D."""
    assert classify_outcome(filename, row_count=0) == UNSUPPORTED


@pytest.mark.parametrize("filename", [
    "dependabot.yml", "renovate.json", "Dockerfile", ".python-version", ".nvmrc",
    "go.sum",
])
def test_a_non_manifest_is_not_a_manifest(filename):
    """Tagged `|dependencies|` but declares no library deps of its own.

    dependabot.yml is scan config, .python-version is a toolchain pin, go.sum is
    an integrity companion. #148 flagged these being lumped in with real coverage
    gaps — 41 dependabot files and 11 version pins on the estate it measured.
    """
    assert classify_outcome(filename, row_count=0) == NOT_A_MANIFEST


def test_a_file_matching_no_registry_entry_is_unclassified():
    """Either a real manifest kospex cannot name, or a mis-tag. Investigate."""
    assert classify_outcome("Gemfile.lock", row_count=0) == UNCLASSIFIED


def test_a_raising_parser_is_a_parse_error():
    """The one that is actually broken, and is silently swallowed today."""
    assert classify_outcome(
        "package.json", row_count=0, error=ValueError("bad json")) == PARSE_ERROR


def test_parse_error_wins_over_every_other_signal():
    """An error must never be reported as a benign outcome."""
    assert classify_outcome(
        "package.json", row_count=5, error=OSError("gone")) == PARSE_ERROR
    assert classify_outcome(
        "dependabot.yml", row_count=0, error=OSError("gone")) == PARSE_ERROR


# --- repo-level roll-up ------------------------------------------------------

def test_an_actionable_outcome_outranks_a_successful_one():
    """A problem must surface rather than be masked by a sibling that worked."""
    assert repo_outcome({"a": EXTRACTED, "b": UNSUPPORTED}) == UNSUPPORTED
    assert repo_outcome({"a": EXTRACTED, "b": PARSE_ERROR}) == PARSE_ERROR
    assert repo_outcome({"a": UNSUPPORTED, "b": UNCLASSIFIED}) == UNCLASSIFIED


def test_a_successful_extraction_outranks_a_benign_absence():
    """Regression: a real run reported a repo that extracted 4 packages as
    `not_a_manifest`, because it also contained one non-manifest file.

    agronholm/anyio: one real manifest plus one non-manifest, 4 packages
    extracted, headline outcome `not_a_manifest` -- as though nothing was found.
    not_a_manifest and empty are benign *absences*, not problems, so they must not
    outrank a successful extraction in the same repository.
    """
    assert repo_outcome({"a": EXTRACTED, "b": NOT_A_MANIFEST}) == EXTRACTED
    assert repo_outcome({"a": EXTRACTED, "b": EMPTY}) == EXTRACTED
    assert repo_outcome({"a": EXTRACTED, "b": EMPTY, "c": NOT_A_MANIFEST}) == EXTRACTED


def test_a_benign_absence_is_reported_when_it_is_all_there_was():
    """The flip side: nothing found must still say so."""
    assert repo_outcome({"a": EMPTY}) == EMPTY
    assert repo_outcome({"a": NOT_A_MANIFEST}) == NOT_A_MANIFEST
    assert repo_outcome({"a": EMPTY, "b": NOT_A_MANIFEST}) == EMPTY


def test_parse_error_outranks_everything():
    """It is the only outcome that means something is broken."""
    every = {str(i): o for i, o in enumerate(
        [EXTRACTED, EMPTY, NOT_A_MANIFEST, UNSUPPORTED, UNCLASSIFIED, PARSE_ERROR])}
    assert repo_outcome(every) == PARSE_ERROR


def test_a_repo_with_no_dependency_files_rolls_up_to_empty():
    """"Examined, found nothing" -- the record that stops it being retried."""
    assert repo_outcome({}) == EMPTY


def test_all_extracted_rolls_up_to_extracted():
    assert repo_outcome({"a": EXTRACTED, "b": EXTRACTED}) == EXTRACTED
