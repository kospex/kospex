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

### Requirement (2026-11-07)

**Once kospex has seen a repo in a listing, it keeps the record that it saw it,
even after the repo is deleted.** The latest listing says what's on GitHub now;
the record says what used to be there. So someone can ask "which repos appear to
have been deleted?", for example: the org was synced a month ago, and three of
its repos have gone since.

### What the 0012 tables already answer

Every `kgit sync --org` / `--user` run saves everything the owner listed, skipped
forks and archived repos included (`org_sync_items`). So "seen" is the union of an
org's runs, "now" is its latest run, and a repo seen but not now is gone, unless
its `provider_repo_id` is in the latest run under another name, which makes it a
rename. Tested against the real `0012` schema with three listings of `acme`:
`api` renamed to `api-server`, `old-tool` and `demo` deleted.

```
github.com~acme~api        first 2026-10-01  last 2026-10-15  x2  renamed -> github.com~acme~api-server
github.com~acme~demo       first 2026-10-01  last 2026-10-15  x2  not listed (deleted, or no access)
github.com~acme~old-tool   first 2026-10-01  last 2026-10-15  x2  not listed (deleted, or no access)
```

```sql
WITH runs AS (
  SELECT run_id, started_at FROM org_sync_runs WHERE org_key = :org
),
latest AS (
  SELECT run_id FROM runs ORDER BY started_at DESC LIMIT 1
),
now_listed AS (
  SELECT _repo_id, provider_repo_id FROM org_sync_items
  WHERE run_id = (SELECT run_id FROM latest)
),
seen AS (
  SELECT i._repo_id, i.provider_repo_id,
         MIN(r.started_at) AS first_listed, MAX(r.started_at) AS last_listed,
         COUNT(*) AS times_listed
  FROM org_sync_items i JOIN runs r USING (run_id)
  GROUP BY i._repo_id
)
SELECT s._repo_id, s.first_listed, s.last_listed, s.times_listed,
       (SELECT n._repo_id FROM now_listed n
         WHERE n.provider_repo_id = s.provider_repo_id) AS now_named
FROM seen s
WHERE s._repo_id NOT IN (SELECT _repo_id FROM now_listed)
ORDER BY s.last_listed DESC, s._repo_id
```

