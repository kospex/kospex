# Version Constraint Column Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record how each dependency is constrained, what version its advisory data refers to, and when that data was last checked.

**Architecture:** A pure classifier in `extractors/` maps a declared version plus its ecosystem to a `(kind, operator)` pair. Migration `0006` adds four nullable columns. The two write paths — `_enrich_dependency_records()` for `assess()` and `save_dependencies()` for `krunner osi` — populate them. The pypi requirements parser stops splitting the operator out of `package_version`.

**Tech Stack:** Python 3.12, sqlite_utils, pytest.

**Spec:** `changes/202609-version-constraint-column.md`

## Global Constraints

- **Extractors are pure.** No DB, no CLI, no network, no `KospexDependencies` import. `constraints.py` follows `pnpm.py` / `gomod.py` / `nuget.py`.
- **The classifier must never raise.** It runs during extraction; an exception loses a whole manifest. Return a tuple for every input including `None`, `""` and malformed values.
- **`package_version` holds the declared text.** It is part of the `dependency_data` primary key — rewriting it inserts duplicate rows on re-sync instead of updating.
- **`last_checked` is written explicitly on every save**, insert and update. It must NOT be a SQL `DEFAULT`; that is precisely the `created_at` defect this replaces.
- **Do not edit a shipped migration.** `0006` is new; `0003`–`0005` are immutable.
- **Do NOT include `BEGIN` / `COMMIT` / `ROLLBACK` in migration SQL** — the runner manages the transaction.
- **Run tests with `PYTHONPATH=$PWD/src pytest`** — an editable install resolves the flat `kospex_*` modules to the main checkout, so without it tests silently exercise the wrong code.
- **Australian/US spelling:** follow the file you are editing; the codebase uses "normalise" in comments.

---

## File Structure

| File | Responsibility |
| --- | --- |
| `src/kospex/extractors/constraints.py` | **Create.** Pure `classify_constraint()`. |
| `src/kospex/db/migrations/0006_dependency_version_constraint.sql` | **Create.** Four `ALTER TABLE` statements. |
| `src/kospex_dependencies.py` | **Modify.** Populate in `_enrich_dependency_records()` and `save_dependencies()`; stop splitting the operator in `parse_pypi_package_declaration()`. |
| `tests/test_constraint_classifier.py` | **Create.** Truth table + totality. |
| `tests/test_version_constraint_columns.py` | **Create.** Both write paths, `last_checked` on update, realignment. |

Task order matters: Task 1 produces `classify_constraint()`, used by Tasks 3 and 4. Task 2 produces the columns those tasks write to.

---

### Task 1: The constraint classifier

**Files:**
- Create: `src/kospex/extractors/constraints.py`
- Test: `tests/test_constraint_classifier.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `classify_constraint(declared_version, package_type) -> tuple[str, str]` returning `(kind, operator)`. `kind` is one of the fourteen values below; `operator` is the raw declared operator or `""`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_constraint_classifier.py`:

