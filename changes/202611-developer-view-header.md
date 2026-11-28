# Developer view: header, radar and GitHub link

**Status:** design approved from mockups and built 2026-11-28. Follows
`202611-developer-technology-by-language.md`.

## Why

- A GitHub privacy address made a long, hard to read heading.
- The radar was full width at the bottom of the page and often not seen.
- Every address got a "View on GitHub" link made from the part before the `@`.

## Behaviour

- Heading is the user name; the full email sits beneath in small grey text. For a
  privacy address the name drops the leading `<number>+`; otherwise it is the whole
  local part.
- The GitHub link appears only for a `users.noreply.github.com` address whose name is
  shaped like a login. A `[bot]` name is therefore not linked.
- The radar sits in the header box with up to eight languages by commits, leaving out
  `Unknown`. It is hidden when there are fewer than three. The table lists everything.

## Decisions

- `developer_identity()` does not use `EmailAnalyzer` for bots: its word matching also
  flags some people's names and misses many `[bot]` accounts.
- `extract_github_username()` is unchanged, so the JSON API, graph labels and
  `kospex` output behave as before.

## Files

`src/kospex_utils.py`, `src/kweb2.py`, `src/templates/developer_view.html`,
`src/templates/_single_radar.html`, `tests/test_author_languages.py`, `CHANGELOG.md`.
