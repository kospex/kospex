# Repo header redesign + new org view

**Date:** 2026-05-17
**Status:** Design / spec (no code yet)
**Type:** Web UI + new route

## Overview

The repo view (`/repo/{repo_id}`) currently renders the raw `repo_id`
(e.g. `github.com~kospex~kospex`) as its `<h1>`. That is not user friendly and
exposes the internal `~`-delimited id format.

This change replaces that header with a **breadcrumb + bold title + sub-label**
treatment and introduces a matching **organisation view** so the breadcrumb's
links lead somewhere real.

Header treatment (chosen after comparing four directions — stacked meta line,
breadcrumb, pills, metadata panel — see Design Rationale):

- Breadcrumb: `server / org / repo`
- Bold title: the entity's short name
- Quiet uppercase sub-label: `Repository` or `Organisation`

Example, repo `github.com~apache~kafka`:

```
github.com / apache / kafka      <- server + org are links, repo is current
kafka                            <- bold title
REPOSITORY                       <- sub-label
```

Organisation `github.com~apache`:

```
github.com / apache              <- server is a link, org is current
apache                           <- bold title
ORGANISATION                     <- sub-label
```

## Goals

- Replace the raw `repo_id` `<h1>` on the repo view with the breadcrumb header.
- Add a new `/org/{org_key}` page that mirrors the repo view's structure.
- Share one header component between both pages for visual consistency.
- Wire breadcrumb links to real destinations.

## Non-goals (explicit scope decisions)

- **Org-level email domains** — `KospexQuery.email_domains()` is repo-only
  (`repo_id`, `days`; no `org_key`). The org page omits the email-domains
  section. Future enhancement.
- **Org-level author summary** — `KospexQuery.author_summary()` is repo-only
  (`repo_id`). The org page omits the author-summary section now. *Future
  enhancement note:* an org-level author summary should aggregate the
  `developer_stats` table (`KospexSchema.TBL_DEVELOPER_STATS = "developer_stats"`,
  schema `kospex_schema.py:363`, written/queried on every sync at roughly
  `kospex_query.py:1839-1923`, keyed by `_repo_id`) across the org's repos.
- No dedicated server detail page — the Server breadcrumb segment links to the
  **existing** `/orgs/{server}` route, which already lists every org on a
  git server.
- No changes to repo-view body content (Commit Summary, tech radar, developer
  status, etc.) beyond replacing the header block.

## Design Rationale

Four header directions were mocked and compared (A stacked meta line,
B breadcrumb + title, C title + pills, D title + metadata panel).

Breadcrumb (B) was chosen because:

- The repo view's body is analytical (commit summary, key-person/developer
  status, tech landscape) — its primary audience skews toward eng managers /
  DevEx / leadership doing portfolio analysis.
- For a less technical viewer, the breadcrumb's "these are clickable, go up a
  level" affordance is far stronger than pills, which read as tags/filters.
- Every breadcrumb segment now resolves to a real page (org page is built
  here; `/orgs/{server}` already exists), so the breadcrumb doubles as the
  primary navigation spine between exactly the pages this change touches.
- It degrades gracefully to the org page (`server / org`, org as current).

The sub-label (`Repository` / `Organisation`) is kept to reinforce what kind
of entity the page represents.

## Components

### Shared header partial — `src/templates/_entity_header.html`

A Jinja2 partial rendering the breadcrumb + bold title + sub-label inside the
existing white card. It reads context variables supplied by the route. It is
an in-card block, distinct from the existing nav `_header.html` include.

Context variables consumed:

| Variable      | Repo page                  | Org page                 |
|---------------|----------------------------|--------------------------|
| `git_server`  | `github.com`               | `github.com`             |
| `org`         | `apache`                   | `apache`                 |
| `org_key`     | `github.com~apache`        | `github.com~apache`      |
| `repo`        | `kafka`                    | *(unset)*                |
| `entity_type` | `"Repository"`             | `"Organisation"`         |

