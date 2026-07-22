# Docs site content and formatting overhaul

Rework of the `docs/` Jekyll site published to [docs.kospex.io](https://docs.kospex.io):
broader product positioning on the landing page, an accurate Web UI guide, and a fix for
the CSS that was silently stripping spacing from every page.

## Overview

Three related problems:

1. **Positioning was too narrow.** The landing page described kospex as a CLI that "looks
   at the guts of your code". That undersells it and omits the Web UI entirely.
2. **The Web UI docs were largely aspirational** — 14 "*(to be created)*" stub links, a
   nav menu that didn't match the app, and several capabilities documented that don't exist.
3. **Page formatting was broken sitewide** by missing CSS rules, not by the markdown.

## Files changed

| File | Change |
| ---- | ------ |
| `docs/index.md` | Repositioned around knowledge mapping, technology identification, open source libraries and maintenance indicators; CLI + Web UI both surfaced |
| `docs/assets/css/style.css` | Restored prose typography, code block styling, syntax highlighting; fixed fixed-header overlap |
| `docs/getting-started.md` | Heading hierarchy, consistent code fences, removed hard-break hacks |
| `docs/kweb/index.md` | Rewritten against actual `kweb2.py` routes; removed unimplemented claims |

## The CSS root cause

`docs/assets/css/style.css` **replaces** the slate remote theme's stylesheet rather than
extending it (Jekyll resolves a site file at that path ahead of the theme's). The theme's
prose typography and Rouge syntax highlighting were therefore never loaded.

Combined with the global `* { margin: 0; padding: 0; }` reset at the top of the file, this
meant markdown-rendered content had no spacing rules at all:

- **No `p` rule anywhere** — every paragraph rendered with zero margin, running together.
  This is why `getting-started.md` had accumulated trailing-backslash hard-break hacks:
  they were compensating for invisible paragraph breaks.
- **No `h3` / `h4` rules** — sub-headings collided with the text above them.
- **No `pre` / `code` rules** — fenced code blocks rendered as unstyled monospace with no
  background, padding or horizontal scroll.
- **Padding bug** — `padding-top: 100px` was immediately overridden by the
  `padding: var(--section-padding)` shorthand on the following line, leaving 80px. The
  header is `position: fixed` and stacks vertically below 768px, so content slid underneath
  it on mobile.

Fixes added under the "Markdown prose typography" block, plus a minimal Rouge palette for
the shell/python/yaml snippets used across the docs.

## Web UI doc corrections

Claims removed because they are not implemented in `src/kweb2.py` or the templates:

- CSV / JSON export and "custom report generation" — no export controls exist
- "REST API … rate limiting and authentication support" — there is **no authentication**
- "Community forums and discussion groups"
- 14 placeholder links to per-view pages that were never written

Corrections made:

- Nav menu documented as it actually is: Summary, Repos, Orgs, Developers, **Opensource**,
  Landscape, Metadata, Help (Summary and Opensource were both missing)
- View paths grounded in real routes (`/osi/`, `/hotspots/{repo_id}`,
  `/key-person/{repo_id}`, `/package-check/`, `/tenure/`, …)
- Added a security callout: `kweb` binds to `127.0.0.1`, has no authentication, and should
  not be exposed without something in front of it

The Developers view previously listed "performance reviews" as a use case. Replaced with
knowledge-mapping and offboarding framing, plus an explicit note that commit activity
indicates where knowledge sits and is not a productivity metric.

## Local preview environment

`docs/Gemfile` pins `github-pages ~> 231`, which requires `commonmarker ~> 0.23.7`. That
gemspec declares `required_ruby_version >= 2.6, < 4.0`. Homebrew's default `ruby` formula
has rolled to **4.0.x**, so `bundle install` fails to resolve:

```
Because github-pages >= 228, < 232 depends on jekyll-commonmark-ghpages = 0.4.0
  and jekyll-commonmark-ghpages >= 0.4.0, < 0.5.0 depends on commonmarker ~> 0.23.7 ...
So, because Gemfile depends on github-pages ~> 231
  and current Ruby version is = 4.0.3,
  version solving has failed.
```

Worth recording: `gh api repos/kospex/kospex/pages` reports `"build_type": "legacy"` with
source `main` at `/docs`. Despite the name, "legacy" is just the API's label for
"Deploy from a branch" — it is **not deprecated**, and GitHub still recommends it for sites
that don't need build customisation. The site is built by GitHub's classic server-side
build, so **the Gemfile does not affect what is published** — it only governs local
preview. (`docs/Gemfile` and `docs/Gemfile.lock` are gitignored for this reason.)

Per [pages.github.com/versions](https://pages.github.com/versions/), GitHub Pages currently
runs **Ruby 3.3.4**, Jekyll 3.10.0, github-pages 232.

Resolution:

```bash
brew install ruby@3.3          # keg-only; system ruby 4.x untouched

cd docs
export PATH="/opt/homebrew/opt/ruby@3.3/bin:$PATH"
bundle install
bundle exec jekyll serve
```

The local `Gemfile` pin was also bumped from `~> 231` to `~> 232` to match the version
GitHub actually builds with.

Longer term the intent is to move docs to Cloudflare Pages, matching the main kospex.io
site, rather than migrating to a GitHub Actions Pages workflow.

## Verification

Site builds clean under Ruby 3.3.12 (`done in 1.539 seconds`). Rendered output checked via
headless Chrome:

- Paragraph spacing, heading hierarchy, inline code and fenced code blocks all render
  correctly at desktop and tablet widths
- Rouge syntax highlighting is applied (`.highlight .c`, `.highlight .nb` present in output)
- `document.documentElement.scrollWidth == viewport` — no horizontal page overflow
- Long command lines scroll inside their `pre` (measured 616px content inside a constrained
  block) rather than widening the page

## Not done

- **True phone-width rendering (<500px) is unverified.** Headless Chrome on macOS clamps the
  window to a 500px minimum and crops the screenshot to the requested size, which makes
  narrow-viewport screenshots misleading. Verifying below 500px needs DevTools device
  emulation. The mobile `padding-top: 200px` header clearance was checked at 500px, not at
  375–390px.
- Per-view Web UI pages (repos, developers, landscape, …) remain unwritten. The stub links
  were removed rather than left as dead placeholders.
