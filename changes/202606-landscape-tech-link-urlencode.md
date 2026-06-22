# URL-encode the `/tech/` drill-down link on the landscape page

## Overview

The Technology Landscape web page (`/landscape/`, `/landscape/{id}`) renders a
breakdown table where each row links to a per-technology drill-down at
`/tech/{Language}`. The link was built with the raw language name:

```html
<a href="/tech/{{ row['Language'] }}">
```

This breaks for languages whose names contain URL-special characters, because
the browser interprets them before the request reaches the server:

| Language       | Raw href        | What the server saw |
|----------------|-----------------|---------------------|
| `C#`           | `/tech/C#`      | `/tech/C` (`#` starts a fragment) |
| `F#`           | `/tech/F#`      | `/tech/F` |
| `C++`          | `/tech/C++`     | `/tech/C  ` (`+` decodes to space) |
| `Visual Basic` | `/tech/Visual Basic` | malformed URL (raw space) |

The drill-down therefore returned the wrong technology (or nothing) for these
languages.

## Fix

Encode the language value with Jinja2's `urlencode` filter in
`src/templates/landscape.html`:

```html
<a href="/tech/{{ row['Language'] | urlencode }}">
```

Results: `C#` → `C%23`, `C++` → `C%2B%2B`, `F#` → `F%23`,
`Visual Basic` → `Visual%20Basic`; ordinary names (`Python`, `Objective-C`)
pass through unchanged.

The receiving route `@app.get("/tech/{tech}")` in `kweb2.py` takes `tech` as a
FastAPI path parameter, which is automatically URL-decoded, so it receives the
original language string (e.g. `C#`) with no further changes needed.

## Also: link the Language name

While here, the Language column (previously plain text) now links to the same
`/tech/{Language}` drill-down as the repos column, using the same URL-encoded
href. Both columns are now clickable entry points to the per-technology view.

## Files changed

- `src/templates/landscape.html` — added `| urlencode` to the `/tech/` link
  href, and made the Language name a `/tech/` link (matching the repos column).

## Notes

- Template-only change; no route, query, or schema changes.
- Verified the filter output for the affected language names against the
  installed Jinja2 (`C#` → `C%23`, etc.).
