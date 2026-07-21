# Remediation: clear `/osi/` "last commit: None" from pre-#115 double-slash paths

## Who needs this

Only kospex databases that were **synced before the #115 fix**. New installs
are unaffected (the fixed `parse_git_rename_event` never produces the bad path),
so this is deliberately **not** a migration — run it by hand only if your
existing DB shows the symptom.

**Symptom:** on `/osi/` (and anywhere per-file "last commit" / dev-status is
shown) a file has a **last commit of `None`**. It happens for files that were
**moved between directories** at some point (e.g. `.github/dependabot.yml` after
it moved out of `.github/workflows/`).

## Cause (recap)

`git log --numstat` renders a directory-level rename with an empty brace side,
e.g. `.github/{workflows => }/dependabot.yml`. Before #115,
`parse_git_rename_event` collapsed that to a **doubled slash**
(`.github//dependabot.yml`) and stored it in `commit_files`. That malformed path
never matches the real working-tree path, so `file_metadata.committer_when` is
left `NULL`. #115 fixed the parser going forward, but **an incremental
`kospex sync` never re-parses the old rename commit**, so already-stored rows do
not self-heal — they must be corrected directly.

## The fix

**Back up your database first:**

```bash
cp ~/kospex/kospex.db ~/kospex/kospex.db.bak-$(date +%Y%m%d-%H%M%S)
```

Then, against `~/kospex/kospex.db`:

```sql
-- Step 1: collapse the doubled slash in stored commit paths (matches what the
-- #115-fixed parser now produces). Repo-relative git paths never legitimately
-- contain '//', and there are no 3+-slash cases, so a single REPLACE is safe.
UPDATE commit_files
SET file_path = REPLACE(file_path, '//', '/')
WHERE file_path LIKE '%//%';

-- Step 2: backfill committer_when for the file_metadata rows that now match a
-- corrected commit path. Bounded to the currently-NULL rows, so it is cheap.
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

Run it, e.g.:

```bash
sqlite3 ~/kospex/kospex.db < fix.sql      # or paste the two statements interactively
```

## Verify

```sql
-- Was ~N NULL rows; should drop by the double-slash count after the fix.
SELECT COUNT(*) FROM file_metadata WHERE latest = 1 AND committer_when IS NULL;

-- Spot-check the file you noticed (adjust repo_id / Provider):
SELECT committer_when FROM file_metadata
WHERE latest = 1
  AND _repo_id = 'github.com~expressjs~express'
  AND Provider = '.github/dependabot.yml';
-- expect a real date, not NULL
```

## Scope & notes

- This fixes only the **double-slash** cause. `NULL` `committer_when` from
  **non-ASCII filenames** (git quotes them in `--numstat` output) is a separate
  cause tracked in **issue #116** and is *not* addressed here.
- Step 2 only restores `committer_when` (the visible "last commit" value). It
  leaves `file_metadata.hash` at its previous fallback (repo HEAD). If you want
  `file_metadata` fully regenerated (both `hash` and `committer_when`) from the
  corrected `commit_files`, run `kospex sync-metadata -repo <repo_dir> -force`
  for each affected repo instead of Step 2 — slower (re-scans the working tree),
  but canonical.
- **Large DBs:** Step 1's `WHERE file_path LIKE '%//%'` scans `commit_files`
  (no index on the path), so on a very large history it can take a while. It is
  a one-time operation; run it when convenient.
- Related: #115 (the forward fix), #116 (the unicode variant).
