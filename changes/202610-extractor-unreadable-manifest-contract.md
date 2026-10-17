# An unreadable manifest is reported as unread, not as empty (CWE-392)

A parser that could not read a file returned `[]`, and `[]` is recorded as the
outcome `empty` — whose documented meaning is "parser ran, produced nothing,
genuinely declares none". "I could not read this file" was therefore stored as
"I read this file and it declares no dependencies", and a repository holding an
unreadable manifest reported clean.

This closes that for every registered parser and makes the rule a contract
driven from the registry, so the next parser inherits it.

**CWE-392, Missing Report of Error Condition** (class: CWE-703, Improper Check
or Handling of Exceptional Conditions). Manifests come from cloned third-party
repositories, so the input is attacker-influenced, and the tool's purpose is to
notice dependencies — a silent read failure is the one failure mode that
produces a confidently wrong answer rather than a visible one.

## The mechanism

`kospex/osi_outcomes.py` ends `classify_outcome` with:

```python
return EXTRACTED if row_count else EMPTY
```

`PARSE_ERROR` is reachable only from the function's `error is not None` branch,
where `error` is the exception the parser raised and the caller caught.
**Raising is the only error channel an extractor has.** A parser that catches
its own exception and returns `[]` arrives with `row_count == 0` and
`error is None`, so its file is recorded `empty`.

It is also a regression in signal as coverage grows: a file type with no parser
reports `unsupported`, which is honest, and gaining a parser can downgrade that
to `empty`, which is not.

## The rule, in three categories

The fix is not "replace every `return []` with a raise" — the third category is
real, common data that must keep reporting `empty`, because for it `empty` is
true.

| Category | Examples | Behaviour |
|---|---|---|
| **Unreadable** | missing, a directory, permissions, bad encoding, invalid YAML/XML/TOML | **raise** |
| **Read but uninterpretable** | an unrecognised `lockfileVersion`, a root that is not the mapping or element the format requires, a declared section holding something unusable, a security guard's refusal, a `go.mod` with no `module` directive | **raise** |
| **Read, declares nothing** | a valid `go.mod` with no `require`, `<Project></Project>`, a pnpm lockfile whose only importer is empty | **return `[]`** |

Measured on a development estate: of 188 `go.mod` files **42 declare no
`require` at all** (the smallest is 27 bytes), and **4 of 9** `.csproj` files
declare no `PackageReference`. Those are not failures and are not treated as
any.

## What changed

New `src/kospex/extractors/errors.py` holds `UnreadableManifest`, the one type
to catch. `npmlock.py`'s existing `UnreadableLockfile` — the first extractor to
do this, and the precedent for the rest — becomes a subclass, so its own name
and tests are unaffected while `except UnreadableManifest` now covers every
extractor.

**`pnpm.py`** had six `return []` paths: YAML error, missing file, OS/decode
error, a non-mapping root, an unrecognised `lockfileVersion`, and a `packages:`
key that is not a mapping. All raise. The version check is deliberately the
loud choice — pnpm v10 is released and this parser reads 5, 6 and 9, so staying
quiet would report every v10 lockfile as `empty`, a whole closure hidden behind
an outcome claiming the file was read.

An *absent* `packages:` section still returns `[]`: pnpm omits it entirely for
a project that resolves nothing, so its absence is a fact about the project. A
key that is present and unusable is a different thing.

**`gomod.py`** had a bare `except Exception` returning `[]`. It now raises, and
the catch is narrowed to `OSError` / `UnicodeDecodeError` — anything else is a
bug and should be loud, which both scan paths handle. A `module` directive is
now required, because without it `go.mod` was the only format here with no
structural gate at all: every line it does not understand is skipped, so
arbitrary text under that name read as a module requiring nothing. All 188
`go.mod` files on the estate declare `module`, so nothing real is reclassified.

**`nuget.py`** had four, one of them a security refusal. The DOCTYPE guard
(CWE-776, uncontrolled entity expansion) was correct to refuse the file and
wrong in what it returned: a hostile `.csproj` was refused and then recorded as
a project with no NuGet packages, so **the security control's own outcome was
indistinguishable from a clean scan**. It now raises — and the check moved
*outside* the read's `try`, where the broad `except` would have swallowed the
refusal it raises. A root element that is not `<Project>` now raises too: the
`PackageReference` search finds nothing in such a file, so any XML under a
`.csproj` name reported as a project declaring nothing.

`PackageReference` is now matched ignoring any XML namespace. SDK-style project
files — the only kind using `PackageReference` — omit `xmlns`, and none on the
estate carries one, but a namespace-qualified document is legal and a
namespace-blind `findall` would have found nothing in it. Adding the root gate
without this would have created a fresh silent-empty path while closing others.

**`parse_pyproject_file`** was found by the registry-wide contract test, not by
inspection: it caught `(TOMLDecodeError, OSError)` and returned `[]`. One real
`pyproject.toml` of 131 on the estate does not parse (an unclosed array) and
was being reported as a project with no dependencies.

### This reverses [#97](https://github.com/kospex/kospex/issues/97), deliberately

That swallow was not an oversight. It was the fix for #97 —
`changes/20260505-pyproject-parser-error-handling.md` — where a malformed TOML
fixture raised out of the parser and **aborted the whole `krunner osi` run
before any results were written**. The doc states the reasoning: wrap at the
parser rather than at the caller, "because every caller of
`parse_pyproject_file` now benefits, not just `krunner osi`".

