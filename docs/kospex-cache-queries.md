# Querying the kospex URL cache

kospex caches responses from external services (deps.dev and PyPI) in the `url_cache`
table of the kospex database (`~/kospex/kospex.db`, or `$KOSPEX_HOME/kospex.db`). These
queries show which URLs have been requested, which is the closest record kospex keeps of
**what package names have been sent to deps.dev**.

## The table

```sql
CREATE TABLE [url_cache] (
    [url] TEXT,        -- the full request URL
    [content] TEXT,    -- the response body
    [timestamp] REAL,  -- epoch seconds when it was fetched
    PRIMARY KEY(url)
);
```

## What the cache can and cannot prove

Read this before using the cache as an audit record.

- **Only successful (HTTP 200) responses are cached.** A request that gets an error
  response, such as a 404 for a package deps.dev doesn't know, is not written to the
  cache or logged anywhere. A request that gets no response at all (an SSL error, a
  timeout, a refused connection) is not cached either, but from 0.4.1 it is logged
  with its URL in `~/kospex/logs/kospex_query.log`, as
  `Request to <url> failed: <error>`. A timeout may still have reached the server.
  So **a missing URL does not prove it was never sent.** A private package name is
  the case most likely to get a 404, and that is the case neither the cache nor the
  log can show. If you need to stop private names being sent, use
  `KOSPEX_DEPS_PRIVATE` (and `GOPRIVATE` for Go). Matching packages are skipped before
  any request is made and saved as `private_skipped`.
- **One row per URL.** Each exact URL has at most one row. The package URL
  (`.../packages/guava`) and each version URL (`.../packages/guava/versions/33.7.0-jre`)
  are separate rows, and each one is updated on its own.