```python
"""Classify how a dependency is constrained (#187).

Every value here occurs in a real 109-repo estate — none are invented. The
classifier runs during extraction, so it must never raise: an exception would
lose a whole manifest, not one row.
"""
import pytest

from kospex.extractors.constraints import classify_constraint


class TestVersionConstraints:
    @pytest.mark.parametrize("declared,package_type,kind,operator", [
        # exact pins
        ("1.4.3", "npm", "pinned", ""),
        ("==2.31.0", "pypi", "pinned", "=="),
        ("3.1.1", "nuget", "pinned", ""),
        # npm range prefixes — tilde is NOT caret: patch-only vs minor+patch
        ("^4.18.0", "npm", "caret", "^"),
        ("~29.0.0", "npm", "tilde", "~"),
        # open floor
        (">=2.0", "pypi", "gte", ">="),
        (">2.0", "pypi", "gte", ">"),
        # windowed — an upper bound exists
        (">=1.0,<2.0", "pypi", "bounded", ">=,<"),
        (">=23.0, <24.3", "pypi", "bounded", ">=,<"),
        ("^1.0.0 || ^2.0.0", "npm", "bounded", "||"),
        (">=1.0 <2.0", "npm", "bounded", ">=,<"),
        ("~=8.1", "pypi", "tilde", "~="),
        ("<4", "pypi", "bounded", "<"),
        # Upper bound declared FIRST — the common shape in the live estate
        # (79 rows, all of this form). The operator label is normalised to
        # ">=,<" regardless of declared order; the raw text stays in
        # package_version if anyone needs the ordering back.
        ("<1,>=0.23.0", "pypi", "bounded", ">=,<"),
        ("<5,>=4.14", "pypi", "bounded", ">=,<"),
    ])
    def test_version_constraints(self, declared, package_type, kind, operator):
        assert classify_constraint(declared, package_type) == (kind, operator)


class TestGoPseudoVersions:
    """A Go pseudo-version is MAXIMALLY pinned — a specific commit — yet today
    it lands in unresolved_spec alongside `>=1.0,<2.0`, which is the opposite.
    12 of mergestat's 88 Go modules are this shape."""

    @pytest.mark.parametrize("declared", [
        "v0.0.0-20230828082145-3c4c8a2d2371",
        "v0.0.0-20221005151137-0ff49e3f5413",
        "v1.5.1-0.20230307220236-3a3c6141e376",
    ])
    def test_pseudo_versions_are_commit_pins(self, declared):
        assert classify_constraint(declared, "go") == ("commit", "")

    def test_ordinary_go_version_is_pinned_not_commit(self):
        assert classify_constraint("v1.8.0", "go") == ("pinned", "")


class TestNonVersionValues:
    """Resolution mechanisms, not constraints. Each means something different."""

    @pytest.mark.parametrize("declared,kind,operator", [
        ("latest", "latest", ""),
        ("workspace:^", "workspace", "workspace:"),
        ("workspace:*", "workspace", "workspace:"),
        ("link:../scripts/repo-utils", "link", "link:"),
        # file: is a local path like link:, and occurs in the live estate.
        ("file:./packages/helpers", "link", "file:"),
        ("catalog:", "catalog", "catalog:"),
        ("catalog:dev", "catalog", "catalog:"),
        ("npm:@babel/core@7.24.4", "alias", "npm:"),
        ("patch:rollup-plugin-dts@npm%3A6.1.0#~/.yarn/patches/x", "patch", "patch:"),
    ])
    def test_non_version_values(self, declared, kind, operator):
        assert classify_constraint(declared, "npm") == (kind, operator)


class TestNoDeclaration:
    @pytest.mark.parametrize("declared", ["", None])
    def test_absent_version_is_none_kind(self, declared):
        assert classify_constraint(declared, "pypi") == ("none", "")


class TestTotality:
    """It runs during extraction — raising loses a whole manifest."""

    @pytest.mark.parametrize("declared", [
        "", None, "   ", "\n", "!!!", "@@@@", "." * 300,
        "éèê", ">=", "^", "~", "||", ":", "1.2.3.4.5.6",
    ])
    def test_never_raises_and_always_returns_a_pair(self, declared):
        result = classify_constraint(declared, "npm")
        assert isinstance(result, tuple) and len(result) == 2
        assert all(isinstance(x, str) for x in result)

    @pytest.mark.parametrize("package_type", ["npm", "pypi", "go", "nuget", None, "", "unknown"])
    def test_unknown_ecosystem_does_not_raise(self, package_type):
        kind, operator = classify_constraint("^1.0.0", package_type)
        assert isinstance(kind, str) and isinstance(operator, str)


class TestKindVocabularyIsClosed:
    """Guards against a typo introducing a fourteenth kind by accident."""

    VALID = {
        "pinned", "commit", "caret", "tilde", "gte", "bounded", "latest",
        "workspace", "link", "catalog", "alias", "patch", "none",
    }

    @pytest.mark.parametrize("declared,package_type", [
        ("1.4.3", "npm"), ("^4.18.0", "npm"), ("~1.0", "npm"),
        (">=1.0,<2.0", "pypi"), ("latest", "npm"), ("catalog:dev", "npm"),
        ("v0.0.0-20230828082145-3c4c8a2d2371", "go"), ("", "pypi"),
        ("npm:x@1.0.0", "npm"), ("workspace:^", "npm"), ("junk!!", "npm"),
    ])
    def test_kind_is_always_from_the_vocabulary(self, declared, package_type):
        kind, _op = classify_constraint(declared, package_type)
        assert kind in self.VALID, f"{declared!r} produced unknown kind {kind!r}"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=$PWD/src pytest tests/test_constraint_classifier.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'kospex.extractors.constraints'`

- [ ] **Step 3: Implement the classifier**

Create `src/kospex/extractors/constraints.py`:

