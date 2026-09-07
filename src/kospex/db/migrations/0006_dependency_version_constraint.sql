-- 0006_dependency_version_constraint.sql
--
-- How a dependency is constrained, what its advisory data refers to, and when
-- that data was last checked.
--
-- version_kind      normalised classification: pinned, commit, caret, tilde,
--                   gte, bounded, latest, workspace, link, catalog, alias,
--                   patch, none. Queryable across ecosystems.
-- version_operator  the raw declared operator, verbatim ("^", ">=", ">=,<",
--                   "workspace:"). Empty for a bare pin — the manifest wrote
--                   no operator and version_kind carries the meaning.
-- resolved_version  the version actually sent to deps.dev. For a range bounded
--                   below, this is the floor. For one bounded only above
--                   (e.g. `<4`) or excluded (`!=1.0`), it is that
--                   ceiling/excluded version instead — either way
--                   advisories/versions_behind describe the worst case the
--                   constraint permits, not what is installed. Empty when
--                   nothing resolved: commit, latest, none, or a pnpm-lock
--                   transitive entry, where `assess()` deliberately skips
--                   the lookup.
-- last_checked      UTC timestamp, written on EVERY save. Deliberately not a
--                   column DEFAULT: created_at is DEFAULT CURRENT_TIMESTAMP and
--                   therefore never updates, so a row refreshed from deps.dev
--                   still reads its original insert date. That is the defect
--                   this column exists to avoid.
--
-- NULL on existing rows until re-sync. A staleness indicator must render NULL
-- as "never checked", not "checked long ago".

ALTER TABLE dependency_data ADD COLUMN version_kind TEXT;
ALTER TABLE dependency_data ADD COLUMN version_operator TEXT;
ALTER TABLE dependency_data ADD COLUMN resolved_version TEXT;
ALTER TABLE dependency_data ADD COLUMN last_checked TEXT;
