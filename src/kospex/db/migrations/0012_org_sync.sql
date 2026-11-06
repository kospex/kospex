-- 0012_org_sync.sql
--
-- The manifest for `kgit sync --org` / `--user` (#335), and the provider id
-- columns on repos from the lineage design (#348).
--
-- An org sync lists every repository an owner has, then clones and syncs them
-- one at a time, often in slices (`-next N`) from a schedule. Keeping that
-- list only in memory meant a crash, a Ctrl-C or one bad repository lost track
-- of what had been planned and what was done. The list is kept here instead:
--
--   org_sync_runs   one row per listing of an owner
--   org_sync_items  one row per repository in that listing, with its status
--
-- The run row and all of its items are written in one transaction, and only
-- from a complete listing, so there is never a half-saved list to resume from.
--
-- Same shape as osi_runs (0008): a run id plus one row per repository, keyed
-- so that history accumulates rather than being overwritten.
--
-- Growth: one row per repository per listing. Pruning old runs is an open
-- question in the design doc, as it is for osi_runs.
--
-- Design: changes/202610-kgit-sync-org.md

CREATE TABLE IF NOT EXISTS [org_sync_runs] (
    [run_id] TEXT,            -- UTC timestamp, as osi_runs
    [org_key] TEXT,           -- e.g. github.com~mergestat
    [provider] TEXT,          -- github (bitbucket later)
    [owner_type] TEXT,        -- org | user
    [listed] INTEGER,         -- repositories in the listing, after de-duplication
    [started_at] TEXT,
    [finished_at] TEXT,       -- NULL while the run is unfinished, so resumable
    PRIMARY KEY(run_id)
    );

-- "The unfinished run for this org" is the question every invocation asks.
CREATE INDEX IF NOT EXISTS idx_org_sync_runs_org ON org_sync_runs (org_key, finished_at);

CREATE TABLE IF NOT EXISTS [org_sync_items] (
    [run_id] TEXT,
    [_repo_id] TEXT,          -- via generate_repo_id(), never hand-built
    [clone_url] TEXT,
    [ssh_url] TEXT,
    [provider_repo_id] TEXT,  -- e.g. github.com:10270250, survives renames and transfers
    [archived] INTEGER,
    [fork] INTEGER,
    [fork_parent] TEXT,       -- full name of the immediate parent (forks only)
    [fork_source] TEXT,       -- full name of the root of the fork network
    [private] INTEGER,
    [pushed_at] TEXT,         -- from the provider, UTC
    [status] TEXT,            -- pending, in_progress, synced, skipped, failed
    [detail] TEXT,            -- why skipped or failed
    [started_at] TEXT,
    [finished_at] TEXT,
    [_git_server] TEXT,
    [_git_owner] TEXT,
    [_git_repo] TEXT,
    PRIMARY KEY(run_id, _repo_id)
    );

-- Picking the next slice filters a run by status.
CREATE INDEX IF NOT EXISTS idx_org_sync_items_status ON org_sync_items (run_id, status);

-- The provider's own id for the repository, from #348. Unlike the name it
-- survives renames and transfers, and a name can be reused by a different
-- repository, so it has to be recorded when kospex first sees the repository.
-- Host-prefixed (github.com:10270250) because ids are only unique per host.
ALTER TABLE [repos] ADD COLUMN [provider_repo_id] TEXT;

-- When the provider was last asked, whether or not it returned an id. Lets a
-- lookup skip repositories already checked.
ALTER TABLE [repos] ADD COLUMN [provider_checked_at] TEXT;