```python
"""Classify how a dependency version is constrained.

Pure: no DB, no CLI, no I/O. Given a declared version string and its ecosystem,
returns the normalised `kind` and the raw declared `operator`.

Two columns rather than one because they answer different questions. `kind` is
queryable across ecosystems — "show me everything floating" is one WHERE clause
whether the manifest wrote `^` or `>=`. `operator` keeps the declared text, so a
classification we get wrong is still recoverable from the row.

The vocabulary is deliberately finer than pinned/floating. `tilde` is separate
from `caret` because `~29.0.0` permits patch drift only where `^4.18.0` permits
minor and patch — collapsing them misreports how far a dependency can move,
which is the question this exists to answer. `commit` is separate from `pinned`
because a Go pseudo-version is maximally pinned yet deps.dev has nothing to
match it against.

See changes/202609-version-constraint-column.md.
"""
import re

# The closed vocabulary. A value not in here is a bug.
KINDS = (
    "pinned", "commit", "caret", "tilde", "gte", "bounded", "latest",
    "workspace", "link", "catalog", "alias", "patch", "none",
)

# Resolution mechanisms, not version constraints. Prefix -> kind.
_MECHANISM_PREFIXES = (
    ("workspace:", "workspace"),
    ("link:", "link"),
    ("catalog:", "catalog"),
    ("patch:", "patch"),
    ("npm:", "alias"),
    ("file:", "link"),
    ("portal:", "link"),
)

# Go pseudo-version: <base>-<14-digit UTC timestamp>-<12-hex commit>.
# Two forms — v0.0.0-20230828082145-3c4c8a2d2371, and the pre-release form
# v1.5.1-0.20230307220236-3a3c6141e376 where the pre-release is separated from
# the timestamp by a DOT, not a dash. Getting that separator wrong silently
# classifies the second form as `pinned`, which is how it reads at a glance.
_GO_PSEUDO = re.compile(r"^v?\d+\.\d+\.\d+-(?:[\w.]+\.)?\d{14}-[0-9a-f]{12}$")

_UPPER_BOUND_OPS = ("<=", "<")


def classify_constraint(declared_version, package_type=None):
    """Return (kind, operator) for a declared version string.

    Never raises: it runs during extraction, where an exception would lose a
    whole manifest rather than one row. Unrecognised input classifies as
    "pinned" if it looks like a version and "none" otherwise.
    """
    if declared_version is None:
        return ("none", "")

    text = str(declared_version).strip()
    if not text:
        return ("none", "")

    lowered = text.lower()

    # Resolution mechanisms first — `workspace:^` starts with a prefix AND
    # contains a caret, so prefix matching has to win.
    for prefix, kind in _MECHANISM_PREFIXES:
        if lowered.startswith(prefix):
            return (kind, prefix)

    if lowered in ("latest", "next", "*", "x"):
        return ("latest", "")

    # Go pseudo-versions are commit pins, not ranges — check before the
    # operator scan, since the embedded '-' must not be read as anything else.
    if _GO_PSEUDO.match(text):
        return ("commit", "")

    # Disjunction of ranges: `^1.0.0 || ^2.0.0`. Bounded — alternatives are
    # each windowed — and recorded as `||` so the shape stays visible.
    if "||" in text:
        return ("bounded", "||")

    operators = _operators_in(text)

    if operators:
        has_lower = any(op in (">=", ">") for op in operators)
        has_upper = any(op in _UPPER_BOUND_OPS for op in operators)

        if has_lower and has_upper:
            return ("bounded", ">=,<")
        if has_upper:
            # `<4` alone is a ceiling with an open floor: still windowed above.
            return ("bounded", operators[0])
        if "~=" in operators:
            # PEP 440 compatible-release: patch drift, the pypi tilde.
            return ("tilde", "~=")
        if "==" in operators:
            return ("pinned", "==")
        if has_lower:
            return ("gte", operators[0])
        return ("pinned", operators[0])

    if text[0] == "^":
        return ("caret", "^")
    if text[0] == "~":
        return ("tilde", "~")

    # A bare version, or something unrecognised that is shaped like one.
    if re.match(r"^v?\d", text):
        return ("pinned", "")

    return ("none", "")


def _operators_in(text):
    """Comparison operators present in a declaration.

    Two-character operators are consumed first, so the `>` inside `>=` is not
    also counted as a bare greater-than — which would read `>=1.0` as having
    both an inclusive and an exclusive lower bound.
    """
    found = []
    remaining = text
    for op in (">=", "<=", "==", "!=", "~="):
        if op in remaining:
            found.append(op)
            remaining = remaining.replace(op, " ")
    for op in (">", "<"):
        if op in remaining:
            found.append(op)
    return found
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTHONPATH=$PWD/src pytest tests/test_constraint_classifier.py -q`
Expected: PASS. If a parametrised case fails, fix the classifier — do not relax the expectation. Each value came from real data.

- [ ] **Step 5: Verify against the live estate**

The truth table uses real values, but the estate holds shapes nobody listed. Run the classifier over every distinct stored version and check nothing lands in a surprising kind:

```bash
PYTHONPATH=$PWD/src python -c "
import sqlite3, os, collections
from kospex.extractors.constraints import classify_constraint, KINDS
c = sqlite3.connect('file:' + os.path.expanduser('~/kospex/kospex.db') + '?mode=ro', uri=True)
rows = c.execute('SELECT package_version, package_type FROM dependency_data WHERE latest=1').fetchall()
counts = collections.Counter()
for v, pt in rows:
    kind, op = classify_constraint(v, pt)
    assert kind in KINDS, (v, kind)
    counts[kind] += 1
for k, n in counts.most_common():
    print(f'  {k:<12} {n}')
c.close()"
```

Expected: every row classifies, no assertion fires. This implementation was run
against the 6,386-row reference estate while the plan was written, and every row
classified. Use these as the baseline — a material deviation means the
classifier changed behaviour:

```
  pinned      3300   51.7%     bounded      79   1.2%
  caret       1566   24.5%     catalog      65   1.0%
  workspace    807   12.6%     alias        27   0.4%
  none         266    4.2%     tilde        12   0.2%
  gte          229    3.6%     commit       12   0.2%
                              link         11   0.2%
                              latest       10   0.2%
                              patch         2   0.0%
```

