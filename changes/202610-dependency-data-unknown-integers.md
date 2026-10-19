# An un-enriched dependency row counted as vulnerable

Issue: [#258](https://github.com/kospex/kospex/issues/258)
Migration: none — see *Why there is no migration*

## Overview

`dependency_data.advisories` and `.versions_behind` are `INTEGER` columns, and
every record template seeded them with `""`. SQLite's type ordering sorts TEXT
above every INTEGER regardless of content, so `'' > 0` is **true** — an
un-enriched row matched `WHERE advisories > 0`, the predicate meaning "has a
security advisory".

The fix replaces `""` with `None` in all six copies of the record template, and
replaces the `setdefault` guard in `save_dependencies()` with a coercion that
can actually fire.

## Proof it was wrong

Through production's own `get_package_template()`, on a fresh fully-migrated
database:

```
1. template as built:  advisories='' versions_behind=''
2. as stored:          advisories='' (text)  versions_behind='' (text)
3. what readers see:
     WHERE advisories > 0         -> 1 rows
     WHERE advisories = 0         -> 0 rows
     WHERE advisories IS NULL     -> 0 rows
     WHERE versions_behind <= 5   -> 0 rows
     SUM(advisories)              -> 0.0
```

The row is in **none** of the three buckets a reader would treat as
exhaustive, and the one it does match is the one meaning "vulnerable".
`SUM()` also returns a float, because mixing TEXT into the aggregate changes
its type.

## The guard that could not fire

`save_dependencies()` already had this, and its comment stated the intent
exactly right — *"None, not 0 — 'we did not ask' is not 'we asked and found
none'"*:

```python
rec.setdefault("advisories", None)
rec.setdefault("versions_behind", None)
```

`setdefault` only fills a **missing** key. Every template supplied the key
already set to `""`, so the guard was a no-op on every record it was written to
defend. The comment even anticipated the trigger — *"reachable as soon as
extraction and enrichment are decoupled"* — which `krunner osi -next` then did.

Two lines below the same template, the `is_dev` / `is_optional` /
`runs_install_script` block gets this right and says why: *"An empty string
would land in an INTEGER column as a third thing meaning neither true, false,
nor unknown."* Same hazard, same file, opposite handling.

## Scale: the `latest` filter changes the answer by three orders of magnitude

A re-save supersedes a row rather than deleting it, so an un-enriched row that
was later re-enriched keeps its `""` at `latest = 0` — where no reporting query
looks. On a several-hundred-repository estate:

| | rows |
|---|---|
| `typeof(advisories) = 'text'`, total | 1,968 |
| of those, at `latest = 0` (superseded) | **1,962** |
| of those, at `latest = 1` (visible) | **6** |
| `WHERE advisories > 0 AND latest = 1` | 3,537, of which 6 wrong |
| `WHERE advisories > 0` (no filter) | 5,574, of which 1,968 wrong (35%) |

The issue was filed with the 35% figure, measured immediately after a
whole-estate sweep when those rows *were* current. Both numbers are real; only
the `latest = 1` one describes what anyone is being shown today. The fix is
unchanged either way — the writer is live, and the six current rows prove new
ones still reach `latest = 1`.

## Files changed

- `src/kospex_dependencies.py` — `get_package_template()` seeds both columns
  with `None`; `save_dependencies()` coerces `""` instead of calling
  `setdefault`.
- `src/kospex/extractors/{gomod,nuget,pnpm,npmlock}.py` — the four older local
  template copies. `uvlock.py` already carried the fix and cites #258; its
  docstring wording is reused verbatim so all six read identically.
- `tests/test_dependency_data_unknown_integers.py` — new; 6 tests.
- `tests/test_extractors_{gomod,nuget,pnpm,npmlock}.py` — the assertion ported
  from `test_extractors_uvlock.py`.
- `docs/refreshing-data.md` — *Clearing unknown advisory counts* runbook.

## Implementation notes

**Both halves, deliberately.** Fixing the templates alone would close the hole
today; fixing the save path alone would leave six templates producing a value
the save path has to know about. The coercion is the line of defence that
survives someone adding a seventh template, and it is tested separately
(`test_a_caller_supplied_empty_string_is_coerced`) so that reverting one does
not hide the other.

**Only `""` is coerced, not any falsy value.** `rec.get(column, "") == ""`
catches the empty string and the missing key, and leaves `0` alone. A measured
zero means "we asked and found none", which is information; turning it into
NULL would be the same defect pointing the other way.
`test_a_real_count_of_zero_is_not_turned_into_unknown` pins that, and passed
before the fix — kept deliberately, because it constrains the fix rather than
demonstrating the bug.

**`published_at` stays `""`.** It is a TEXT column, so an empty string there is
merely empty rather than a value outranking every number in the column. The
`table_rows()` coercion at `kospex_dependencies.py:334` still maps it to None
for JSON output and is unaffected.

**`kweb2.py:1588` is left as `item.get("advisories") or 0`.** With the column
now NULL, an unknown row renders as *not vulnerable* — still wrong, but
inverted. Rendering it honestly needs a distinct "unknown" state in the badge
and its templates, which is a UI change rather than a data one; #258 is about
what is stored. Tracked separately.

### Why there is no migration

The remaining `""` values are existing data, and a data-only change is not
expressible as a migration: a `.py` migration requires a matching `.sql`, and
that `.sql` cannot be empty, so one would need fabricated SQL wrapped around
the backfill. `CLAUDE.md` names this case and points at a runbook instead, as
`docs/refreshing-data.md` already does for `repo_id` case normalisation.

The runbook keys its `UPDATE` on `typeof(advisories) = 'text'` rather than
`= ''`, because one legacy row on the dev estate holds `'unknown'` — written by
a path that no longer exists, at `latest = 0`, with no `source`. NULL is the
right answer for that too, but a predicate of `= ''` would silently leave it.
The runbook tells the operator to look for a *number* stored as text and stop if
they find one, since that would be a different writer.

### Testing

The test that should have caught this already existed and passed:
`test_advisory_data_does_not_survive_an_unenriched_re_save` builds its record
with a `_record()` helper that **omits both keys**, so `setdefault` fires on
input production never produces. The new tests build their record from
`get_package_template()` for that reason.

They assert `typeof()`, not the Python value. The stored value and the Python
value are the same object here, and the storage *type* is the defect — a test
comparing `row["advisories"] == ""` passes against a fixed database too,
because NULL and `""` are both falsy coming out.

`test_no_integer_column_holds_text` derives its column list from
`PRAGMA table_info` rather than naming the two, so the next column to acquire
this fails without anyone remembering to extend the test.
