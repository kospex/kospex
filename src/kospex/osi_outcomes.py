"""What happened to a dependency file during extraction — the #148 vocabulary.

Design: changes/202610-krunner-osi-next.md

A manifest producing zero dependency rows currently means several different
things, and nothing records which. The sharpest pair is `empty` (the file
genuinely declares nothing, which is fine) against `parse_error` (the parser
raised and the exception was swallowed, which is a defect). They look identical in
the database today and want opposite responses.

`dependency_data.resolution` already does this for the *resolve* layer — six
categories that make an unresolvable dependency reportable rather than invisible.
This is the same model one layer earlier, for the parse-file layer.

Not covered here, deliberately:

* **Discovery** — a manifest panopticas never tagged `|dependencies|` is invisible
  to osi by construction, since `get_dependency_files()` only returns tagged
  files. Instrumenting that belongs with panopticas.
* **Parse-line** — individual declarations dropped inside a parser (`-e .`,
  `git+…`, `-c …`) need recording at parser level, not per file. That is #145.
"""
from kospex.extractors.registry import Kind, classify

# The vocabulary. Values are stored in the observations record, so they are part
# of the data contract -- changing a string is a migration of meaning, not a
# rename.
EXTRACTED = "extracted"            # parser ran, produced rows
EMPTY = "empty"                    # parser ran, produced nothing -- genuinely declares none
UNSUPPORTED = "unsupported"        # real manifest, no parser yet (sub-project D)
UNCLASSIFIED = "unclassified"      # matched no registry entry -- investigate
NOT_A_MANIFEST = "not_a_manifest"  # runtime pin, container, scan config, lockfile
PARSE_ERROR = "parse_error"        # parser raised -- the one that is actually broken

# Priority for a repository's single headline outcome: **actionable first, then
# success, then absence of data.**
#
# parse_error leads because it is the only outcome meaning something is broken.
# unclassified outranks unsupported -- "we cannot even name this file" is a bigger
# gap than "we named it and have not written the parser yet".
#
# extracted then sits ABOVE empty and not_a_manifest, which is the part that is
# easy to get wrong. An earlier ordering had every non-success outcome outrank
# extracted, on the reasoning "worst first so nothing is masked". A real run showed
# why that is wrong: agronholm/anyio has one real manifest and one non-manifest
# file, extracted 4 packages, and was reported `not_a_manifest` -- as though
# nothing had been found. not_a_manifest and empty are benign *absences*, not
# problems, so they must not outrank a successful extraction in the same
# repository. A repo reports `empty` or `not_a_manifest` only when that is all
# there was.
OUTCOME_PRIORITY = (
    PARSE_ERROR,
    UNCLASSIFIED,
    UNSUPPORTED,
    EXTRACTED,
    EMPTY,
    NOT_A_MANIFEST,
)

# Kinds that declare no library dependencies of their own. Tagged
# `|dependencies|` by panopticas, which is right -- they are dependency-adjacent --
# but producing no rows from them is correct rather than a coverage gap. #148
# flagged these being counted as gaps: 41 dependabot files and 11 runtime pins on
# the estate it measured.
_NON_MANIFEST_KINDS = frozenset({
    Kind.RUNTIME, Kind.CONTAINER, Kind.SCA_CONFIG, Kind.LOCKFILE,
})


def classify_outcome(filename, row_count=0, error=None):
    """What happened to `filename`, as one value from the vocabulary above.

    `error` is the exception the parser raised, if any. It wins over everything
    else: an error must never be reported as a benign outcome, even when some rows
    were produced before it, because the extraction is then incomplete and the
    row count is not trustworthy.
    """
    if error is not None:
        return PARSE_ERROR

    classification = classify(filename)

    if classification.kind in _NON_MANIFEST_KINDS:
        return NOT_A_MANIFEST

    if classification.kind == Kind.UNKNOWN or classification.extractor is None:
        return UNCLASSIFIED

    if not classification.supported:
        return UNSUPPORTED

    return EXTRACTED if row_count else EMPTY


def repo_outcome(outcomes):
    """One headline value for a repository, by OUTCOME_PRIORITY.

    `outcomes` maps file_path -> outcome. An empty mapping rolls up to `empty`:
    a repository with no dependency files was examined and genuinely had nothing
    to find, which is the fact that stops it being retried forever.

    Not "worst" -- a successful extraction outranks the benign absences, so a
    repository that got data says so even when it also contains a Dockerfile. See
    the note on OUTCOME_PRIORITY.
    """
    present = set(outcomes.values())
    for outcome in OUTCOME_PRIORITY:
        if outcome in present:
            return outcome
    return EMPTY