**These figures predate two later corrections and no longer match the shipped
classifier.** Recomputed over the same 6,386 rows after `pinned` was narrowed
to "exactly one version, nothing can drift":

```
  pinned      3205  (-95)     gte          308  (+79)     bounded   95  (+16)
```

79 Go rows moved `pinned` → `gte` (a `require` is a Minimal Version Selection
floor; 91 Go rows less the 12 pseudo-versions, which stay `commit`). 16 moved
`pinned` → `bounded`: 4 `==N.*` wildcards and 12 npm partial versions
(`react "16"`, `chalk "4"`, `@types/react "19.2"`), which npm reads as ranges
but which carry no wildcard character to give them away. Everything else is
unchanged.
`excluded` is 0 today only because the estate's one `!=` declaration is still
stored flattened; it becomes 1 on re-sync.

All kinds except `excluded` occur in real data — none is speculative. `commit` = 12 is
exactly the twelve `mergestat` Go pseudo-versions the spec cites, which is the
check that the `_GO_PSEUDO` regex is matching both forms.

Note `pinned` is inflated here: it includes the requirements.txt rows still
flattened by the parser. After Task 5 those move to `gte` and `bounded`, so
`pinned` should fall by roughly 3-4 points once the estate is re-synced. If it
does not move at all after Task 5, the realignment did not take effect.

If a large bucket lands in `none`, a real shape is unhandled — add it to the
truth table and fix the classifier.

- [ ] **Step 6: Commit**

```bash
git add src/kospex/extractors/constraints.py tests/test_constraint_classifier.py
git commit -m "feat(extractors): add the constraint classifier

Pure classifier mapping a declared version and its ecosystem to a
normalised kind plus the raw declared operator. tilde is separate from
caret because ~29.0.0 permits patch drift only where ^4.18.0 permits minor
and patch; commit is separate from pinned because a Go pseudo-version is
maximally pinned yet deps.dev has nothing to match it against.

Never raises — it runs during extraction, where an exception loses a whole
manifest rather than one row."
```

---

### Task 2: Migration 0006

**Files:**
- Create: `src/kospex/db/migrations/0006_dependency_version_constraint.sql`
- Test: `tests/test_version_constraint_columns.py`

**Do NOT add these columns to `SQL_CREATE_DEPENDENCY_DATA`.** The baseline
`CREATE TABLE` is frozen at `KOSPEX_DB_VERSION = 2` and does not contain
`resolution` either, even though migration `0005` added it. A fresh DB runs the
baseline CREATE and *then* bootstraps the migrations, so a column present in
both places makes `ALTER TABLE ADD COLUMN` fail with `duplicate column name` on
every clean install. Migration only.

**Interfaces:**
- Consumes: nothing.
- Produces: columns `version_kind`, `version_operator`, `resolved_version`, `last_checked` on `dependency_data`, present both after migration and on a freshly created DB.

- [ ] **Step 1: Write the failing test**

Create `tests/test_version_constraint_columns.py`:

```python
"""The four columns recording what a dependency declared and when it was checked (#187)."""


def _fresh_home(tmp_path, monkeypatch):
    """Point kospex at an empty KOSPEX_HOME.

    The HabitatConfig singleton caches the resolved paths, so setting the env
    var is not enough — it must be reset. Matches tests/test_kospex_schema.py.
    """
    from kospex.habitat_config import HabitatConfig
    monkeypatch.setenv("KOSPEX_HOME", str(tmp_path))
    HabitatConfig.reset_instance()


NEW_COLUMNS = ("version_kind", "version_operator", "resolved_version", "last_checked")


class TestSchema:
    def test_fresh_db_has_the_columns(self, tmp_path, monkeypatch):
        """A clean install bootstraps the migrations, so the columns exist."""
        import kospex_schema as KospexSchema
        _fresh_home(tmp_path, monkeypatch)

        db = KospexSchema.connect_or_create_kospex_db()

        cols = [c[1] for c in db.execute("PRAGMA table_info(dependency_data)").fetchall()]
        for name in NEW_COLUMNS:
            assert name in cols, f"{name} missing from a freshly created DB"

    def test_baseline_create_table_does_not_declare_them(self):
        """The migration owns these columns, not the frozen baseline.

        SQL_CREATE_DEPENDENCY_DATA is frozen at KOSPEX_DB_VERSION = 2 and does
        not carry `resolution` either, though 0005 added it. A fresh DB runs the
        baseline CREATE and then bootstraps migrations, so declaring a column in
        both makes ALTER TABLE fail with `duplicate column name` on every clean
        install. This test is the guard against someone "fixing" the baseline.
        """
        import kospex_schema as KospexSchema

        for name in NEW_COLUMNS:
            assert name not in KospexSchema.SQL_CREATE_DEPENDENCY_DATA, (
                f"{name} must be added by migration 0006 only, not the baseline"
            )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=$PWD/src pytest tests/test_version_constraint_columns.py -q`
