-- 0008_osi_runs.sql
--
-- Per-run, per-repository diagnostics for `krunner osi -next`.
--
-- The run's log lines say what happened once. They cannot answer the questions
-- that matter over time, because log rotation discards them:
--
--   What N actually fits my interval?  Per-repo cost on a real estate spans four
--                                     orders of magnitude -- 2ms for a repo with
--                                     no dependency files against 109s for a
--                                     monorepo with 215 of them and 1101
--                                     packages. That cannot be reasoned about,
--                                     only measured.
--   Is this repository getting slower?
--   Is the response cache working?     lookups counts deps.dev requests actually
--                                     made, separately from packages written, so
--                                     cache effectiveness is lookups/packages
--                                     tracked over time.
--   Is the schedule completing passes, or quietly falling behind?
--
-- Why not observations: its primary key includes `latest`, so it holds one
-- current row per key and the demote-then-insert pattern raises on the third
-- write. It cannot store history. This table is keyed on (run_id, _repo_id) so
-- history accumulates.
--
-- Growth: one row per repository per run. A daily pass over several thousand
-- repositories is order a million rows a year -- small for SQLite, but not
-- nothing, and pruning is a deliberate open question in the design doc rather
-- than something to discover later.

CREATE TABLE IF NOT EXISTS [osi_runs] (
    [run_id] TEXT,          -- one per -next invocation, UTC timestamp
    [_repo_id] TEXT,
    [started_at] TEXT,      -- UTC, when this repository began
    [duration_ms] INTEGER,  -- how long this repository took
    [files] INTEGER,        -- dependency-tagged files examined
    [packages] INTEGER,     -- dependency rows written
    [lookups] INTEGER,      -- deps.dev requests actually made (cache misses)
    [outcome] TEXT,         -- headline outcome, see kospex.osi_outcomes
    [_git_server] TEXT,
    [_git_owner] TEXT,
    [_git_repo] TEXT,
    PRIMARY KEY(run_id, _repo_id)
    );

-- Per-repository history over time, the "is this getting slower" question.
-- run_id leads the primary key, so a repo-scoped query has nothing to seek
-- without this.
CREATE INDEX IF NOT EXISTS idx_osi_runs_repo ON osi_runs (_repo_id, run_id);
