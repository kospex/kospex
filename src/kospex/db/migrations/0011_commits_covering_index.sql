-- 0011_commits_covering_index.sql
--
-- A covering index on commits, and author-time columns on developer_stats.
--
-- commits' only index was its primary key, (_repo_id, hash). The aggregates
-- behind kweb's summary pages and sync's developer stats read the author,
-- committer, their timestamps and the git server, owner and repo, so every one
-- fetched table rows. With this index they read the index alone.
--
-- Measured on a 1.1M-commit, 374-repo DB, page cache warm, production queries:
--
--   repos()                            1.66s   ->  0.49s
--   repos(org_key)                     0.92s   ->  0.09s
--   active_devs()                      1.01s   ->  0.08s
--   update_developer_stats SELECT      0.54s   ->  0.19s   (199k-commit repo)
--
-- Cost: 190MB on disk (+4.4 percent), built in 4.6s. Sync-style commit
-- upserts are 26 percent slower (about 120us a commit). commit_files upserts,
-- the bulk of a sync, are unaffected.
--
-- Column order is deliberate. _repo_id leads so per-repo queries (every sync)
-- seek. The git server, owner and repo come next because repos() selects them
-- and org and server scopes filter on them. Without them the index is not
-- covering, and repos() ignores it and scans the table. The remaining four
-- columns are what the aggregates read.
--
-- A covering index on commit_files was measured and rejected: 1GB (+25
-- percent) to halve one sync query that this index already cuts by two-thirds.
--
-- first_authored and last_authored are schema only. update_developer_stats
-- will fill them when first_commit and last_commit move to committer time.
-- Every sync recomputes a repo's rows, so existing rows fill on their next
-- sync and there is no backfill here. A NULL last_authored marks a row that
-- has not been recomputed yet. Design is in
-- changes/202610-sync-time-summary-stats.md

CREATE INDEX IF NOT EXISTS idx_commits_repo_author_when
    ON commits (_repo_id, _git_server, _git_owner, _git_repo,
                author_email, committer_email, committer_when, author_when);

ALTER TABLE developer_stats ADD COLUMN first_authored TEXT;
ALTER TABLE developer_stats ADD COLUMN last_authored TEXT;