Expected: FAIL — `version_kind missing from a freshly created DB`

- [ ] **Step 3: Write the migration**

Create `src/kospex/db/migrations/0006_dependency_version_constraint.sql`:

```sql
-- 0006_dependency_version_constraint.sql
--
-- How a dependency is constrained, what its advisory data refers to, and when
-- that data was last checked.
--
-- version_kind      normalised classification: pinned, commit, caret, tilde,
--                   gte, bounded, latest, workspace, link, catalog, alias,
--                   patch, none. Queryable across ecosystems.
-- version_operator  the raw declared operator, verbatim ("^", ">=", ">=,<",
--                   "workspace:"). Empty for a bare pin — the manifest wrote
--                   no operator and version_kind carries the meaning.
-- resolved_version  the version actually sent to deps.dev. For a range this is
--                   the floor, so advisories/versions_behind describe the worst
--                   case the constraint permits, not what is installed. Empty
--                   when nothing resolved (commit, latest, none).
-- last_checked      UTC timestamp, written on EVERY save. Deliberately not a
--                   column DEFAULT: created_at is DEFAULT CURRENT_TIMESTAMP and
--                   therefore never updates, so a row refreshed from deps.dev
--                   still reads its original insert date. That is the defect
--                   this column exists to avoid.
--
-- NULL on existing rows until re-sync. A staleness indicator must render NULL
-- as "never checked", not "checked long ago".

ALTER TABLE dependency_data ADD COLUMN version_kind TEXT;
ALTER TABLE dependency_data ADD COLUMN version_operator TEXT;
ALTER TABLE dependency_data ADD COLUMN resolved_version TEXT;
ALTER TABLE dependency_data ADD COLUMN last_checked TEXT;
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTHONPATH=$PWD/src pytest tests/test_version_constraint_columns.py tests/test_kospex_schema.py -q`
Expected: PASS, including `test_fresh_db_has_no_pending_migrations`.

- [ ] **Step 5: Verify the migration by hand**

```bash
rm -rf /tmp/kospex-0006 && mkdir -p /tmp/kospex-0006
KOSPEX_HOME=/tmp/kospex-0006 PYTHONPATH=$PWD/src python -m kospex_cli upgrade-db
```
Expected: `No pending migrations. DB is at version 6.`

If this errors, #175 and #176 are open bugs on exactly this path — a schema ahead of its ledger, and a raw traceback instead of a diagnosis. Fix the cause rather than working around it, and say so.

- [ ] **Step 6: Commit**

```bash
git add src/kospex/db/migrations/0006_dependency_version_constraint.sql tests/test_version_constraint_columns.py
git commit -m "feat(db): add version constraint columns (migration 0006)

version_kind, version_operator, resolved_version and last_checked on
dependency_data. All nullable, so existing rows stay valid and populate on
re-sync. Added by migration only — the baseline CREATE TABLE is frozen, and
declaring a column in both makes a clean install fail on duplicate column.

last_checked is written by the save paths, never a column DEFAULT:
created_at is DEFAULT CURRENT_TIMESTAMP and so never updates, which is why
a mergestat row refreshed from deps.dev still read its July insert date."
```

---

### Task 3: Populate from the assess() path

**Files:**
- Modify: `src/kospex_dependencies.py` — `_enrich_dependency_records()` (around line 336)
- Test: `tests/test_version_constraint_columns.py`

**Interfaces:**
- Consumes: `classify_constraint(declared_version, package_type) -> (kind, operator)` from Task 1; the columns from Task 2.
- Produces: records carrying `version_kind`, `version_operator`, `resolved_version`, `last_checked`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_version_constraint_columns.py`:

```python
import contextlib
import io


def _kdeps():
    from kospex_dependencies import KospexDependencies
    kd = KospexDependencies()
    kd.depsdev_record = lambda pt, pn, pv: {
        "package_name": pn, "package_version": pv, "package_type": pt,
        "versions_behind": 1, "advisories": 0, "resolution": "resolved",
        "published_at": "", "source_repo": "",
    }
    kd.get_pypi_source_repo = lambda n: ""
    return kd


def _assess(kd, path):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        return kd.assess(path) or []


