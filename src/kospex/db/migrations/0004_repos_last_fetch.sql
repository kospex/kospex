-- 0004_repos_last_fetch.sql
--
-- Track when kospex last refreshed a repo's local clone from its remote
-- (git clone or git pull). Distinct from last_sync (when kospex last read the
-- clone into the DB) and last_seen (the newest commit date). NULL means never
-- recorded and is surfaced as "never" in kgit pull --check.

ALTER TABLE repos ADD COLUMN last_fetch TEXT;
