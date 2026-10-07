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

# Worst-first. A repository's roll-up takes the first of these present, so a
# problem is never masked by a benign sibling file.
#
# parse_error leads because it is the only outcome meaning something is broken.
# unclassified outranks unsupported: "we cannot even name this file" is a bigger
# gap than "we named it and have not written the parser". not_a_manifest and empty
# are both benign, and extracted is the ordinary case.
SEVERITY = (
    PARSE_ERROR,
    UNCLASSIFIED,
    UNSUPPORTED,
    NOT_A_MANIFEST,
    EMPTY,
    EXTRACTED,
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


def worst_outcome(outcomes):
    """One value summarising a repository, worst-first.

    `outcomes` maps file_path -> outcome. An empty mapping rolls up to `empty`:
    a repository with no dependency files was examined and genuinely had nothing
    to find, which is the fact that stops it being retried forever.
    """
    present = set(outcomes.values())
    for outcome in SEVERITY:
        if outcome in present:
            return outcome
    return EMPTY