class TestAssessPathPopulates:
    def test_npm_caret_is_classified(self, tmp_path):
        kd = _kdeps()
        p = tmp_path / "package.json"
        p.write_text('{"name":"d","dependencies":{"express":"^4.18.0"}}')

        rec = _assess(kd, str(p))[0]

        assert rec["version_kind"] == "caret"
        assert rec["version_operator"] == "^"

    def test_resolved_version_records_the_floor(self, tmp_path):
        """A range's advisories describe the floor, so the row has to say so."""
        kd = _kdeps()
        p = tmp_path / "package.json"
        p.write_text('{"name":"d","dependencies":{"express":"^4.18.0"}}')

        rec = _assess(kd, str(p))[0]

        assert rec["package_version"] == "^4.18.0"   # declared text preserved
        assert rec["resolved_version"] == "4.18.0"   # what deps.dev was asked

    def test_go_pseudo_version_is_a_commit_pin(self, tmp_path):
        kd = _kdeps()
        p = tmp_path / "go.mod"
        p.write_text("module x\nrequire github.com/a/b v0.0.0-20230828082145-3c4c8a2d2371\n")

        rec = _assess(kd, str(p))[0]

        assert rec["version_kind"] == "commit"

    def test_last_checked_is_set(self, tmp_path):
        kd = _kdeps()
        p = tmp_path / "package.json"
        p.write_text('{"name":"d","dependencies":{"express":"4.18.0"}}')

        rec = _assess(kd, str(p))[0]

        assert rec["last_checked"], "last_checked must be stamped on every save"
        assert rec["last_checked"].startswith("20")   # ISO-ish timestamp
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=$PWD/src pytest tests/test_version_constraint_columns.py -q -k AssessPath`
Expected: FAIL with `KeyError: 'version_kind'`

- [ ] **Step 3: Implement**

In `src/kospex_dependencies.py`, add near the other imports:

```python
from kospex.extractors.constraints import classify_constraint
```

Add a helper on the class, beside `_semantic_prefix`:

```python
    @staticmethod
    def _utc_now_iso():
        """UTC timestamp for last_checked, second precision."""
        import datetime
        return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
```

In `_enrich_dependency_records()`, inside the per-record loop, after
`out["package_use"] = ...` and before the npm `semantic` block:

```python
            # What the manifest declared, recorded so "are we pinned?" can be
            # asked of the database rather than re-derived from the string.
            kind, operator = classify_constraint(declared_version, package_type)
            out["version_kind"] = kind
            out["version_operator"] = operator

            # What the advisory numbers actually refer to. For a range this is
            # the floor, so `advisories` describes the worst case the constraint
            # permits — unreadable without recording which version was queried.
            out["resolved_version"] = "" if skip_lookup else lookup_version

            # Written on every save. NOT a column default: created_at is
            # DEFAULT CURRENT_TIMESTAMP and so never updates on an upsert.
            out["last_checked"] = self._utc_now_iso()