That was right in May and is obsolete now, because what it was compensating for
has since been fixed properly. Both scan paths acquired broad per-file guards
afterwards: `osi_extract` records `parse_error` against the one file and
carries on, and `assess` logs and returns `None`. The parser no longer has to
swallow anything for the batch to survive — and swallowing was costing the
outcome.

Verified rather than assumed, by running the extraction loop over that same
repository: **the run completes, 8 rows from 17 files**, the bad-syntax fixture
is the single `parse_error`, and the other 15 `pyproject.toml` files continue
to report `empty` as before. The failure mode #97 fixed does not come back.

What is **not** reverted is #97's other half: a single malformed dependency
string inside an otherwise-valid `pyproject.toml` is still logged and skipped
via `except InvalidRequirement`. That is a per-declaration skip in a file that
was read successfully, not a silent whole-file failure, and it is the mechanism
by which indented `#` comment lines are correctly rejected.

## Both scan paths

A parser contract only one caller honours is not a contract: the `npmlock` fix
had to add a guard to `assess` for precisely this reason. Both are now broad
and both were driven, not reasoned about —

- `kospex/osi_extract.py` records `PARSE_ERROR` against the file and continues;
- `kospex_dependencies.py`'s `assess` logs, prints and returns `None`.

`parse_gomod` is additionally reached through the public
`KospexDependencies.parse_go_mod_from_file()`, so the raise lives in the parser
rather than only in the `extract_gomod` wrapper.

## Tests

`tests/test_extractor_unreadable_manifests.py` owns the contract. The
load-bearing test is **registry-driven**: it resolves every entry with a
`parse_ref` and asserts none of them returns a value for a directory, which is
unreadable for every format at once. That test is what turned up
`parse_pyproject_file`, and it covers a parser added later on the day it is
registered.

Per-extractor error-handling assertions were flipped from `== []` to
`pytest.raises` in place; the duplicates they would otherwise have created are
left to the contract file, which each now points at.

One existing assertion was replaced rather than flipped.
`test_doctype_rejected_before_any_expansion` asserted `not any("AAAA" in str(r)
for r in records)` against a list it had already asserted was empty — satisfied
by any implementation. It now asserts that the **DOCTYPE refusal** is the
branch that fired, which is a real discriminator: if the guard moved after
`ET.fromstring` the expansion would already have run and the failure would
arrive as a parse error instead.

## Blast radius, measured

Every `pnpm-lock.yaml`, `go.mod`, `.csproj` and `pyproject.toml` the database
knows about was parsed with the old code and the new, comparing a sha256 of each
file's sorted `name@version` list: **357 files, 349 byte-identical**.

The changes are the two files that genuinely cannot be read — a 0-byte
`pnpm-lock.yaml` and the malformed `pyproject.toml` — which now report
`parse_error` instead of `empty`, and six lockfiles covered by the next
section.

No schema change, no migration, no stored data rewritten. What changes is the
outcome recorded for a file kospex cannot read, and that only on the next scan.

## The defect the fix exposed: a lockfile can be two YAML documents

Not part of the above, and not found by looking for it. Verifying the fix
against every real lockfile turned up six that `yaml.safe_load` refuses
outright:

```
yaml.composer.ComposerError: expected a single document in the stream
  ... but found another document, line 158, column 1
```

**When a project pins pnpm itself** through `packageManagerDependencies`, pnpm
writes a **multi-document** lockfile: the closure of the pnpm binary as one
YAML document, the project's own closure as a second, each with its own
`lockfileVersion`, `importers`, `packages` and `snapshots`. `safe_load` accepts
exactly one document and raises on the stream.

Six lockfiles across three repositories on a development estate are shaped this
way, holding **7,337 package entries between them** — and every one of those
entries was being reported as `empty`. One of the six is a 3,652-entry closure
for a single repository.

So the fail-open fix alone would have turned these six from a silent `empty`
into an honest `parse_error` and still read nothing. Reading the stream is what
recovers them, which is why it belongs with the fix rather than after it.

`extract_pnpm_lock` now reads via `safe_load_all` and extracts **per document**,
because classification is a per-document fact: pooling the stream's importers
first would resolve a direct dependency of document 2 against document 1's
importers, which do not mention it. Every document is checked before any is
extracted, so a stream whose second document is unusable cannot return the
first one's packages as though the file had been read in full. A trailing `---`
yields a `None` document, which declares nothing and is dropped rather than
rejected.

Verified through the production extraction loop — the one both `krunner osi
-all` and `-next` use — on two affected repositories: one went from 27 rows to
700 with its lockfile flipping `empty` → `extracted`, the other from 6,466 rows
to 11,022 with four lockfiles flipping.

### One silent skip kept, deliberately

`if not name or not version: continue` in the packages loop is the last path in
this parser that produces no record and no error. Measured: **3 of 23,769**
real `packages` entries, all three `file:../<dir>` — local workspace
directories, which have no registry `name@version` for deps.dev to answer
about, so producing no record is right.

It is **not** raised, because a git or tarball dependency key
(`github.com/owner/repo/<sha>`) also fails to split and no lockfile on the
estate measured carries one. Raising would convert working scans into
`parse_error` across a population that has not been measured — the opposite of
how the `module` and `<Project>` gates above were added, each checked against
every such file first. Instead the two cases are now distinguished: a known
non-registry prefix is skipped quietly, anything else is **logged**, so the
case leaves a trace without a guess. Worth narrowing once a lockfile with a git
dependency can be measured.
