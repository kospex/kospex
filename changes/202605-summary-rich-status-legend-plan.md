# Summary command: rich output + status legend — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert the `kospex summary` command's two tables to `rich`, and add a legend describing the four repo development statuses.

**Architecture:** `rich` is already a project dependency (used in `kospex_cli.py` and `kospex stats`). Two helpers in `kospex_utils.py` change: `get_status_table()` is converted from `PrettyTable` to `rich.table.Table`, and a new `get_status_legend()` is added. `kospex_core.summary()` swaps its per-repo `PrettyTable` for a `rich.table.Table` (sorting rows manually since rich doesn't auto-sort). `kospex_cli.summary()` prints the legend and switches the now-rich tables to `console.print`.

**Tech Stack:** Python 3.12, `rich`, `pytest`, Click.

---

## File Structure

- `src/kospex_utils.py` — add `from rich.table import Table`; convert `get_status_table()` (line 1291) to rich; add `get_status_legend()`.
- `src/kospex_core.py` — add a module-level `rich` `Console`; convert the per-repo table in `summary()` (~line 417) to rich; sort results by `last_commit`.
- `src/kospex_cli.py` — in `summary()`, print legend below status table; change `print(...)` → `console.print(...)` for all `get_status_table()` callers (lines 311, 337, 350, 637).
- `tests/test_kospex_utils.py` — add tests for `get_status_legend()` and the rich `get_status_table()`.

Status definitions (from `development_status()`, defaults 90/180/365):

| Status        | Meaning                          |
|---------------|----------------------------------|
| Active        | commit within last 90 days       |
| Aging         | last commit 91–180 days ago      |
| Stale         | last commit 181–365 days ago     |
| Unmaintained  | no commit in over 365 days       |

---

### Task 1: `get_status_legend()` helper

**Files:**
- Modify: `src/kospex_utils.py` (add import near line 14; add function near the existing `get_status_table` at line 1291)
- Test: `tests/test_kospex_utils.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_kospex_utils.py`:

```python
def test_get_status_legend():
    """get_status_legend returns a rich Table describing the 4 statuses."""
    from rich.console import Console
    from rich.table import Table

    legend = KospexUtils.get_status_legend()
    assert isinstance(legend, Table)

    rendered = Console(width=120).capture()
    with rendered:
        Console(width=120).print(legend)
    text = rendered.get()
    for status in ["Active", "Aging", "Stale", "Unmaintained"]:
        assert status in text
    # thresholds appear in the descriptions
    assert "90" in text
    assert "180" in text
    assert "365" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_kospex_utils.py::test_get_status_legend -v`
Expected: FAIL with `AttributeError: module 'kospex_utils' has no attribute 'get_status_legend'`

- [ ] **Step 3: Add the rich import (if not present)**

In `src/kospex_utils.py`, after line 14 (`from prettytable import PrettyTable`), add:

```python
from rich.table import Table as RichTable
```

(Use the `RichTable` alias to avoid any future name clash; the rest of the plan refers to `RichTable`.)

- [ ] **Step 4: Write the implementation**

Add near the existing `get_status_table` (around line 1291) in `src/kospex_utils.py`:

```python
def get_status_legend():
    """Return a rich Table legend describing the development statuses.

    Statuses come from development_status() with the default thresholds
    (active_limit=90, aging_limit=180, stale_limit=365).
    """
    table = RichTable(title="Status legend", title_style="dim", show_edge=True)
    table.add_column("Status", style="dim")
    table.add_column("Meaning", style="dim")
    table.add_row("Active", "commit within last 90 days")
    table.add_row("Aging", "last commit 91-180 days ago")
    table.add_row("Stale", "last commit 181-365 days ago")
    table.add_row("Unmaintained", "no commit in over 365 days")
    return table
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_kospex_utils.py::test_get_status_legend -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/kospex_utils.py tests/test_kospex_utils.py
git commit -m "feat: add get_status_legend rich helper

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Convert `get_status_table()` to rich

**Files:**
- Modify: `src/kospex_utils.py:1291` (`get_status_table`)
- Test: `tests/test_kospex_utils.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_kospex_utils.py`:

```python
def test_get_status_table_rich():
    """get_status_table returns a rich Table with counts and percentages."""
    from rich.console import Console
    from rich.table import Table

    status = {"Active": 3, "Aging": 1, "Stale": 0, "Unmaintained": 1}
    table = KospexUtils.get_status_table(status)
    assert isinstance(table, Table)

    rendered = Console(width=120).capture()
    with rendered:
        Console(width=120).print(table)
    text = rendered.get()
    for header in ["Active", "Aging", "Stale", "Unmaintained", "Total"]:
        assert header in text
    assert "5" in text       # total count
    assert "%" in text       # percentage row present
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_kospex_utils.py::test_get_status_table_rich -v`
Expected: FAIL with `AssertionError` on `isinstance(table, Table)` (currently returns a `PrettyTable`).

- [ ] **Step 3: Rewrite `get_status_table` using rich**

Replace the body of `get_status_table` (line 1291) in `src/kospex_utils.py` with:

```python
def get_status_table(status):
    """
    Return a rich Table for the status results
    ("Active", "Aging", "Stale", "Unmaintained").
    Row 1 = raw counts, row 2 = percentages.
    """

    total = sum(status.values())
    status_percentage = convert_to_percentage(status)
    # Need to run convert first, or the generic function
    # will include the percentage values in the calculation
    status["Total"] = total
    status_percentage["Total"] = 100

    headers = ["Active", "Aging", "Stale", "Unmaintained", "Total"]
    table = RichTable()
    for header in headers:
        table.add_column(header, justify="right")

    table.add_row(*[str(status.get(key, 0)) for key in headers])
    table.add_row(*[f"{status_percentage.get(key, 0)}%" for key in headers])

    return table
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_kospex_utils.py::test_get_status_table_rich -v`
Expected: PASS

- [ ] **Step 5: Run the full utils test file (no regressions)**

Run: `pytest tests/test_kospex_utils.py -v`
Expected: PASS (all tests)

- [ ] **Step 6: Commit**

```bash
git add src/kospex_utils.py tests/test_kospex_utils.py
git commit -m "feat: convert get_status_table to rich Table

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Convert the per-repo table in `kospex_core.summary()` to rich

**Files:**
- Modify: `src/kospex_core.py` (imports near line 17; `summary()` ~line 417-522)

This task has no unit test (requires a populated `~/kospex/kospex.db`); verify by running the command. Logic/data are unchanged — only rendering and explicit sort.

- [ ] **Step 1: Add rich imports / Console to `kospex_core.py`**

After line 17 (`from prettytable import PrettyTable, from_db_cursor`), add:

```python
from rich.console import Console as RichConsole
from rich.table import Table as RichTable
```

After the imports block (near line 25, after `from kospex_query import ...`), add a module-level console:

```python
_console = RichConsole()
```

- [ ] **Step 2: Replace the PrettyTable setup in `summary()`**

In `summary()`, replace the block from `table = PrettyTable()` (line 417) through `table.sortby = "last_commit"` (line 443) with:

```python
        headers = [
            "repo",
            "status",
            "developers",
            "active",
            "present",
            "last_commit",
            "first_commit",
            "active_days",
            "commits",
        ]

        left_cols = {"repo", "status"}
        table = RichTable()
        for header in headers:
            justify = "left" if header in left_cols else "right"
            table.add_column(header, justify=justify)
```

- [ ] **Step 3: Collect rows, sort by last_commit, then add to the table**

Currently rows are added inside the `for row in kd.execute():` loop via
`table.add_row(KospexUtils.get_values_by_keys(row, headers))` (line 506).
Remove that in-loop `table.add_row(...)` call, and after the loop (after the
`results.append(row)` block, before the `if results_file:` block at line 517) add:

```python
        # rich does not auto-sort; replicate PrettyTable's sortby="last_commit"
        results.sort(key=lambda r: (r.get("last_commit") is None, r.get("last_commit")))
        for row in results:
            table.add_row(*[str(v) for v in KospexUtils.get_values_by_keys(row, headers)])
```

- [ ] **Step 4: Print via the rich console**

Replace the `if table.rows:` / `print(table)` block (lines 521-522) with:

```python
        if results:
            _console.print(table)
```

- [ ] **Step 5: Verify by running the command**

Run: `python -m kospex summary`
Expected: a rich-bordered repo table prints, sorted by `last_commit` ascending (oldest first), with the same columns as before. No traceback.

- [ ] **Step 6: Commit**

```bash
git add src/kospex_core.py
git commit -m "feat: render summary repo table with rich

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Wire up `kospex_cli.summary()` — legend + console.print

**Files:**
- Modify: `src/kospex_cli.py` (`summary()` ~lines 308-350; caller near line 637)

No unit test (CLI integration needs a DB); verify by running. `console` already exists at `src/kospex_cli.py:31`.

- [ ] **Step 1: Print the status-count table and legend via rich in `summary()`**

In `summary()`, replace lines 308-312:

```python
        # Print status stats of the repos found
        print("Repo status summary")
        status = KospexUtils.count_key_occurrences(results, "status")
        status_table = KospexUtils.get_status_table(status)
        print(status_table)
        print()
```

with:

```python
        # Print status stats of the repos found
        console.print("Repo status summary")
        status = KospexUtils.count_key_occurrences(results, "status")
        status_table = KospexUtils.get_status_table(status)
        console.print(status_table)
        console.print(KospexUtils.get_status_legend())
        console.print()
```

- [ ] **Step 2: Update the docker status-table print**

In `summary()`, replace lines 336-337:

```python
            docker_status_table = KospexUtils.get_status_table(docker_status)
            print(docker_status_table)
```

with:

```python
            docker_status_table = KospexUtils.get_status_table(docker_status)
            console.print(docker_status_table)
```

- [ ] **Step 3: Update the dependencies status-table print**

In `summary()`, replace lines 348-350:

```python
            deps_status_table = KospexUtils.get_status_table(dep_stats)
            print()
            print(deps_status_table)
```

with:

```python
            deps_status_table = KospexUtils.get_status_table(dep_stats)
            console.print()
            console.print(deps_status_table)
```

(If the exact surrounding lines differ, the rule is: every `print(<var from get_status_table>)` becomes `console.print(<var>)`.)

- [ ] **Step 4: Update the remaining `get_status_table` caller (near line 637)**

Replace lines 636-637:

```python
            status_table = KospexUtils.get_status_table(stats)
            print(status_table)
```

with:

```python
            status_table = KospexUtils.get_status_table(stats)
            console.print(status_table)
```

- [ ] **Step 5: Confirm no stale `print(` of a status table remains**

Run: `grep -n "print(.*status_table\|print(.*_status_table" src/kospex_cli.py`
Expected: no plain `print(` matches — only `console.print(...)` (or none).

- [ ] **Step 6: Verify by running the commands**

Run: `python -m kospex summary`
Expected: rich repo table, rich status-count table, then the "Status legend" table below it. No traceback.

Run: `python -m kospex summary -dependencies`
Expected: dependencies sub-summary status table renders as a rich table (no legend), no traceback.

- [ ] **Step 7: Commit**

```bash
git add src/kospex_cli.py
git commit -m "feat: print status legend and rich tables in summary command

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Full test run + changes-doc cross-check

- [ ] **Step 1: Run the whole suite**

Run: `pytest`
Expected: PASS (no regressions).

- [ ] **Step 2: Re-read the changes doc and confirm it matches what shipped**

Open `changes/202605-summary-rich-status-legend.md` and confirm the "Files changed" list matches the actual edits. Update any drift (e.g. line numbers, alias names). Commit if changed:

```bash
git add changes/202605-summary-rich-status-legend.md
git commit -m "changes: align summary rich doc with implementation

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review notes

- **Spec coverage:** Task 2 (rich `get_status_table` — both tables req.), Task 3 (rich per-repo table — both tables req.), Task 1 + Task 4 (legend below table, summary-only). Shared-helper scope note covered by Task 4 steps 2-4. All spec sections mapped.
- **Type consistency:** `RichTable` alias used consistently in `kospex_utils.py` (Tasks 1, 2) and `kospex_core.py` (Task 3, where `RichConsole`/`RichTable` are local aliases). `get_status_legend()` / `get_status_table()` names consistent across tasks.
- **No placeholders:** all code shown inline; Task 4 step 3 includes a fallback rule for line drift.
