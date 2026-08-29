# Remove kospex_mergestat

## Overview

`src/kospex_mergestat.py` has been dead since December 2023 but still shipped in
every wheel. Deleted, along with its `pyproject.toml` entry and the two
commented-out references in `kospex_core.py`.

Mergestat was the prototype's git-query engine. It was replaced by direct git
wrapping in `d12d3dc` (2023-12-03, *"Change sync method to wrapping git"*), and
nothing has imported it since. The last substantive touch was `ae156a9`
(2024-07-27), which was the `src/` filesystem move rather than a code change.

The module says so itself, at the top:

```python
# WARNING note: This code works, but is no longer used in kospex
# It is kept here for reference and possible future use
```

Nearly three years is long enough for "possible future use" — the design it
encodes is preserved in git history if it is ever wanted.

## Evidence it is orphaned

| checked | result |
|---|---|
| Live imports under `src/` | none — only comments |
| `tests/` references | none |
| `docs/` references | none |
| Dockerfile / requirements | none |

The only `src/` references were:

```
kospex_core.py:16    # from kospex_mergestat import KospexMergeStat
kospex_core.py:382   # self.mergestat = KospexMergeStat()
kospex_core.py:498   #    results = self.mergestat.commit_files(**kwargs)
kospex_core.py:1130  #    for row in self.mergestat.cursor().execute(sql):
```

## What was removed

- `src/kospex_mergestat.py` — 154 lines
- the `"kospex_mergestat"` entry in `pyproject.toml` `py-modules`
- `kospex_core.py:16` and `:382` — the import and the instantiation

`:498` and `:1130` were left alone. They sit inside larger commented-out method
bodies, and deleting fragments of those would leave the blocks incoherent.
Removing the blocks wholesale is a separate cleanup with its own reasoning.

## Packaging check

`py-modules` is listed explicitly because setuptools' `packages.find`
auto-discovery **disables** it and drops the sibling `templates/` and `static/`
data directories — that is what broke the 0.0.38 wheel. Removing one entry is
safe, but an editable install hides this whole class of bug, so the wheel was
built and inspected:

```
mergestat present : False
top-level modules : 25      (was 26)
templates         : 50
static            : 8
total files       : 107
```

Both data directories intact.

## Note

`changes/202608-utc-date-ordering.md` lists `kospex_mergestat.py` among the files
it converted to timezone-correct ordering. That one line was in this dead code
and went with the file. The change doc has been annotated so the record is not
misleading.
