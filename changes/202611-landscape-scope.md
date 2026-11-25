# Landscape and tech pages: scope, breadcrumbs and top-N

## Overview
`/landscape/` was unscoped in its chrome (no breadcrumb), its per-technology links went
estate-wide, and its Download link rendered `/landscape/None` when scoped by
`?repo_id=`. The Organisation page's Tech links had the same estate-wide problem.

## Files changed
- `src/kweb2.py` - `landscape()` builds the breadcrumb, scoped links and download URL;
  `repo_with_tech()` accepts `org_key` (a `repo_id` wins).
- `src/kospex_query.py` - `repos_with_tech`, `repos_without_tech`,
  `repos_with_file_metadata`, `count_repos_with_file_metadata` take `org_key`.
- `src/templates/landscape.html` - breadcrumb, scope label, fixed-height chart, linked
  Top-N selectors, status line, "entries per page".
- `src/templates/repos-tech.html` - breadcrumb for the org-scoped list.
- `src/templates/org_view.html` - Tech links carry `?org_key=`.
- `/tag/{tag}` takes `?org_key=` like `/tech/` (`repos_with_tag` / `repos_without_tag`).
- `/developers/` takes `?repo_id=` / `?org_key=` (or a path id) and its Download,
  View More and active-90-days links keep the scope (and the `days` window on Download).
  `KospexQuery.authors()` gained `repo_id`.
- Committers view (`/developers/?view=committers`): `KospexQuery.committers()` shares a
  `_commit_people()` helper with `authors()`. The detector behind the "bot" label is
  `EmailAnalyzer`, shared with `krunner key-person` and `orphans`, so the label agrees
  with them. It misses Gerrit, TeamCity, copybara, the Android automergers, JetBrains'
  noreply and similar; widening it would change those commands' output too, so it is a
  separate change.
- `tests/test_landscape_scope.py`.

## Usage
- `/landscape/?org_key=github.com~acme`, `/landscape/?repo_id=github.com~acme~one`
- `/tech/Python?org_key=github.com~acme` (add `&absent=true` for repos without it)

## Notes
- Top-N is client-side: rows carry `data-rank` from the server's count ordering, so a
  re-sort in DataTables does not change which rows are in the top N.
- `/tag/{tag}?repo_id=` already listed that tag's files in one repo.
- Developers page breadcrumb and scope label (follow-up): `developers.html` includes the
  shared breadcrumb above the stat cards, and the scope label under its existing heading,
  for a repo or org scope only.
