-- 0014_org_sync_visibility.sql
--
-- Each listed repository's visibility, as GitHub reports it: public, private
-- or internal.
--
-- Until now an item had only `private`, set when GitHub said "private". An
-- org owned by a GitHub Enterprise account can also have internal
-- repositories, readable by every member of the enterprise but not by the
-- public, and those were recorded with private = 0, the same as public ones.
--
--   org_sync_items.visibility   public | private | internal, as sent.
--                               NULL = not recorded (listed before this).
--   org_sync_listings.internal  internal repositories in that listing.
--                               NULL = listed before this.
--
-- `private` keeps its meaning (GitHub said "private"), so existing rows stay
-- correct. The backfill sets visibility only where it is known: private = 1.
-- Everything else stays NULL until the org is listed again, which
-- `kgit sync --org OWNER -inventory -refresh` does without cloning.
--
-- Design: changes/202611-org-sync-visibility.md

ALTER TABLE [org_sync_items] ADD COLUMN [visibility] TEXT;

ALTER TABLE [org_sync_listings] ADD COLUMN [internal] INTEGER;

UPDATE [org_sync_items] SET [visibility] = 'private' WHERE [private] = 1;
