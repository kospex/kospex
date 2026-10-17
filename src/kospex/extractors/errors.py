"""The error every extractor raises for a file it could not read.

Raising is the only error channel an extractor has.
`kospex.osi_outcomes.classify_outcome` reaches PARSE_ERROR solely from its
`error is not None` branch -- where `error` is the exception the parser
raised -- and otherwise ends:

    return EXTRACTED if row_count else EMPTY

So a parser that catches its own exception and returns [] has its file
recorded as `empty`, whose documented meaning is "parser ran, produced nothing
-- genuinely declares none". "I could not read this file" is stored as "I read
this file and it declares no dependencies", and a repository holding an
unreadable manifest reports clean.

Manifests come from cloned third-party repositories, so this is fail-open on
attacker-influenced input in a tool whose purpose is to notice dependencies.
It is also a regression in signal as coverage grows: an unparsed file type
reports `unsupported`, which is honest, and gaining a parser can downgrade
that to `empty`, which is not.

The distinction was measured on package-lock.json before it was fixed:
changing `lockfileVersion: 3` to `"3"`, bumping it to 4, deleting the field or
truncating the JSON each read 0 of 2 real packages and reported the repository
as cleanly `empty`. One character, every dependency hidden, and a
benign-looking scan result.
"""


class UnreadableManifest(Exception):
    """This file could not be read, so nothing is known about its contents.

    Raise this instead of returning [] whenever the file is either

    - unreadable -- missing, a directory, permissions, bad encoding, invalid
      YAML/XML; or
    - read but uninterpretable -- an unrecognised lockfileVersion, a root that
      is not the mapping or element the format requires, a declared section
      holding something unusable, or a refusal by a security guard.

    Return [] only for the third case: the file was read, it is well-formed,
    and it genuinely declares no dependencies. A valid go.mod with no
    `require`, an empty `<Project></Project>` and a pnpm lockfile whose only
    importer is empty are all real and common, and for them `empty` is true.

    The message should name the file and say what was wrong with it. It is
    surfaced to the operator by `krunner osi` and logged by `kospex sca`; it is
    NOT stored, because the outcome record keeps the outcome word only (#148).
    """
