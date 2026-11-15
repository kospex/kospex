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
-- org_sync_commands has a row for each `kgit sync --org/--user` command that
-- listed or synced: the options as typed, what it did, and which GitHub token
-- it used (the login, the kind read from its prefix, the environment variable
-- and the expiry GitHub reports, never the token itself). A run is often done
-- over several commands with different options. Runs from before this have none.
--
-- Design: changes/202611-kgit-sync-active.md, changes/202611-kweb-syncs.md

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

CREATE TABLE IF NOT EXISTS [org_sync_commands] (
    [command_id] INTEGER PRIMARY KEY,
    [run_id] TEXT,            -- NULL only if a new run's first listing failed
    [org_key] TEXT,
    [started_at] TEXT,        -- UTC
    [finished_at] TEXT,       -- NULL while running, or if it was stopped
    [listing] TEXT,           -- the listing it saved: first, refresh, restart, or NULL
    [synced_any] INTEGER,     -- 1 if it cloned or synced items, 0 for -inventory
    [options] TEXT,           -- as typed, in kgit's fixed order
    [command] TEXT,           -- the whole command, ready to copy
    [synced] INTEGER,         -- items this command moved to each status
    [failed] INTEGER,
    [skipped] INTEGER,
    [new] INTEGER,            -- from the listing it saved, if any
    [gone] INTEGER,
    [auth_login] TEXT,        -- the GitHub login the token belongs to
    [auth_kind] TEXT,         -- fine-grained, classic, app, oauth, none or unknown
    [auth_source] TEXT,       -- the environment variable the token came from
    [auth_expires_at] TEXT,   -- UTC, from GitHub, NULL if it has no expiry
    [error] TEXT              -- why it stopped early, after it started
    );

CREATE INDEX IF NOT EXISTS idx_org_sync_commands_run ON org_sync_commands (run_id, started_at);
