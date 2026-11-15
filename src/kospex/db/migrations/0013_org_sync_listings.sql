-- 0013_org_sync_listings.sql
--
-- A history of org-sync listings, so a weekly listing shows how an org changes.
--
-- started_at: when the run was created (its first listing), not when syncing began.
--
-- Until now a run was listed once, when it started, so `started_at` said how
-- old its listing was. `kgit sync --org ... -refresh` (and `-active` on a
-- stale run) lists the org again and merges the result into the same run, so
-- the run keeps its `started_at` while its listing gets newer. `listed_at` is
-- that time, and what `-active` compares with KOSPEX_ORG_SYNC_REFRESH_DAYS.
--
-- A refresh overwrites the run's items, so each listing also gets a row in
-- org_sync_listings with what changed since the one before. Items gain:
--
--   created_at        when the provider says the repository was created
--   first_listed_at   the first listing of this org that had it, across runs,
--                     followed through a rename by provider_repo_id
--   archived_seen_at  the first listing that found it archived after one that
--                     didn't. NULL if it was already archived when first listed
--                     (kospex can't know when) or isn't archived.
--
-- The Python step backfills the existing runs, oldest first.
--
-- Design: changes/202611-kgit-sync-active.md

ALTER TABLE [org_sync_runs] ADD COLUMN [listed_at] TEXT;

UPDATE [org_sync_runs] SET [listed_at] = [started_at] WHERE [listed_at] IS NULL;

ALTER TABLE [org_sync_items] ADD COLUMN [created_at] TEXT;

ALTER TABLE [org_sync_items] ADD COLUMN [first_listed_at] TEXT;

ALTER TABLE [org_sync_items] ADD COLUMN [archived_seen_at] TEXT;

CREATE TABLE IF NOT EXISTS [org_sync_listings] (
    [run_id] TEXT,
    [org_key] TEXT,
    [listed_at] TEXT,         -- UTC
    [kind] TEXT,              -- first (the run's first listing) or refresh
    [listed] INTEGER,         -- repositories in the listing
    [new] INTEGER,            -- in no earlier listing of this org
    [gone] INTEGER,           -- in the previous listing, not in this one
    [back] INTEGER,           -- listed before, missing from the previous listing
    [renamed] INTEGER,        -- same provider_repo_id, new name
    [newly_archived] INTEGER, -- archived now, not in the previous listing it was in
    [unarchived] INTEGER,
    [archived] INTEGER,       -- totals in this listing
    [forks] INTEGER,
    [private] INTEGER
    );

CREATE INDEX IF NOT EXISTS idx_org_sync_listings_org ON org_sync_listings (org_key, listed_at);
