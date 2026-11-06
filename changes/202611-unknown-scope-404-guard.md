# kweb: unknown-scope 404 — route inventory guard and query-parameter ids

Status: **planned**, not started. Tracked in #369. Follow-up to #206 / #367 / #368.

## Background

A kweb page given a repository, organisation or server id that isn't in the
database used to render an empty page with HTTP 200. That looks the same as a
real scope with no data, when the id is most likely a typo or a stale link.

- #367 fixed `/tenure/` and added `KospexQuery.scope_exists(params)`.
- #368 added `_require_known_scope(params, id)` in `kweb2.py` and called it in
  24 HTML routes that take the id **in the path**, so an unknown id raises a 404
  naming it.

Two gaps remain.

## 1. Nothing enforces the check on new routes

`_require_known_scope` is one call in each handler. A new route with an
`{id}`, `{repo_id}`, `{org_key}` or `{server}` path parameter can leave it
out, and nothing fails.

**Change:** a test that walks `kweb2.app.routes`, collects every route whose
path has one of those parameters, and fails unless the route is either:

- in the table driving `tests/test_unknown_id_404.py` (so it has an
  unknown-id → 404 test), or
- in an explicit exemption list, each entry with a one-line reason.

Measured on the #368 code: 31 routes have an id-like path parameter. 24 are
checked. The other 7 would start as exemptions:

| Route | Reason |
|---|---|
| `/help/{id}` | id is a help page name, not a scope |
| `/collab/{repo_id}` | has its own 404 checks (#43) |
| `/api/collab/graph/{repo_id}` | JSON; the 404 handler renders HTML |
| `/org-graph/{org_key}` | JSON, as above |
| `/org-graph/{focus}/{org_key}` | JSON, as above |
| `/developer/{id}` | developer (base64 email) id, not a repos-table scope |
| `/dependencies/{id}` | already explains "Repository not synced" and what to run |

A FastAPI dependency was considered instead. It still has to be added to
each route by hand, so on its own it enforces nothing. The inventory test is
what makes a missed route fail CI.

## 2. Scope ids passed as query parameters aren't checked

`_require_known_scope` only sees the path id. Several routes also take the
scope from the query string, and an unknown value there still renders an
empty page with 200. Measured on the #368 code, with a one-repo database:

| Request | Known id | Unknown id |
|---|---|---|
| `/repos/?org_key=` | 200 | **200** |
| `/repos/?server=` | 200 | **200** |
| `/landscape/?repo_id=` | 200 | **200** |
| `/landscape/?org_key=` | 200 | **200** |
| `/developers/?org_key=` | 200 | **200** |
| `/graph/?repo_id=` | 200 | **200** |
| `/observations/?repo_id=` | 200 | **200** |
| `/tag/{tag}?repo_id=` | 200 | **200** |
| `/commits/?repo_id=` | 200 | 404 (already covered) |

`/commits/?repo_id=` is already covered because on the `/commits/` route the
handler's `repo_id` argument binds from the query string, so #368's check sees
it. `/tech/{tech}?repo_id=` reads the same parameter but wasn't measured (it
needs a technology present in the test database).

**Change:** check the scope the handler actually uses, after it has merged
the path id with `request.query_params`. For example, `/repos/` and
`/landscape/` compute `repo_id`, `org_key` and `server` from both. Either:

- call `_require_known_scope({key: value}, value)` on that merged value; or
- give `_require_known_scope` a `request` argument, and let it also read
  `repo_id` / `org_key` / `server` from the query string.

Prefer the first. It checks exactly the value the query will use, rather than
a parameter the handler might ignore.

## Tests

- **Route inventory test (item 1):** a new test route with an `{id}` and
  without a check or exemption makes it fail. Verify this once by hand,
  temporarily.
- **Query-parameter cases (item 2):** add each row of the item 2 table to the
  unknown-id table: unknown value → 404 naming it, known value → 200.
- A false-404 sweep, as done for #368: on a populated database, every
  `repo_id`, every `org_key` as `orgs()` links it, and every server passes
  through each newly checked query parameter. Include `~~` (nested group /
  empty owner) ids.

## Out of scope

- **Developer ids:** `/developer/{id}`, `?author_email=`, and base64 ids on
  shared pages. "Unknown developer" needs its own definition (no commits by
  that email, under which identity mapping), so it is a separate decision.
- **A JSON 404 body for the JSON routes.** That is a small change to the 404
  handler, keyed on `response_class` or the `Accept` header, and is a separate
  issue.
