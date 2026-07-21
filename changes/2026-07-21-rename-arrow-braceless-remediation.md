# Brace-less git rename paths stored raw in `commit_files`

## Symptom

Same visible symptom as the double-slash bug (`2026-07-21-commit-files-double-slash-remediation.md`):
a file shows **last commit `None`** on `/osi/` and anywhere per-file "last commit" /
dev-status is rendered. Different cause, so the earlier remediation does not fix these.

## Cause

`git log --numstat` compresses a rename using braces **only when the old and new
paths share a common leading directory or trailing component**:

```
docs/{license.rst => license.md}          # shared prefix "docs/"
.github/{workflows => }/dependabot.yml    # shared prefix and suffix
```

When the two paths have **nothing** in common, git emits the whole field as a
bare `old => new` with no braces:

```
FASTAPI_MIGRATION.md => changes/FASTAPI_MIGRATION.md   # root file moved into a subdir
LICENSE.rst => LICENSE.txt                             # extension change at the root
```

`extract_git_rename_paths()` matches on `r'{[^{]* => [^}]*}'` — braces are
mandatory — so `parse_git_rename_event()` returned the brace-less form
**unchanged**. The literal string `"LICENSE.rst => LICENSE.txt"` was then stored
as `commit_files.file_path`, never matched the working-tree path, and
`build_file_metadata_rows` left `file_metadata.committer_when` NULL.

Fixed in `parse_git_rename_event()`: when no brace rename matched but the field
still contains `" => "`, keep the right-hand (new) path. Guarded on the spaced
`" => "` so a filename that merely contains `=>` is untouched. Test:
`tests/test_kospex.py::test_git_rename_event_no_braces`.

## Remediation for existing databases

New syncs are correct from this fix onward, but **an incremental re-sync
(`kgit pull`, `kgit sync`, `kospex sync-directory`) only reads commits newer than
the last synced one**, so it never re-parses the old rename commit and stored
rows do not self-heal. This is deliberately **not** a migration — run it only if
your DB predates the fix.

**Back up first:**

```bash
cp ~/kospex/kospex.db ~/kospex/kospex.db.bak-$(date +%Y%m%d-%H%M%S)
```

Optional pre-check — should return 0. If it does not, collapsing the arrow would
collide with an existing `(hash, file_path, _repo_id)` primary key, and Step 1
will fail rather than silently drop a row:

```sql
SELECT COUNT(*) FROM commit_files a
JOIN commit_files b
  ON a._repo_id = b._repo_id AND a.hash = b.hash
 AND b.file_path = substr(a.file_path, instr(a.file_path, ' => ') + 4)
WHERE a.file_path LIKE '% => %';
```

```sql
-- Step 1: keep the new path from the raw "old => new" field, matching what the
-- fixed parser now produces. ' => ' is 4 characters, so +4 starts the new path.
UPDATE commit_files
SET file_path = substr(file_path, instr(file_path, ' => ') + 4)
WHERE file_path LIKE '% => %';

-- Step 2: backfill committer_when for file_metadata rows that now match a
-- corrected commit path. Bounded to currently-NULL rows, so it is cheap.
UPDATE file_metadata
SET committer_when = (
    SELECT cf.committer_when FROM commit_files cf
    WHERE cf._repo_id = file_metadata._repo_id
      AND cf.file_path = file_metadata.Provider
    ORDER BY cf.committer_when DESC LIMIT 1)
WHERE committer_when IS NULL
  AND EXISTS (
    SELECT 1 FROM commit_files cf
    WHERE cf._repo_id = file_metadata._repo_id
      AND cf.file_path = file_metadata.Provider);
```

## Verify

```sql
SELECT COUNT(*) FROM commit_files WHERE file_path LIKE '% => %';        -- expect 0
SELECT COUNT(*) FROM file_metadata WHERE latest = 1 AND committer_when IS NULL;
```

## Scope & notes

- Step 2 restores `committer_when` only; `file_metadata.hash` keeps its previous
  fallback (repo HEAD). For a canonical rebuild of both, run
  `kospex sync-metadata -repo <repo_dir> -force` per affected repo instead.
- Step 1's `LIKE '% => %'` scans `commit_files` (no index on the path); on a large
  history it takes a while. One-time operation.
- **Other known causes of a NULL `committer_when`**, not addressed here:
  - non-ASCII filenames quoted by git `core.quotePath` — issue #116;
  - files whose content was introduced **only by a merge commit** (subtree adds,
    conflict-resolution edits). `git log --numstat` emits no diff for merge
    commits by default, so no `commit_files` row exists at all, while
    `file_metadata` still sees the file in the working tree.
- Related: #115 (double-slash rename), #116 (unicode quoting).
