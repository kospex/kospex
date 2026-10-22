# kweb: scoped summary cache, cached /repos, handlers off the event loop

## Overview

`/` and `/repos/` were slow on a large DB (measured on a 1.1M-commit, 374-repo
estate):

| Query | Time | Used by |
|---|---|---|
| `KospexQuery.developers()` | ~5.0s | `/` (cached), `/repos/` (not cached) |
| `KospexQuery.repos()` | ~2.2s | `/` and `/repos/`, never cached |
| `KospexQuery.active_devs()` | ~1.1s | `/repos/`, not cached |

All three scan the full `commits` table with `GROUP BY` / `COUNT(DISTINCT)`.
`/` took ~1.7s with a warm cache, and `/repos/` ~7s on every load.

This change fixes three things:

1. **Cache key bug.** The developer list was cached under the fixed key
   `summary:developers`, whatever the scope. `/summary/<org_key>` showed the
   org's repo count with the estate-wide developer count (3 repos, 72,649
   developers). Whichever scope loaded first filled the cache for all the
   others, and `/summary2/` shared the key.
2. **More caching.** `repos()` on `/` and `/summary2/`, and `repos()`,
   `developers()` and `active_devs()` on `/repos/`, are now cached.
3. **Handlers moved off the event loop.** Every route was `async def` while
   making blocking SQLite (and, for package-check, deps.dev) calls. A single
   slow page held the event loop, so every other request queued behind it.

## Files changed

- `src/kospex_request_cache.py`
  - `RequestCache.key(name, **scope)`: unset params are dropped, and the key
    does not depend on argument order.
  - Entries hold an aware `datetime`. `last_sync` is parsed and compared as a
    time, not a string, so a sync recorded in another UTC offset is ordered
    correctly. An unparseable `last_sync` is a miss.
  - `max_age_seconds` (default 3600): statuses and `days_ago` are relative to
    when they were computed, so an entry ages out even with no sync.
  - `set(..., cache_time=)`: callers pass the time the query *started*, so a
    sync that lands while the query runs still invalidates the result.
  - An empty result (`[]`) is a hit. The old `if data := ...` treated it as a
    miss and recomputed.
- `src/kweb2.py`
  - `cached_query(name, compute, **scope)` wraps the get / compute / set steps.
    `/`, `/summary/`, `/summary2/`, `/repos/` and `/metadata/` use it.
  - `/repos/` copies the cached `repos()` rows before adding `active_devs` and
    `years_active` to them.
  - Every route handler is now `def`. The exceptions are `lifespan` and the two
    exception handlers, which await Starlette's handlers and do no DB work.
    `package_check_upload` reads the upload with `file.file.read()`.
- `src/api_routes.py`: all handlers are now `def`.
- `src/kweb_graph_service.py`: `GraphService.query` is a property that returns
  a fresh `KospexQuery`. kweb holds one `GraphService` for the whole process,
  and a sqlite3 connection opened at import can only be used on that thread, so
  it fails on any worker thread.
- Tests:
  - New: `tests/test_request_cache.py` and `tests/test_kweb_threadpool.py`
    (asserts no route in `kweb2` / `api_routes` is a coroutine, and that
    `graph_service` works off the import thread).
  - `test_generate_repo_id_endpoint.py` and `test_scope_resolution.py` now call
    the handlers directly instead of through `asyncio.run`.

## Results (same DB, local kweb)

| Request | Before | After |
|---|---|---|
| `/` warm | ~1.7s | ~0.01s |
| `/repos/` repeat | ~7s every load | ~0.09s |
| `/summary/<org_key>` developer count | estate-wide (wrong) | org-scoped |
| `/health` while a cold `/repos/` runs | queued behind it | ~2ms |

Cold loads are unchanged: the first request after a sync or after `max_age`
still runs the full queries (~7–13s for `/repos/`, depending on machine load).

## Implementation notes

- Any repo sync invalidates every entry, since the check is against the latest
  `last_sync` across all repos. With `kospex-agent` running every 5 minutes, the
  slow cold load comes back often.
- Two concurrent cold requests for the same key both compute; there is no
  per-key lock. With handlers on the threadpool they no longer block each other,
  but they do double the DB load.
- The cache is per process and unbounded by key count. It grows with each
  distinct scope a user visits. Stale entries are dropped when read.

## Follow-ups (not in this change)

- Warm the cache in the background at startup and after a sync, so no user
  waits for a cold load.
- Precompute the per-repo and per-(author, repo) stats at sync time, so that
  only the synced repo's rows are recomputed. That removes the cold load
  instead of hiding it, and holds up as the estate grows.