- **A row changes only when its URL is fetched again successfully.** That happens
  only when kospex needs the URL again and the row is older than the TTL
  (`KOSPEX_URL_CACHE_SECONDS`, default one day). The new response then replaces the
  old content and timestamp. Nothing else changes a row:
  - **A cache hit doesn't touch it.** The timestamp is when the URL was last
    *fetched*, not when kospex last used it.
  - **A failed re-fetch leaves it unchanged.** The old content and timestamp stay, so
    a row's age is when the fetch last *succeeded*, not when it was last tried.
  - **Nothing deletes rows.** There is no expiry sweep, and deleting a repo doesn't
    remove its packages' rows. A row stays until it's overwritten or deleted by hand
    (see [Cleaning up old entries](#cleaning-up-old-entries)).

  So you can see *which* URLs were sent and when each last succeeded, but not how
  many times. The table holds every distinct URL that has succeeded since the
  database was created or last cleaned up.
- **The system name is part of the URL.** Older kospex versions sent `NPM` and `PyPi`;
  current ones send `npm` and `pypi`. deps.dev treats both spellings as the same
  system, but the cache treats them as different URLs, so the same package can appear
  under both. Filter case-insensitively, as these queries do.
- **Gradle packages are looked up as `maven`.** There is no `gradle` system in the URLs.

## Queries

All of these are read-only. Run them with the `sqlite3` CLI against
`~/kospex/kospex.db`.

### Every deps.dev URL, newest first

```bash
sqlite3 -header -column ~/kospex/kospex.db \
  "SELECT datetime(timestamp, 'unixepoch', 'localtime') AS cached_at, url
   FROM url_cache
   WHERE url LIKE 'https://api.deps.dev/%'
   ORDER BY timestamp DESC;"
```

The result can be tens of thousands of rows, so redirect it to a file
(`> depsdev_urls.txt`) and search that.

### URLs only, sorted, ready to grep

```bash
sqlite3 ~/kospex/kospex.db \
  "SELECT url FROM url_cache WHERE url LIKE '%deps.dev%' ORDER BY url;"
```

### Count by host

This shows what else is cached besides deps.dev, such as PyPI.

```bash
sqlite3 -header -column ~/kospex/kospex.db \
  "SELECT substr(url, 1, instr(substr(url, 9), '/') + 8) AS host, count(*) AS n
   FROM url_cache GROUP BY host ORDER BY n DESC;"
```

### Summary by API version, ecosystem and request type

```bash
sqlite3 -header -column ~/kospex/kospex.db "
SELECT CASE WHEN url LIKE '%/v3alpha/%' THEN 'v3alpha' ELSE 'v3' END AS api,
       substr(url, instr(url, '/systems/') + 9,
              instr(substr(url, instr(url, '/systems/') + 9), '/') - 1) AS system,
       CASE WHEN url LIKE '%:dependencies' THEN 'dependencies'
            WHEN url LIKE '%:requirements' THEN 'requirements'
            WHEN url LIKE '%/versions/%'   THEN 'version'
            WHEN url LIKE '%/packages/%'   THEN 'package'
            ELSE 'other' END AS kind,
       count(*) AS n,
       min(date(timestamp, 'unixepoch')) AS oldest,
       max(date(timestamp, 'unixepoch')) AS newest
FROM url_cache
WHERE url LIKE 'https://api.deps.dev/%'
GROUP BY 1, 2, 3
ORDER BY n DESC;"
```

The request types:

| kind | URL shape | What it fetches |
|---|---|---|
| `package` | `.../systems/<sys>/packages/<name>` | The package and its version list |
| `version` | `.../packages/<name>/versions/<v>` | One version, including its advisories |
| `requirements` | `.../versions/<v>:requirements` | The managed versions a Maven BOM declares |
| `dependencies` | `.../versions/<v>:dependencies` | The resolved dependency graph of one version |

### One ecosystem

Use `maven`, `npm`, `pypi`, `go` or `nuget`. `LIKE` is case-insensitive for ASCII, so
the old `NPM` and `PyPi` spellings match too.

```bash
sqlite3 ~/kospex/kospex.db \
  "SELECT datetime(timestamp, 'unixepoch', 'localtime') || '  ' || url
   FROM url_cache
   WHERE url LIKE 'https://api.deps.dev/%/systems/maven/%'
   ORDER BY url;"
```

Maven names are URL-encoded, with `%3A` standing for the `:` between group and artifact
(`com.google.guava%3Aguava`). To make the list easier to read, strip the common prefix:

```bash
... | sed 's#https://api.deps.dev/v3alpha/systems/maven/packages/##'
```

### Was a given name or prefix ever sent?

```bash
sqlite3 -header -column ~/kospex/kospex.db \
  "SELECT datetime(timestamp, 'unixepoch', 'localtime') AS cached_at, url
   FROM url_cache
   WHERE url LIKE 'https://api.deps.dev/%'
     AND (url LIKE '%/packages/com.example%' OR url LIKE '%/packages/github.com%2Fexample%');"
```

Match on the **encoded** form. A Maven `:` is `%3A`, and a Go module's `/` is `%2F`,
so `github.com/example/foo` appears as `github.com%2Fexample%2Ffoo`. An empty result
only means no *successful* lookup was cached (see the first caveat above).

### Recent activity: what a run just sent

```bash
sqlite3 -header -column ~/kospex/kospex.db \
  "SELECT datetime(timestamp, 'unixepoch', 'localtime') AS cached_at, url
   FROM url_cache
   WHERE url LIKE 'https://api.deps.dev/%'
     AND timestamp > CAST(strftime('%s', 'now', '-1 day') AS INTEGER)
   ORDER BY timestamp DESC;"
```

Change `-1 day` to `-2 hours`, `-7 days` and so on. A cache hit doesn't update the
timestamp, so this shows URLs actually fetched in the window, not every package the
run evaluated.

### Look at a cached response

```bash
sqlite3 ~/kospex/kospex.db \
  "SELECT content FROM url_cache
   WHERE url = 'https://api.deps.dev/v3alpha/systems/pypi/packages/requests';" | python3 -m json.tool | head -40
```

## Cleaning up old entries

kospex never deletes cache rows itself, so the table only grows. Deleting rows is
safe: a deleted URL is fetched again the next time kospex needs it. But **it destroys
the history these queries rely on.** Once a row is deleted, you can no longer see that
the URL was ever sent. If you might need that record, export it first.

### 1. Back up the database

Stop kweb, `kospex-agent` and any running `krunner` jobs first.

```bash
cp ~/kospex/kospex.db ~/kospex/kospex.db.$(date +%Y%m%d).bak
```

### 2. (Optional) Export the URLs you're about to delete

```bash
sqlite3 ~/kospex/kospex.db \
  "SELECT datetime(timestamp, 'unixepoch') || '  ' || url FROM url_cache
   WHERE timestamp < CAST(strftime('%s', 'now', '-1 month') AS INTEGER)
   ORDER BY url;" > url_cache_deleted_$(date +%Y%m%d).txt
```

### 3. Count what would be deleted

```bash
sqlite3 -header -column ~/kospex/kospex.db \
  "SELECT count(*) AS rows_to_delete,
          datetime(min(timestamp), 'unixepoch') AS oldest,
          datetime(max(timestamp), 'unixepoch') AS newest
   FROM url_cache
   WHERE timestamp < CAST(strftime('%s', 'now', '-1 month') AS INTEGER);"
```

### 4. Delete

```bash
sqlite3 ~/kospex/kospex.db \
  "DELETE FROM url_cache
   WHERE timestamp < CAST(strftime('%s', 'now', '-1 month') AS INTEGER);
   SELECT changes() AS rows_deleted;"
```

Change `-1 month` to `-7 days`, `-3 months` and so on. Use the same value in steps 2
to 4. To limit a cleanup to one host, add a condition on `url`, such as
`AND url LIKE 'https://api.deps.dev/%'`.

### 5. (Optional) Reclaim the disk space

SQLite doesn't shrink the file when rows are deleted. `VACUUM` rewrites it, which
needs free disk space about the size of the database and can take a while on a large
one.

```bash
sqlite3 ~/kospex/kospex.db "VACUUM;"
```