Link rules:

- Server segment → `/orgs/{git_server}` (existing).
- Org segment → `/org/{org_key}` (new). On the repo page the org segment is a
  link; on the org page it is the current (plain) segment.
- Repo segment (repo page only) is the current (plain) segment.

### Repo view changes — `kweb2.py` `/repo/{repo_id}` + `repo_view.html`

- Handler: call `KospexUtils.parse_repo_id(repo_id)`. If it returns `None`
  (id is not 3 `~`-delimited parts), respond **HTTP 404** with a clear
  message instead of today's generic 500. On success add `git_server`,
  `org`, `repo`, `org_key`, `entity_type="Repository"` to the template
  context (existing context keys unchanged).
- Template: replace `repo_view.html` lines 39–41
  (`<h1 ...>{{ repo_id }}</h1>`) with
  `{% include '_entity_header.html' %}`. All content below ("Commit Summary"
  and onward) is untouched.

### New org view — `kweb2.py` `/org/{org_key}` + `src/templates/org_view.html`

New route `@app.get("/org/{org_key}", response_class=HTMLResponse)` →
`async def org_view(request, org_key)`.

- Parse `KospexUtils.parse_org_key(org_key)`. Malformed (not 2 parts) →
  **HTTP 404**.
- Data sources (all already support `org_key`):
  - **Commit Summary** table — `KospexQuery.commit_ranges(org_key=...)`
    (reuse the same Jinja table markup as repo_view).
  - **Tech landscape** radar — `KospexQuery.tech_landscape(org_key=...)`,
    top ~10 languages → `labels` / `datapoints` (same pattern as the repo
    handler).
  - **Developer status** — `KospexQuery.developers(org_key=...)` →
    `KospexUtils.repo_stats(developers, "last_commit")`.
  - **Repos in this organisation** table — `KospexQuery.repos(org_key=...)`
    with `KospexQuery.active_devs()` mapped onto each row by `_repo_id`;
    each row links to `/repo/{repo_id}`.
- Template `org_view.html`: `{% include '_entity_header.html' %}`
  (`entity_type="Organisation"`), then Commit Summary table, tech radar,
  developer status, and the repos table. Omits email-domains and
  author-summary sections (see Non-goals).

## Files changed

| File | Change |
|------|--------|
| `src/templates/_entity_header.html` | **New** — shared breadcrumb header partial |
| `src/templates/org_view.html` | **New** — org view, mirrors repo_view |
| `src/templates/repo_view.html` | Replace raw `<h1>{{ repo_id }}</h1>` (lines 39–41) with the partial |
| `src/kweb2.py` | `/repo/{repo_id}`: parse repo_id, 404 on malformed, pass header context. **New** `/org/{org_key}` route + handler |
| `tests/` | New/extended tests (see Testing) |

## Error handling

- Malformed `repo_id` (not 3 `~` parts) → HTTP 404, clear message.
- Malformed `org_key` (not 2 `~` parts) → HTTP 404, clear message.
- Unexpected errors keep the existing logged → HTTP 500 behaviour.

## Testing

Follow existing `tests/` pytest patterns:

- `/repo/{valid_repo_id}` → 200 and breadcrumb renders correct
  `/orgs/{server}` and `/org/{org_key}` hrefs.
- `/repo/{malformed_id}` → 404.
- `/org/{valid_org_key}` → 200; header renders `server / org` with the server
  link to `/orgs/{server}`; repos table rows link to `/repo/{repo_id}`.
- `/org/{malformed_org_key}` → 404.

## Future enhancements (out of scope here)

1. Org-level email domains — add `org_key` support to
   `KospexQuery.email_domains()` and surface on the org page.
2. Org-level author summary — aggregate `developer_stats`
   (`TBL_DEVELOPER_STATS`) across the org's repos and surface on the org page.
3. Optional: a dedicated server detail page (today the Server link reuses
   `/orgs/{server}`).
