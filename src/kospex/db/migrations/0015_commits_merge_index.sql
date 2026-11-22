-- 0015_commits_merge_index.sql
--
-- A partial index over merge commits only, for `kospex orphans` and kweb
-- /orphans/.
--
-- Orphans counts a repo's authors with merge commits left out (a merge's
-- author merged the code rather than wrote it). Telling a merge apart needs
-- parents and _files, which no index holds, so excluding merges read every
-- commit row: about 10 seconds on a 1.4M-commit database, against about 1
-- second for the same grouping from idx_commits_repo_author_when.
--
-- Instead the authors come from that covering index, and the merge commits
-- from this one, and an author whose every commit is a merge is dropped.
-- Merges are a small share of commits (4,025 of 1.4M in the dev database), so
-- the index is small and quick to build.
--
-- The WHERE clause must match KospexQuery.repo_author_emails() exactly, or
-- SQLite will not use the index. It is NOT AUTHORSHIP_COMMIT in kospex_query.py.
-- A commit with NULL parents is not in it: without parents a merge can't be
-- told apart, so it counts as authorship.
--
-- Partial indexes need SQLite 3.8.0, well below the 3.26 kospex supports.

CREATE INDEX IF NOT EXISTS idx_commits_merge_authors
    ON commits (_repo_id, author_email, author_when)
    WHERE NOT (parents <= 1 OR _files > 0)
