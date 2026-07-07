-- 0003_repos_sync_provenance.sql
--
-- Provenance of the last successful file_metadata sync, per repo, so the
-- skip-guard can decide whether a rebuild is needed without inspecting
-- file_metadata rows. Rebuild when the repo HEAD moved OR panopticas/scc
-- changed version since the values recorded here.
--
-- Existing rows get NULL ("unknown / never recorded"), which the guard treats
-- as "must rebuild" — so the first sync after this migration always rebuilds.
--
-- Single-branch assumption: last_sync_hash is the HEAD of the checked-out
-- (default) branch that file_metadata was snapshotted from. This holds while
-- sync ingests only the default branch. When we move to syncing all branches
-- (git log --all), file_metadata stays a default-branch snapshot, so this
-- column still means "default-branch HEAD" — but per-branch provenance (if
-- needed then) is a separate future migration.

ALTER TABLE repos ADD COLUMN last_sync_hash TEXT;
ALTER TABLE repos ADD COLUMN last_panopticas_version TEXT;
ALTER TABLE repos ADD COLUMN last_scc_version TEXT;
