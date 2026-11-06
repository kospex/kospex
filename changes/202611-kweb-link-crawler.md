# kweb link crawler, and two broken routes it found

## Overview

`tests/test_web_endpoints.py` requests each route with one repo id, accepts a
404 as a pass, and never uses a GitLab nested-group id or a base64 developer
id. `tests/manual/web_crawl.py` crawls a running kweb instead. It seeds every
route that takes an id with each kind of id (repo, org, server, nested-group
repo, base64 developer), then follows the links on each page. It caps fetches
per route shape so a run takes about five minutes on a 74k-developer DB.

    kweb
    python tests/manual/web_crawl.py
    python tests/manual/web_crawl.py --repo github.com~kospex~kospex --base-url http://127.0.0.1:8017

A 5xx, timeout or connection error fails the run (exit status 1). A 4xx is
listed for review, since some are deliberate (`/summary/{developer}` is a 404 by
design). It cannot catch a page that renders with wrong data.

## Fixed

- **`/tenure/{developer id}` returned 500.** `get_id_params()` decodes a
  base64 id to `author_email`, which `developers()` does not take. It now
  returns the 404 page, as `/summary/` does for a developer id.
- **Every file link on `/files/repo/{repo_id}` was a 404.** `files.html`
  linked to `/repo/{{ row['Provider'] }}`, but `Provider` is the file's path,
  not a repo id. It now links to `/file-collab/{repo_id}/?file_path=...`, as
  `commit.html` does.

## Found, not fixed

- Developer links built in `graph.html`, `bubble.html` and `treemap.html` put
  standard base64 in the path. An id containing `/` splits into two segments
  and 404s. 1 of 74,388 developers in the dev DB.
- `/file-collab/{repo_id}/?file_path=...` takes 25-30s, even on a 26-commit
  repo. Unreachable from the files page until the fix above.

## Files changed

- `tests/manual/web_crawl.py` - the crawler.
- `src/kweb2.py` - `tenure()` refuses an `author_email` scope.
- `src/templates/files.html` - file links go to `/file-collab/`.
- `tests/test_tenure_empty_scope.py`, `tests/test_files_page_links.py` - tests
  for both fixes, calling the handlers directly.
