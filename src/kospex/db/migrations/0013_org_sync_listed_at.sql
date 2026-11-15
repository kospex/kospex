-- 0013_org_sync_listed_at.sql
--
-- When an org-sync run's listing was last taken.
--
-- started_at: when the run was created (its first listing), not when syncing began.
--
-- Until now a run was listed once, when it started, so `started_at` said how
-- old its listing was. `kgit sync --org ... -refresh` (and `-active` on a
-- stale run) lists the org again and merges the result into the same run, so
-- the run keeps its `started_at` while its listing gets newer. `listed_at` is
-- that time, and what `-active` compares with KOSPEX_ORG_SYNC_REFRESH_DAYS.
--
-- Existing runs were listed once, at the start, so they are backfilled from
-- `started_at`.
--
-- Design: changes/202611-kgit-sync-active.md

ALTER TABLE [org_sync_runs] ADD COLUMN [listed_at] TEXT;

UPDATE [org_sync_runs] SET [listed_at] = [started_at] WHERE [listed_at] IS NULL;
