"""Extract one repository's dependencies, reporting what happened to each file.

Design: changes/202610-krunner-osi-next.md

`extract_repo()` is the per-repo unit `krunner osi -next` calls once per queued
repository. It is a function rather than inline in the command because the outcome
reporting has to be testable against the real parsers -- a test that
re-implements the loop proves nothing about the code that runs, which is why
`extract_dependency_file` and `enrich_dependency_records` were pulled out of `osi`
to begin with.

The behavioural difference from the loop inside `osi -all`: nothing is swallowed.
Today a parser that raises hits `except (json.JSONDecodeError, OSError): continue`
and the file vanishes from the record entirely, indistinguishable from one that
parsed to nothing. Here it becomes a `parse_error` outcome. That is #148.
"""
import os

from kospex.extractors.registry import classify
from kospex.osi_outcomes import PARSE_ERROR, classify_outcome


def extract_repo(repo_id, kospex_query, kdeps, echo=None):
    """Parse every dependency file in `repo_id`.

    Returns `(rows, outcomes)`:

    * `rows` -- dependency records ready for enrichment and `save_dependencies`,
      each already carrying its full primary key.
    * `outcomes` -- `file_path -> outcome`, one entry per dependency-tagged file.
      An empty mapping means the repository has no dependency files, which is a
      real result and not an error: the caller records it as "examined, nothing to
      find", and that is what stops the repository being queued forever.

    Enrichment is deliberately NOT done here. The caller enriches and saves the
    batch as one unit, because `save_dependencies` stamps `last_checked` on every
    row it writes and never calls deps.dev -- splitting them would advance the
    timestamp over rows whose advisory data was never refreshed.
    """
    # Local import: krunner imports this module, and extract_dependency_file lives
    # in krunner. Importing at module level would cycle.
    from krunner import extract_dependency_file

    repo = kospex_query.get_repo_by_id(repo_id)
    if not repo:
        raise ValueError(f"repo not in the kospex database: {repo_id}")

    files = kospex_query.get_dependency_files(request_id={"repo_id": repo_id})

    rows = []
    outcomes = {}

    for f in files:
        provider = f["Provider"]
        classification = classify(provider)

        # Nothing to parse: unsupported, unclassified or not a manifest at all.
        # classify_outcome tells those three apart; the command reports them and
        # #148 counts them.
        if classification.extractor is None or not classification.supported:
            outcomes[provider] = classify_outcome(provider)
            continue

        full_path = os.path.join(repo["file_path"], provider)
        try:
            parsed = extract_dependency_file(
                classification.extractor, full_path, repo_id,
                provider, f.get("hash"), kdeps,
            )
        except Exception as e:                      # noqa: BLE001 -- see below
            # Deliberately broad. The point of the outcome record is that a file
            # kospex could not read is *reported*, and narrowing this to
            # (JSONDecodeError, OSError) is what makes a tomllib or yaml error
            # crash the whole batch instead of marking one file. The exception is
            # not discarded -- it is surfaced through `echo` and the outcome.
            outcomes[provider] = PARSE_ERROR
            if echo:
                echo(f"  {provider}: {type(e).__name__}: {e}")
            continue

        outcomes[provider] = classify_outcome(provider, row_count=len(parsed))
        rows.extend(parsed)

    return rows, outcomes
