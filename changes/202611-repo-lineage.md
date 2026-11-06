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
| `square/okhttp` ↔ `lysine-dev/okhttp` (all 6,466 commits) | same repo, transferred (both names return id `5152285`) | no (it's not a fork) |
| `facebook/react` ↔ `react/react` | same repo, transferred (id `10270250`) | no (it's not a fork) |
| `protonmail/go-crypto` ↔ `go.googlesource.com/crypto` | copy with its own work | no |
| `eslint-plugin-n` ↔ `mysticatea/eslint-plugin-node` | fork that became the successor | yes |
| `graphql-code-generator` ↔ `…-community` | split, history copied | no |
| `jackc/pgx` + 5 libraries | libraries merged into pgx, keeping their histories | no |
| `go.googlesource.com` `crypto`, `net`, `sys`, `tools` | the Go repos share an initial commit | no |

### What the GitHub API says about the same DB (2026-11-05)

All 365 github.com repos, looked up with 8 batched GraphQL queries (50 repos per
query, **1 rate-limit point each**):

- **9 forks.** Only one parent (`mysticatea/eslint-plugin-node`) is also in the DB.
- **10 repos renamed or transferred** since they were synced. Two now exist in the
  DB under both names (react, okhttp). The other 8 (`square/retrofit`,
  `imdario/mergo`, `ladjs/supertest`, …) are synced under their old name only,
  which still works because git and the API follow the redirect.
- **0 created from a template.** That's expected for a DB of open-source
  dependencies.
- `protonmail/go-crypto` is not marked as a fork, so only the shared root shows
  where it came from.

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
-- Provider identity, captured when first seen. These two columns land early,
-- in #335's migration 0012.
ALTER TABLE repos ADD COLUMN provider_repo_id TEXT;   -- e.g. github.com:10270250
ALTER TABLE repos ADD COLUMN provider_checked_at TEXT; -- last lookup, found or not

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

The `shares_root_with` row is a placeholder: how shared history should be stored,
and when it has a direction, is still being decided. See "Shared history: shapes
and direction" below. Root counts alone do show some direction: pgx has six
roots, and pgproto3's only root is one of them, so pgx took in pgproto3's
history and not the other way round.

## Where each piece is filled in

Every GitHub lookup uses **one GraphQL query per 50 repos** (`databaseId`,
`isFork`, `parent`, `templateRepository`, `nameWithOwner`), which costs 1
rate-limit point. That's about 100 points for a 5000-repo estate. REST would
need one `GET /repos/{owner}/{repo}` per repo for the same fields, because the
listing endpoint doesn't include `parent`, `source` or `template_repository`.
So fork, template and id detection are one lookup, never separate costs.

Ids are captured in four places:

- **`kgit clone`:** when the repo is first seen, which is the only moment a later
  rename can't confuse. Needs a token; without one, it's left for later.
- **`kgit sync --org` / `--user` (#335):** for each listed repo, store
  `provider_repo_id`, `fork_of` (with `source` in `detail`) and `template_from`.
  Today `get_repos()` fetches each fork's parent and then **drops it**.
- **`kgit lineage`:** a new command for the repos already in the DB (below).
- **`kgit pull`:** a lazy lookup for repos without an id (below).

Each lookup sets `repos.provider_checked_at`, whether or not it found anything,
so a repo isn't asked about again unless `-refresh` says so.

Everything else is offline:

- **Every sync (`sync_repo`):** record the repo's root commits in `repo_roots`.
  It's cheap: one `git rev-list` on a clone that's already present.
- **`kospex rebuild lineage`**, a new entry in the `TASKS` registry
  (`src/kospex_rebuild.py`):
  - fills in `repo_roots` for existing repos from their clones
  - derives the shared-history relations from `repo_roots` and `commits`
  - derives `same_repo_as` from matching `provider_repo_id`s

  It makes no network calls, which keeps rebuild DB-and-disk only.

### `kgit lineage`

Looks up provider identity for repos already in the DB, and reports what changed.

```
kgit lineage REPO_ID | --all | --org ORG_KEY | --server SERVER
             [-refresh] [-check] [-no-prompt] [-limit N]
```

- Scope arguments are the same as `kgit pull`.
- By default, only repos never looked up. `-refresh` re-checks the rest, which
  catches renames and transfers since the last check, and forks that were
  detached.
- `-check`: offline report from the DB only. It shows repos without an id,
  `same_repo_as` groups, forks with or without their parent in the DB, and
  non-GitHub repos skipped.
- A repo that no longer exists (deleted, or no access) is reported and gets
  `provider_checked_at` set with no id. It isn't an error for the run.
- Output: renamed or moved (old name → current name), duplicated in the DB (same
  id under two `repo_id`s, with a pointer to the merge runbook), new forks, new
  template origins.

### Lazy lookup in `kgit pull`

Before the pull loop, collect the in-scope GitHub repos that have no
`provider_checked_at`, and look them up in batches of 50. It's a single
step before the loop, not one request per repo. The gaps:

- **It needs a token, and `pull` doesn't today.** Pull works over SSH or a
  credential helper. Without a token, skip the lookup and print a count ("12
  repos have no GitHub id; run `kgit lineage`"). Never prompt.
- **Repos that are never pulled never get an id.** That includes archived repos,
  and anything synced with `sync-directory` from a clone made outside kgit.
- **`pull --check` must stay offline**, so it can only report missing ids.
- **Renames are only caught once.** A repo with an id isn't looked up again, so
  a later transfer goes unnoticed until `kgit lineage -refresh`. Catching it on
  every pull would mean a lookup on every pull.
- **A failed or rate-limited lookup must not fail the pull.** It's logged and
  retried next time.

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

The default is set per installation (decided 2026-11-05). For closed-source work,
such as kospex installed by a company over its own repos, the forks an org holds
are deliberate and probably worth syncing. For open-source surveys, they're
mostly noise.

- `KOSPEX_CLONE_FORKS_DEFAULT`, set in the environment or `~/kospex/kospex.env`.
  Default `false`.
- Per run, `-include-forks` and `-skip-forks` override it either way.
- Either way, every fork is recorded in the manifest and as a `fork_of` row.

## Out of scope

- **Using lineage in stats.** That covers developer and commit counts per org or
  estate that count each hash once, and crediting a fork's inherited commits to
  its parent. It's a separate change, and it depends on this data existing.
- Bitbucket and GitLab provider ids. The schema allows them (`provider_repo_id`
  is host-prefixed); GitHub comes first.

## Templates

The template a repo was created from is set when the repo is created and never
changes. So it's looked up **once per repo**, as part of the same batched query
that gets the id and fork fields. It costs nothing extra and needs no flag.

The lookup can come back with nothing when:

1. **The repo wasn't created from a template.** This is the normal case.
2. **The template is private and the token can't see it.** An example is a
   company template in one org, used to create a repo in another org that the
   token only has access to.
3. **The template has since been deleted.**
4. **The repo isn't on GitHub.** GitLab has project templates; Bitbucket has none.
5. **The "template" was a clone and push, not GitHub's template feature.** This is
   the re-pointed remote case. It keeps the template's history, so it shows up as
   shared history, not here.

**Not yet verified:** whether GitHub tells cases 2 and 3 apart from case 1. A
repository you can't see is normally `null` in GraphQL, the same as "no
template". If that's what happens, kospex can't record "created from a template
it can't see". It would record nothing, and say so in the doc for the relation.
To check, before building it:

- create a private template in one org, and a repo from it in a second org
- query the new repo with a token that can see only the second org, in both REST
  (`template_repository`) and GraphQL (`templateRepository`)
- delete the template and query again

If either API distinguishes it, record `template_from` with `related_repo_id`
NULL and `detail` saying why (`template not visible` / `template deleted`).

## Shared history: shapes and direction (for discussion)

Every pair of repos in the dev DB that shares a root commit, with the overlap
measured from `commits` (`a_only` and `b_only` are commits in one repo but not
the other):

| a | b | shared | a_only | b_only | roots (a / b) |
|---|---|---:|---:|---:|---|
| graphql-code-generator | …-community | 7,497 | 1,325 | 1,170 | 1 / 1 |
| lysine-dev/okhttp | square/okhttp | 6,466 | 0 | 0 | 2 / 2 |
| protonmail/go-crypto | go `crypto` | 912 | 345 | 466 | 1 / 1 |
| jackc/pgtype | jackc/pgx | 406 | 47 | 3,966 | 1 / 6 |
| eslint-plugin-n | eslint-plugin-node | 390 | 394 | 1 | 1 / 1 |
| jackc/pgconn | jackc/pgx | 350 | 24 | 4,022 | 1 / 6 |
| jackc/pgproto3 | jackc/pgx | 81 | 6 | 4,291 | 1 / 6 |
| jackc/chunkreader | jackc/pgx | 14 | 0 | 4,358 | 1 / 6 |
| jackc/pgio | jackc/pgx | 8 | 0 | 4,364 | 1 / 6 |
| go `sys` | go `tools` | 3 | 1,782 | 10,900 | 1 / 1 |
| 10 more pairs (okio and okhttp, and the Go repos) | | 1 | | | |

The pairs fall into four shapes:

| shape | test | examples | direction |
|---|---|---|---|
| **identical** | neither side has its own commits | okhttp under both names | none: the same history. Compare provider ids to tell a move from a mirror |
| **absorbed** | one side has other roots, and the shared root is the other side's only root | pgx has 6 roots, and each library's single root is one of them | known: pgx took in the library's history |
| **derived** | single root each, substantial overlap, both may continue | eslint-plugin-n, go-crypto, graphql split | unknown from git alone. The newer GitHub id (or `createdAt`) is the copy |
| **common start** | only the root, or a handful of commits | the Go repos (1 to 3 shared), okio and okhttp (1) | none, and the relation is weak |

The counts, not a heuristic, decide the first two shapes, and **11 of the 20
pairs are "common start"**. Those 11 are the pairs a plain undirected
`shares_root_with` row describes worst.

Decisions this needs:

1. **Store facts or labels?** Proposed: store the measured numbers per pair
   (`shared`, `a_only`, `b_only`, roots per side, last shared commit date) and
   derive the shape in a query. Then the thresholds can change without a rebuild.
   The alternative is storing only a `relation` label.
2. **Where "common start" ends.** The data has a clean gap: at most 3 shared
   commits for the weak pairs, then 8 or more for the real ones (and pgio's 8 are
   all of its history). Proposed rule: "common start" when shared commits are
   ≤ 1% of *both* repos. pgio is 100% of itself, so it stays "absorbed".
3. **Materialise every pair, or only strong ones?** One root shared by N repos is
   N(N−1)/2 pairs: 4 Go repos already give 6. Proposed: `repo_roots` already holds
   the group, so store pair rows only for shapes other than "common start".
4. **Direction for "derived"** needs the API (`createdAt`, or id order on one
   host). Proposed: record it only when the API gives it, as a separate
   `github_api` row, and leave the commit-history row undirected.
5. **Mirrors across hosts.** "Identical" across two hosts (say
   `go.googlesource.com/crypto` and `github.com/golang/crypto`) is a mirror.
   Provider ids can't see it, because ids are per host. Proposed:
   `mirror_of`, derived only from "identical" plus different hosts.
6. **The numbers are a snapshot.** A "derived" pair's `a_only` / `b_only` grow as
   both continue, so `rebuild lineage` recomputes them. A repo only enters a
   group once its roots are recorded.
7. **HEAD only.** `commits` holds the default branch (all-branches sync is
   deferred), so a fork whose work is on another branch looks smaller than it is.

## Gone repos (for discussion)

A repo kospex has synced can disappear from GitHub. GitHub keeps no public record
of a deleted repo, and a deleted repo looks the same as one the token can't see.
kospex often holds more than GitHub does: the clone has the full history, and the
DB has the commits and the run in which the repo was last listed. Related: #12
(repos removed upstream).

### What GitHub gives you

From GitHub's documentation. Not yet tested here; the deletion cases need a
throwaway repo.

- **The repo API answers 404**, by name (`/repos/{owner}/{repo}`) or by numeric
  id (`/repositories/{id}`). That's the same answer as for a private repo the
  token can't read, so a 404 alone can't say the repo was deleted.
- **A rename or transfer is different.** The old name redirects, and a lookup by
  numeric id returns the new owner and name. That's the okhttp case above: same
  id, new owner.
- **Restore:** the owner can restore a deleted repo for 90 days from the account
  or org settings. As far as I know there's no public API to list deleted repos.
- **The org audit log** records `repo.destroy`, with who deleted it and when. Org
  owners can see it in the web UI; the audit log API is Enterprise Cloud only.
- **Webhooks:** an org webhook receives a `repository` event with action
  `deleted`, but only if the hook existed before the deletion.
- **Forks:**
  - Deleting a public repo leaves its forks, and the fork network gets a new root.
  - Deleting a private repo deletes its private forks too.
  - So a deleted public repo's history may still be on GitHub through its forks.

### What kospex can work out

`kgit sync --org` / `--user` already lists repos that are in kospex but no longer
in the listing (`-inventory` and `-status`). Once a repo has its
`provider_repo_id`, looking up the id sorts them:

| What happened | How to tell |
|---|---|
| Renamed in the same org | the id is in the new listing under a different name |
| Transferred to another owner | the lookup by id returns the new owner |
| Deleted, or the token lost access | the lookup by id returns 404 |
| Made private and the token lost access | the same 404, so it can't be told apart |

So "deleted" is something kospex can only infer: a 404 by id, ideally seen
across more than one run. Only the audit log can confirm it, where that's
available.

### Decisions

1. **What it's called.** Suggest `gone (404)` rather than `deleted`, with the
   date it was last seen in a listing.
2. **What happens to the kospex data.** Suggest keeping the commits: a deleted
   repo's history is often exactly what someone wants to know about. Mark the
   repo as gone rather than deleting anything; if anything is ever deleted,
   `kreaper` does it on request.
3. **The local clone.** `kgit pull` will start failing on it. Should it skip the
   clone, or report it once and stop retrying?
4. **Repos with no `provider_repo_id`.** Ones synced before `0012` can only be
   matched by name, so a rename looks like a deletion until the id is filled in.
5. **The audit log.** Should kospex read `repo.destroy` where it's available, or
   leave that to the user?

## Open questions

1. **Shared-history decisions 1 to 7** above.
2. **Template visibility** needs the private-template check above before the
   relation is built.
3. **Gone repos:** decisions 1 to 5 above, and a live check of what GitHub
   returns for a deleted repo, by name and by id, using a throwaway account.
