# Repo lineage — forks, copies, moves and shared history

Design doc for [#348](https://github.com/kospex/kospex/issues/348). No code yet.

kospex treats every repo as independent. Many aren't: a fork, a private copy, a
split, a moved repo, or a repo created from a template all share history or
identity with another repo. Without recording that, kospex can't tell an org's own
work from history it inherited, and a moved repo is synced twice under two
`repo_id`s.

This doc covers **recording lineage so it's correct and queryable**. Using it in
stats (counting each commit once, crediting shared commits to the original) is a
separate, later change.

Related: #335 (`kgit sync --org`, which records the GitHub side and skips forks by
default), #12 (repos removed upstream).

## Measured on a dev DB (376 repos, 1.42M commit rows)

- **77,148 commit rows (5.4%)** have a hash that also appears in another repo,
  across 24 repos.
- **10 root commits are shared across 18 repos.** A root commit has no parents,
  and the same root hash in two repos means shared ancestry, whatever GitHub says.
- `facebook/react` and `react/react` share 21,577 commits. They are one repo:
  `GET /repos/facebook/react` returns `301 → /repositories/10270250`, and
  `react/react` has id `10270250`, created 2013. It was transferred, and then
  synced again under its new name.

| shared history | what it is | GitHub flags it? |
|---|---|---|
| `lysine-dev/okhttp` ↔ `square/okhttp` (all 6,466 commits) | fork, no commits of its own | yes |
| `protonmail/go-crypto` ↔ `golang` `x/crypto` | fork with its own work | yes |
| `eslint-plugin-n` ↔ `mysticatea/eslint-plugin-node` | fork that became the successor | yes |
| `graphql-code-generator` ↔ `…-community` | split, history copied | no |
| `jackc/pgx` + 5 libraries | libraries merged into pgx, keeping their histories | no |
| `golang` `x/crypto`, `x/net`, `x/sys`, `x/tools` | the Go repos share an initial commit | no |
| `facebook/react` ↔ `react/react` | same repo, moved | no (it's not a fork) |

## Relations and how each is detected

The evidence comes from two sources, and between them they cover each other's gaps:

| relation | GitHub API | commit history |
|---|---|---|
| `fork_of`: fork (public, or private inside an org) | `fork`, `parent`, `source` | shared root |
| **private copy / re-pointed remote** | invisible | **shared root** |
| detached fork | the API stops calling it a fork | shared root |
| split, or history merged in | invisible | shared root (direction unknown) |
| `template_from`: created from a template repo | `template_repository` | invisible (fresh history) |
| `same_repo_as`: renamed or transferred | same numeric `id` | shared root |

The private copy matters most for closed-source work. **A fork of a public repo
is always public.** Anyone wanting a private copy of public code has to duplicate
it (clone, then push to a new private repo), and GitHub doesn't mark that as a
fork. Only the shared root shows it.

### Provider ids

GitHub's numeric repo `id` (and `node_id`) belongs to the repository, not its
name, and survives renames and transfers. So:

- **Same id, different name** means renamed or transferred. Compare the names to
  tell which: a changed owner is a transfer, a changed repo name is a rename.
  GitHub keeps no list of previous names in the API; old names only redirect.
- **Old-name redirects aren't permanent.** If someone creates a new repo at the
  old name, it takes over the name with a different id. Names can be reused;
  only the id identifies the repo. **Store the id when kospex first sees a
  repo**, or this can't be detected later.
- **Different id, shared root** means a different repository with shared history:
  a fork, a copy, or a delete-and-recreate.
- Ids are per host (github.com ≠ GitHub Enterprise). GitLab project ids and
  Bitbucket UUIDs behave the same way on their own hosts.

## Storage

Three pieces. Every table and column goes in a migration only, never in
`kospex_schema.py` (see CLAUDE.md). Use the next free number when this lands,
since #335's `0012` may come first.

```sql
-- Provider identity, captured when first seen.
ALTER TABLE repos ADD COLUMN provider_repo_id TEXT;   -- e.g. github.com:10270250

-- Root commits per repo, from `git rev-list --max-parents=0 HEAD` on the clone.
-- Read from git, not from commits.parents: that is a count, and 109 repos in
-- the dev DB were synced before it was populated (NULL).
CREATE TABLE IF NOT EXISTS [repo_roots] (
    [_repo_id] TEXT,
    [hash] TEXT,
    [recorded_at] TEXT,
    PRIMARY KEY(_repo_id, hash)
    );
CREATE INDEX IF NOT EXISTS idx_repo_roots_hash ON repo_roots (hash);

-- One row per relation per piece of evidence. Many-to-many: the pgx group and
-- the Go repos are one-to-several.
CREATE TABLE IF NOT EXISTS [repo_lineage] (
    [_repo_id] TEXT,
    [related_repo_id] TEXT,   -- via generate_repo_id(); the related repo may never be synced
    [related_url] TEXT,       -- as GitHub reports it, for a repo not in kospex
    [relation] TEXT,          -- fork_of | template_from | same_repo_as | shares_root_with
    [evidence] TEXT,          -- github_api | commit_history
    [detail] TEXT,            -- root hash, or provider ids / fork source
    [detected_at] TEXT,
    PRIMARY KEY(_repo_id, related_repo_id, relation, evidence)
    );
```

**Why a table, not columns on `repos`.** A repo can have several relations of
several kinds, and from more than one source of evidence at once. A
`repos.parent` column can't hold the pgx group, or a fork that is also
`same_repo_as` an old name.

`shares_root_with` is **symmetric and undirected**. pgx contains pgproto3's
history, and so does pgproto3. Which one absorbed the other isn't knowable from
roots alone. The relation records that the history is shared, nothing more.

## Where each piece is filled in

- **`kgit sync --org` / `--user` (#335):** for each listed repo, store
  `provider_repo_id`, plus `fork_of` (from `parent`, with `source` in `detail`) and
  `template_from` rows. The listing endpoint doesn't include `parent`,
  `source` or `template_repository`, so each needs a `GET /repos/{owner}/{repo}`.
  Make that request only for forks. Template detection would mean one request per
  repo, so only make it when asked (open question 3). Today `get_repos()` fetches
  each fork's parent and then **drops it**, so that cost is already being paid
  for nothing.
- **Every sync (`sync_repo`):** record the repo's root commits in `repo_roots`.
  It's cheap: one `git rev-list` on a clone that's already present.
- **`kospex rebuild lineage`**, a new entry in the `TASKS` registry
  (`src/kospex_rebuild.py`):
  - fills in `repo_roots` for existing repos from their clones
  - derives `shares_root_with` from `repo_roots`
  - derives `same_repo_as` from matching `provider_repo_id`s

  It makes no network calls, which keeps rebuild DB-and-disk only.
- **Moved repos already in the DB** (the react case): detected by matching
  `provider_repo_id`, which needs one API lookup per GitHub repo that doesn't
  have an id yet. `sync --org` gets the ids for free for an org it lists. For
  everything else, see open question 2.

**Merging two `repo_id`s of one repo is not automatic.** Someone has to decide
which name survives, and data from both has to be combined. It should be a
documented runbook, as `docs/refreshing-data.md` does for `repo_id` case
normalisation. Detection reports it; a person resolves it.

## Fork policy for `kgit sync --org` / `--user`

Decided for #335: **forks are skipped by default, but always tracked.** Each
fork is a manifest item with status `skipped` and reason `fork`, plus a
`fork_of` lineage row with its parent and source. `-include-forks` syncs them.

The reasoning:
- **User accounts fork a lot.** Cloning every fork a user has is mostly cloning
  other people's projects.
- **A fork brings its parent's whole history with it**, so syncing one makes
  upstream contributors look like the org's developers until the analysis
  de-duplicates.
- **Knowing an org *has* a fork is still useful**, especially a fork it has
  patched and depends on. So it's never silently dropped.

This default may change. For closed-source work (a company's own repos), the
forks an org holds are deliberate and probably worth syncing. For open-source
surveys, they're mostly noise. If it changes, it should probably be a config
default rather than a per-run flag (open question 1).

## Out of scope

- **Using lineage in stats.** That covers developer and commit counts per org or
  estate that count each hash once, and crediting a fork's inherited commits to
  its parent. It's a separate change, and it depends on this data existing.
- Bitbucket and GitLab provider ids. The schema allows them (`provider_repo_id`
  is host-prefixed); GitHub comes first.

## Open questions

1. **Fork default per engagement:** keep it a flag, or add a config setting so
   closed-source work can sync forks by default?
2. **Ids for repos that aren't in a listed org:** a `kgit github -lineage`
   command, a lazy lookup in `kgit pull`, or only what `sync --org` provides?
3. **Template detection** costs one API request per repo. Opt-in flag, or skip
   until someone needs it?
4. **Direction for `shares_root_with`:** the root's author date, or which repo's
   history the shared commits make up a larger share of, could suggest a
   direction. Worth a heuristic, or leave it undirected?