```

`lookup_version` is only assigned inside `if not skip_lookup:`. Initialise it to `""` immediately before that block so the assignment above is always valid:

```python
            lookup_version = ""
            if not skip_lookup:
                lookup_version = self.clean_version_spec(
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTHONPATH=$PWD/src pytest tests/test_version_constraint_columns.py -q`
Expected: PASS

- [ ] **Step 5: Run the full suite**

Run: `PYTHONPATH=$PWD/src pytest -q`
Expected: PASS. The four new keys reach the upsert; they are real columns after Task 2, so no `_NON_SCHEMA_FIELDS` entry is needed.

- [ ] **Step 6: Commit**

```bash
git add src/kospex_dependencies.py tests/test_version_constraint_columns.py
git commit -m "feat(deps): populate the constraint columns from assess()

version_kind and version_operator record what the manifest declared;
resolved_version records what deps.dev was actually asked, which for a
range is the floor — so an advisory count can be read as worst case rather
than guessed at. last_checked is stamped on every save."
```

---

### Task 4: Populate from the krunner osi path

**Files:**
- Modify: `src/kospex_dependencies.py` — `save_dependencies()` (around line 530)
- Test: `tests/test_version_constraint_columns.py`

**Interfaces:**
- Consumes: `classify_constraint()`, `_utc_now_iso()` from Task 3.
- Produces: `save_dependencies()` writes the same four columns.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_version_constraint_columns.py`:

```python
class TestOsiPathPopulates:
    """Both write paths must populate these. Testing one and assuming the other
    is how the Go `v`-prefix defect reached production twice."""

    def _db(self):
        import sqlite_utils
        import kospex_schema as KospexSchema
        db = sqlite_utils.Database(memory=True)
        db.execute(KospexSchema.SQL_CREATE_DEPENDENCY_DATA)
        return db

    def _record(self, version="^4.18.0", name="express"):
        return {
            "_repo_id": "s~o~r", "hash": "h1", "file_path": "package.json",
            "package_type": "npm", "package_name": name,
            "package_version": version, "requirements_type": "direct",
        }

    def test_save_dependencies_classifies(self):
        from kospex_dependencies import KospexDependencies
        db = self._db()
        kd = KospexDependencies(kospex_db=db)

        kd.save_dependencies([self._record()], source="test")

        row = next(db.query("SELECT * FROM dependency_data"))
        assert row["version_kind"] == "caret"
        assert row["version_operator"] == "^"
        assert row["last_checked"]

    def test_last_checked_updates_on_re_save(self):
        """The precise bug created_at has: a DB DEFAULT is set on INSERT and
        never again, so a row refreshed from deps.dev keeps its original date."""
        import time
        from kospex_dependencies import KospexDependencies
        db = self._db()
        kd = KospexDependencies(kospex_db=db)

        kd.save_dependencies([self._record()], source="test")
        first = next(db.query("SELECT last_checked FROM dependency_data"))["last_checked"]

        time.sleep(1.1)   # second precision
        kd.save_dependencies([self._record()], source="test")
        second = next(db.query(
            "SELECT last_checked FROM dependency_data WHERE latest=1"))["last_checked"]

        assert second > first, (
            f"last_checked did not advance on re-save ({first} -> {second}); "
            "it must be written explicitly, not left to a column default"
        )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=$PWD/src pytest tests/test_version_constraint_columns.py -q -k OsiPath`
Expected: FAIL — `version_kind` is `None`, since `save_dependencies()` does not set it.

- [ ] **Step 3: Implement**

In `save_dependencies()`, inside the loop that builds `cleaned` (after
`rec["latest"] = 1`), add:

```python
            kind, operator = classify_constraint(
                rec.get("package_version"), rec.get("package_type")
            )
            rec["version_kind"] = kind
            rec["version_operator"] = operator
            rec["last_checked"] = self._utc_now_iso()
```

`resolved_version` is set by the caller here: `krunner osi` enriches before
saving, so `enrich_dependency_records()` in `src/krunner.py` sets it alongside
the deps.dev call. Add there, next to `d["resolution"] = ...`:

```python
        d["resolved_version"] = kdeps.clean_version_spec(
            d["package_version"], d["package_type"]
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTHONPATH=$PWD/src pytest tests/test_version_constraint_columns.py -q`
Expected: PASS

- [ ] **Step 5: Run the full suite**

Run: `PYTHONPATH=$PWD/src pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/kospex_dependencies.py src/krunner.py tests/test_version_constraint_columns.py
git commit -m "feat(deps): populate the constraint columns from krunner osi

Both write paths now record the same four columns. Testing one and
assuming the other is how the Go v-prefix defect reached production twice.

Includes a test that last_checked advances on re-save — the precise
failure created_at has, where a column DEFAULT is set on INSERT and never
again."
```

---

### Task 5: Stop the requirements parser flattening the operator

**Files:**
- Modify: `src/kospex_dependencies.py` — `parse_pypi_package_declaration()` (around line 750)
- Test: `tests/test_version_constraint_columns.py`

**Interfaces:**
- Consumes: the columns and classifier from earlier tasks.
- Produces: `parse_pypi_package_declaration()` returns the declared text in `package_version`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_version_constraint_columns.py`:

```python
class TestRequirementsRealignment:
    """requirements.txt was the only parser splitting the operator out of
    package_version, so `flask>=2.0` stored `2.0` — indistinguishable from a
    pin. Source files are 53% pinned while the stored data read 94% bare."""

    def test_operator_stays_in_package_version(self, tmp_path):
        kd = _kdeps()
        p = tmp_path / "requirements.txt"
        p.write_text("flask>=2.0\n")

        rec = _assess(kd, str(p))[0]

        assert rec["package_version"] == ">=2.0"
        assert rec["version_kind"] == "gte"
        assert rec["version_operator"] == ">="

    def test_exact_pin_is_unchanged(self, tmp_path):
        kd = _kdeps()
        p = tmp_path / "requirements.txt"
        p.write_text("click==8.1\n")

        rec = _assess(kd, str(p))[0]

        assert rec["package_version"] == "==8.1"
        assert rec["version_kind"] == "pinned"

    def test_matches_pyproject_for_the_same_declaration(self, tmp_path):
        """The point of the realignment: one declaration, one representation."""
        kd = _kdeps()
        (tmp_path / "requirements.txt").write_text("flask>=2.0\n")
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname="d"\ndependencies=["flask>=2.0"]\n')

        req = _assess(kd, str(tmp_path / "requirements.txt"))[0]
        proj = _assess(kd, str(tmp_path / "pyproject.toml"))[0]

        assert req["package_version"] == proj["package_version"] == ">=2.0"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=$PWD/src pytest tests/test_version_constraint_columns.py -q -k Realignment`
Expected: FAIL — `assert '2.0' == '>=2.0'`

- [ ] **Step 3: Implement**

In `parse_pypi_package_declaration()` (around line 810), the single-specifier
`else` branch stores the bare version. Replace exactly this:

```python
        else:
            package["package_version"] = specifiers[0].version
            package["version_type"] = specifiers[0].operator
```

with this — the same `_PYPI_NAME_EXTRAS_RE.sub()` the multi-specifier branch
directly above already uses, so both branches now agree:

```python
        else:
            # Keep the declared text, as the multi-specifier branch above does.
            # package_version is part of the dependency_data primary key and
            # every other parser stores the declaration as written; splitting
            # the operator out here made `flask>=2.0` indistinguishable from a
            # pin, because version_type is in _NON_SCHEMA_FIELDS and never
            # persisted. The operator now lives in version_operator.
            package["package_version"] = self._PYPI_NAME_EXTRAS_RE.sub(
                "", spec_part).strip()
            package["version_type"] = specifiers[0].operator
```

`version_type` keeps its value — it stays the parser's working field and stays
in `_NON_SCHEMA_FIELDS`. Only what lands in `package_version` changes.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTHONPATH=$PWD/src pytest tests/test_version_constraint_columns.py -q`
Expected: PASS

- [ ] **Step 5: Run the full suite**

Run: `PYTHONPATH=$PWD/src pytest -q`
Expected: PASS. `tests/test_pypi_declaration_parsing.py` asserts the old bare-version behaviour in places — update those expectations to the declared text and note in each why. Do NOT weaken an assertion to make it pass; if one now describes wrong behaviour, rewrite it to describe the right behaviour.

- [ ] **Step 6: Commit**

```bash
git add src/kospex_dependencies.py tests/
git commit -m "fix(deps): keep the declared operator in package_version for requirements

requirements.txt was the only parser splitting the operator out, so
flask>=2.0 stored 2.0 — indistinguishable from a pin once version_type was
discarded. Source files across a 109-repo estate are 53% pinned while the
stored data read 94% bare, overstating pinning discipline by ~20 points.

The operator now lives in version_operator, and package_version holds the
declared text like every other parser."
```

---

### Task 6: Changelog and upgrade notes

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Add the Added entry**

Under `## Unreleased` → `### Added`:

```markdown
- **Dependency constraints are now recorded.** Four columns on
  `dependency_data` (migration `0006`): `version_kind` classifies how a
  dependency is constrained (pinned / commit / caret / tilde / gte / bounded /
  latest / workspace / link / catalog / alias / patch / none),
  `version_operator` keeps the raw declared operator, `resolved_version`
  records the version deps.dev was actually asked about, and `last_checked`
  records when. Previously the classification was computed and discarded, so
  "which of our dependencies float?" could not be asked of the database, and an
  advisory count could not be read without knowing which version it referred to
  or how old it was. `tilde` is deliberately separate from `caret` — `~29.0.0`
  permits patch drift where `^4.18.0` permits minor and patch — and `commit` is
  separate from `pinned`, since a Go pseudo-version is maximally pinned but has
  no published release to match against. Columns are NULL on existing rows
  until re-sync; a staleness indicator must treat NULL as "never checked", not
  "checked long ago". See `changes/202609-version-constraint-column.md`.
```

- [ ] **Step 2: Add the upgrade note**

`CHANGELOG.md` → `## Unreleased` → `### Upgrade notes` opens with **"Reported
numbers change in this release, in seven ways."** Change `seven` to `eight`
(the word, not a digit — match the existing prose) and append item `8.` to the
end of that numbered list:

```markdown
8. **Python requirements rows show their operator.** `requirements.txt` was the
   only parser that split the operator out of `package_version`, so `flask>=2.0`
   stored `2.0` and looked pinned. It now stores `>=2.0`, matching
   `pyproject.toml`, `package.json`, `go.mod` and `.csproj`. About 317 rows
   change; `package_version` is in the primary key, so the old rows are
   superseded rather than updated and demoted by the next `krunner osi` run.
   Anything reading that column verbatim will show the operator — which is
   accurate, and what `version_kind` now lets you filter on instead.
```

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): record the version constraint columns"
```

---

## Final Verification

- [ ] **Full suite:** `PYTHONPATH=$PWD/src pytest -q` — all pass.
- [ ] **Clean install reaches version 6:**
```bash
rm -rf /tmp/kospex-final && mkdir -p /tmp/kospex-final
KOSPEX_HOME=/tmp/kospex-final PYTHONPATH=$PWD/src python -m kospex_cli upgrade-db
```
Expect `No pending migrations. DB is at version 6.`
- [ ] **Classifier covers the live estate** — re-run the Task 1 Step 5 script; every row classifies, nothing unexpected in `none`.
- [ ] **End-to-end write:** back up `~/kospex/kospex.db`, apply the migration to it, run `krunner osi` over one small repo, and confirm the four columns are populated on the new rows. Verifying in isolation is what let the Go `v`-prefix defect ship twice.

## Notes for the implementer

**Why `last_checked` is not a column DEFAULT.** `created_at` is
`DEFAULT CURRENT_TIMESTAMP`, which SQLite applies on INSERT only. Re-running
`krunner osi` over `mergestat` re-fetched 95 npm rows from deps.dev and they
still read their original July date. If you "simplify" this to a default, the
column becomes useless for staleness and the failure is invisible.

**Why the classifier must not raise.** It runs inside extraction. An exception
does not lose one row, it loses the manifest — and the caller logs and moves on,
so nothing looks broken.

**Why `package_version` keeps declared text.** It is part of the primary key.
Rewriting it inserts duplicate rows on re-sync instead of updating, which is the
divergence C2a fixed for npm.

**#175 and #176 sit on this path.** They concern `upgrade-db` failing to recover
or diagnose a schema/ledger conflict. Neither blocks writing `0006`, but if
applying it locally goes wrong they are why it is hard to read. Fix the cause
rather than working around it.