The latest run counts even if its syncing isn't finished: a run is saved only
from a complete listing (#347), so its items are the whole org at that time.

### What's missing for the requirement

1. **The "seen" record must survive pruning.** Today it exists only as run
   history, and retention of `org_sync_runs` / `org_sync_items` is an open
   question; pruning would erase it. Proposed: a small permanent table keyed by
   `provider_repo_id` (first listed, last listed, the `_repo_id` it was last
   listed as, the owner it was listed under, its state, and when the state was
   last checked). Each listing updates it. Then the question is a lookup, runs
   can be pruned safely, and the per-id lookup result has somewhere to live.
2. **A run doesn't record what the token could see.** What a listing includes
   depends on the login, not just on whether there was a token: an org member
   sees its private repos, while a `-no-auth` run, or a token of someone outside
   the org, sees only the public ones. So a single run without a member's
   token makes every private repo look gone. For a public org it makes no
   difference: a non-member's token and `-no-auth` list the same repos.
   - **Record the login on each run**, empty for `-no-auth` (a login is not a
     secret). This needs a migration (`0013`) on `org_sync_runs`.
   - **Use `org_sync_items.private`, which 0012 already records.** If the
     latest run has no login, or a different one from the run that last saw
     the repo, a repo last seen as private is reported as **not visible to
     this run**, not as gone. A public repo that vanishes is gone whatever the
     login.
   - This is the one piece that affects `kgit sync --org` as shipped: until it
     lands, one run without a member's token makes the private repos look
     deleted.
3. **"Not listed" is not "deleted".** The per-id lookup below sorts not-listed
   repos into transferred (the id has a new owner) and appears deleted (404).
4. **Repos never listed.** A repo added with `kgit clone`, outside `sync --org`,
   has no listing history. Only the per-id check (`kgit lineage -check`) covers it.

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
  or org settings. There appears to be no public API to list deleted repos.
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
6. **Where "seen" lives.** The permanent table above, or keep every run's items
   forever? Suggest the table.
7. **Recording the login on a run** (`0013`): its own PR soon after
   `kgit sync --org` ships, or with the table in 6?

## History rewrites (for discussion)

Sometimes a repo's history is rewritten and force-pushed. The commonest reason
is removing a committed secret, using `git filter-repo` or BFG. Other reasons
are removing a large file, correcting author emails, or undoing commits at the
tip. Since #385, kospex's pull is fast-forward only, so a rewrite makes the
pull fail and the clone is left as it was. That is the point to record what
changed, before anything is replaced.

**Updating after a rewrite is not a rebase.** Rebasing the clone would replay
the old commits, secret included, on top of the cleaned history. A kospex clone
is a mirror, so the update is to replace it with the new history.

### What a secret scrub looks like (tested 2026-11-08)

The test used a remote with 5 commits. Bob added `.env` with a secret in commit
2. The remote was scrubbed with
`git filter-repo --invert-paths --path .env`, and then the kospex clone tried to
update. The script is in the appendix.

```
ff-only pull exit: 128              <- refused, so the clone is untouched
old head ancestor of new: False     <- a rewrite, not new commits
merge-base: commit 1                <- history before the secret is unchanged
  rewritten 582ab53 -> 2b2e5d9  alice  commit 5  dates kept: True  tree differs in: .env
  rewritten 361afef -> fdee746  alice  commit 4  dates kept: True  tree differs in: .env
  rewritten 65f1c03 -> 9becbc5  alice  commit 3  dates kept: True  tree differs in: .env
  REMOVED   7e949e3             bob    commit 2: .env
secret blob still in the clone's object store: True
```

filter-repo kept each commit's author, author date, committer date and message.
Only the tree, and therefore the hash, changed. The commit that only added
`.env` became empty, so filter-repo dropped it.

### Detecting it

After the fast-forward fails:

1. **`git fetch`.** This brings in the new history without moving the clone's
   branch.
2. **Ancestry.** If the old `HEAD` is an ancestor of the new tip, the pull failed
   for another reason, such as auth or network. If it isn't, history was
   rewritten. `git merge-base old new` gives the last commit the two histories
   share. If there is none, the rewrite started at the first commit or the repo
   was recreated.
3. **Commit map.** Compare `old ^new` (commits only the old history has) with
   `new ^old` (commits only the new one has). Match them on author email,
   author date and subject. The result is:
   - **rewritten:** old hash → new hash, plus the paths whose content differs
     between the two trees. Here that is `.env`, the scrubbed path.
   - **removed:** an old commit with no match. Here that is the commit that
     added the secret, with its author and date.
   - **added:** a new commit with no match.
4. **GitHub's record of the push.**
   `GET /repos/{owner}/{repo}/activity?activity_type=force_push` returns the
   ref, `before` and `after` hashes, the actor and a timestamp. It was checked
   against kospex/kospex and lists its force-pushes. How long GitHub keeps
   these hasn't been checked.

### Why it was rewritten

kospex can't know the intent. It can record what kind of rewrite the evidence
shows, plus the evidence itself. A person adds the reason.

| Evidence | Likely kind |
|---|---|
| Authors, dates and messages kept; some paths changed or gone in every later commit | **content scrub**: a secret (`.env`, `*.pem`, `id_rsa`, a config file) or a large file |
| Trees the same; author names or emails changed | identity rewrite |
| Only commits at the tip gone; nothing else changed | force-push that undid commits |
| No commits match | recreated repo, or a rewrite from the first commit (not yet tested) |

### Updating

1. **Record the rewrite and its commit map first.** This is the only moment
   both histories are available.
2. **Replace the clone, preferably by re-cloning.** A `reset --hard` to the new
   tip also works, but the old objects stay in the clone until the reflog
   expires and git garbage-collects them. The test's last line shows the
   secret is still readable. After a secret scrub, the clone on disk is the
   real exposure. The DB holds commit metadata, not file contents.
3. **Re-sync the repo in full.** The rewritten commits have new hashes but keep
   their old dates. The incremental sync reads `git log --since=<latest
   committer date>`, so it would miss every one of them.
4. **Keep the old commit rows, marked.** A rewritten commit is linked to its new
   hash, and a removed one is marked removed, with the rewrite it was removed
   in. This follows the gone-repos requirement: what kospex has seen stays
   recorded.

### Storage (proposed)

- `repo_rewrites`, one row per detected rewrite. Columns:
  - `_repo_id`
  - `detected_at`
  - `old_head`, `new_head`, `merge_base`
  - `kind`
  - the rewritten, removed and added counts
  - the GitHub `actor` and `pushed_at`, where available
  - `reason`, empty until a person fills it in
- `repo_rewrite_commits`, one row per old commit. Columns:
  - `rewrite_id`
  - `old_hash`
  - `new_hash` (empty when the commit was removed)
  - `author_email`, `author_when`
  - `changed_paths`

Both tables need a migration. They are separate from `0013`, which records the
login on each sync run.

### Decisions

1. **Automatic or manual.** Suggest: detect, record and stop. `kgit sync --org`
   / `kgit pull` report the repo as `history rewritten`, and an explicit step
   accepts the rewrite and re-clones. A secret scrub is exactly the case where
   a person should look before kospex moves on.
2. **Removed commits' metadata.** Rows hold hashes, authors, dates and messages,
   not file contents, so keeping them doesn't keep the secret. The exception is
   a secret in a commit message. Keep them, or let the accept step drop the
   messages of removed commits?
3. **Changed paths.** Record the paths a rewrite changed (`.env` above)? They
   say where a secret was, not what it was.
4. **The GitHub activity API.** Look up the force-push when a rewrite is
   detected, or leave it to the user? It needs read access to the repo.
5. **Untested cases.** These need a run each before the matching is relied on:
   - a rewrite from the first commit
   - BFG `--replace-text` (the file stays, its content changes)
   - an identity rewrite with `.mailmap`, or filter-repo `--mailmap`
   - a rewrite of a branch other than the default

## Open questions

1. **Shared-history decisions 1 to 7** above.
2. **Template visibility** needs the private-template check above before the
   relation is built.
3. **Gone repos:** decisions 1 to 7 above, and a live check of what GitHub
   returns for a deleted repo, by name and by id, using a throwaway account.
4. **History rewrites:** decisions 1 to 5 above.

## Appendix: history rewrite test script

Run with `python3 -I scrub_test.py`. It needs `git`, and uses `git filter-repo`
if it is installed, `git filter-branch` otherwise. It writes only under a
`scrub/` directory next to the script, and uses its own git config, so the
user's settings don't affect it.

```python
"""What does a secret-scrubbing rewrite look like from a kospex clone?
Remote: 5 commits, a secret file added in commit 2. The remote is scrubbed with
filter-repo (filter-branch if filter-repo isn't installed). Then the clone
fetches and the two histories are compared."""
import os, shutil, subprocess
from pathlib import Path

D = Path(__file__).parent / "scrub"
shutil.rmtree(D, ignore_errors=True); D.mkdir()
ENV = {**os.environ, "GIT_CONFIG_GLOBAL": str(D / "gc"), "GIT_CONFIG_NOSYSTEM": "1",
       "FILTER_BRANCH_SQUELCH_WARNING": "1"}
(D / "gc").write_text("[user]\n\tname = Alice\n\temail = alice@example.com\n"
                      "[init]\n\tdefaultBranch = main\n")

def g(*a, cwd, ok=True, env=None):
    p = subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True,
                       env={**ENV, **(env or {})})
    if ok and p.returncode:
        raise SystemExit(f"git {a} failed: {p.stderr}")
    return p.stdout.strip()

remote = D / "remote"; remote.mkdir()
g("init", "-q", cwd=remote)
files = [("README.md", "hello"), (".env", "AWS_SECRET=abc123"), ("app.py", "print(1)"),
         ("app.py", "print(2)"), ("lib.py", "x=1")]
for i, (f, body) in enumerate(files, 1):
    (remote / f).write_text(body + "\n")
    g("add", f, cwd=remote)
    who = {"GIT_AUTHOR_NAME": "Bob", "GIT_AUTHOR_EMAIL": "bob@example.com"} if i == 2 else {}
    g("commit", "-q", "-m", f"commit {i}: {f}", cwd=remote,
      env={**who, "GIT_AUTHOR_DATE": f"2026-01-0{i}T10:00:00Z",
           "GIT_COMMITTER_DATE": f"2026-01-0{i}T10:00:00Z"})

clone = D / "clone"
g("clone", "-q", str(remote), str(clone), cwd=D)
old_head = g("rev-parse", "HEAD", cwd=clone)

have_fr = subprocess.run(["git", "filter-repo", "--version"], capture_output=True).returncode == 0
if have_fr:
    g("filter-repo", "--force", "--invert-paths", "--path", ".env", cwd=remote)
else:
    g("filter-branch", "-f", "--index-filter", "git rm -q --cached --ignore-unmatch .env",
      "--prune-empty", "HEAD", cwd=remote)
print("rewrite tool:", "filter-repo" if have_fr else "filter-branch")

pull = subprocess.run(["git", "-C", str(clone), "pull", "--ff-only", "--no-rebase"],
                      capture_output=True, env=ENV)
print("ff-only pull exit:", pull.returncode)
g("fetch", "-q", "origin", cwd=clone)
new_head = g("rev-parse", "origin/main", cwd=clone)
is_anc = subprocess.run(["git", "-C", str(clone), "merge-base", "--is-ancestor",
                         old_head, new_head], env=ENV).returncode == 0
mb = g("merge-base", old_head, new_head, cwd=clone, ok=False)
print("old head ancestor of new:", is_anc, "| merge-base:", mb[:7] or "(none)")

FMT = "%H%x09%ae%x09%aI%x09%cI%x09%s"
def commits(*rng):
    out = g("log", "--format=" + FMT, *rng, cwd=clone)
    return [dict(zip(("hash", "ae", "ad", "cd", "subj"), line.split("\t")))
            for line in out.splitlines() if line]

only_old = commits(old_head, "^" + new_head)
only_new = commits(new_head, "^" + old_head)
key = lambda c: (c["ae"], c["ad"], c["subj"])
new_by_key = {key(c): c for c in only_new}
print(f"\nold-only commits: {len(only_old)}, new-only commits: {len(only_new)}")
for c in only_old:
    m = new_by_key.get(key(c))
    if m:
        paths = g("diff", "--name-only", c["hash"], m["hash"], cwd=clone)
        print(f"  rewritten {c['hash'][:7]} -> {m['hash'][:7]}  {c['ae']:18} {c['subj']:22}"
              f" committer date kept: {c['cd'] == m['cd']}  tree differs in: {paths or '-'}")
    else:
        print(f"  REMOVED   {c['hash'][:7]}             {c['ae']:18} {c['subj']}")
print("secret blob still in clone's object store:",
      subprocess.run(["git", "-C", str(clone), "cat-file", "-e", f"{old_head}:.env"],
                     env=ENV).returncode == 0)
```
