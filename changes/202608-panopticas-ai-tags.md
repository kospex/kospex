# AI coding agent tags in file_metadata

**Status:** Done
**Owner:** Peter
**Date:** 2026-08-05

## Context

kospex records a `tech_type` tag list for every file it syncs. `kospex_git.py`
builds it by calling into panopticas:

```python
tags = Panopticas.get_filename_metatypes(entry)
data["tech_type"] = tags
```

`kospex_schema.array_to_db_tags()` then encodes it as a pipe-delimited string
(`|pip|Python|dependencies|`), and `kospex_query.py` filters on it with
`tech_type LIKE '%|{tag}|%'`.

Until now panopticas recognised exactly two AI coding agent files — `claude.md`
and `gemini.md`. Everything else an agent leaves in a repository was invisible
to kospex: `.claude/`, `AGENTS.md`, `.cursor/rules/`, `.github/copilot-instructions.md`,
MCP configs, and the equivalents for every other tool.

That is a growing blind spot. "Which of our repos have AI tooling configured,
and which tools" is a question kospex is otherwise well-placed to answer, since
it already walks every file of every repo.

## Design

**No kospex code change.** panopticas 0.0.17 emits the new tags from
`get_filename_metatypes()` — the function kospex already calls — so the tags
flow through the existing sync path, the existing encoding, and the existing
query helper untouched.

The pin moves and nothing else:

```diff
-    "panopticas==0.0.16",
+    "panopticas==0.0.17",
```

panopticas detects 20 products via 60 path-based rules across three match modes
(exact filename, path fragment, filename suffix). Each recognised file yields
three tags: `AI`, the product brand, and the artifact kind.

Products are **brand-level** by design — `Claude` covers both Claude Code and
Claude Desktop, `Gemini` covers the CLI and Code Assist — so one tag finds all
of a vendor's tooling. Files owned by no vendor use a pseudo-product: `Agents`
for `AGENTS.md`, `MCP` for `.mcp.json`, `llms.txt` for `llms.txt`.

Detection is **path-based only**. panopticas never opens a file to decide
whether it is an AI artifact.

## Changes

`pyproject.toml` — `panopticas==0.0.16` → `panopticas==0.0.17`.

That is the whole change. What it produces, verified against the released
0.0.17 package:

| Path | Stored `tech_type` |
|---|---|
| `CLAUDE.md` | `\|AI\|Claude\|instructions\|` |
| `GEMINI.md` | `\|AI\|Gemini\|instructions\|` |
| `.claude/settings.json` | `\|AI\|Claude\|config\|` |
| `.cursor/rules/style.mdc` | `\|AI\|Cursor\|rules\|` |
| `AGENTS.md` | `\|AI\|Agents\|instructions\|` |
| `.mcp.json` | `\|AI\|MCP\|config\|` |
| `.github/copilot-instructions.md` | `\|GitHub\|Git\|AI\|Copilot\|instructions\|` |
| `pyproject.toml` | `\|build\|dependencies\|Python\|` *(unchanged)* |

Note the Copilot row: the AI tags are **additive**. panopticas appends them
after its existing extension/filename/path rules rather than replacing them, so
`.github/copilot-instructions.md` keeps the `GitHub` and `Git` tags it always
had.

Querying needs nothing new:

```python
kd.where("tech_type", "LIKE", "%|AI|%")       # every AI file
kd.where("tech_type", "LIKE", "%|Cursor|%")   # one product
```

Full product list: Claude, Copilot, Cursor, Gemini, Codex, Windsurf, Aider,
Cline, Roo Code, Continue, Amazon Q, Junie, Goose, Augment, OpenHands,
Kilo Code, Trae, plus `Agents`, `MCP` and `llms.txt`.

## Breaking change

The AI tag vocabulary changed. The old tags are **gone**, not aliased:

| File | 0.0.16 | 0.0.17 |
|---|---|---|
| `CLAUDE.md` | `\|Claude\|AI\|Claude Code\|` | `\|AI\|Claude\|instructions\|` |
| `GEMINI.md` | `\|Gemini\|AI\|Gemini CLI\|` | `\|AI\|Gemini\|instructions\|` |

Nothing in kospex queries `Claude Code` or `Gemini CLI` — grepped `src/*.py`
before bumping, zero hits, which is why no code change was needed. The only
hardcoded tag literal anywhere in kospex is `'%|dependencies|%'`
(`kospex_query.py`).

The exposure is **outside** this repo: any saved query, report, dashboard or
external consumer filtering on `Claude Code` or `Gemini CLI` now returns
nothing rather than erroring. Silent under-reporting, not a visible failure.

## Existing databases keep the old tags until re-sync

`tech_type` is cached at sync time, so bumping the pin changes nothing in a
database until its repos are synced again.

That happens automatically rather than needing a manual reindex.
`Kospex.file_metadata()` runs a version-aware skip-guard that compares the
`last_panopticas_version` recorded on the `repos` row against the installed
version:

```python
current = {
    "hash": git_hash,
    "panopticas_version": panopticas_version(),
    "scc_version": scc_version(),
}
recorded = self._recorded_sync_provenance(repo_id)
rebuild, reason = needs_metadata_rebuild(recorded, current, force=force)
```

Moving to 0.0.17 changes `panopticas_version`, so every repo is marked for a
`file_metadata` rebuild on its next sync. Until that sync runs, the row still
holds `|Claude|AI|Claude Code|`.

## Tests

No new kospex tests. The behaviour under test lives in panopticas, which covers
it with 236 tests including the tag-shape assertions and regression guards that
non-AI tag output is byte-identical.

What was verified here before bumping:

- `grep` for `Claude Code` / `Gemini CLI` / `'Claude'` / `"Claude"` across
  `src/*.py` — no hits, so no kospex code depends on the old vocabulary
- the stored `tech_type` strings in the table above, produced by installing
  `panopticas==0.0.17` into a clean venv and running `get_filename_metatypes()`
  through `array_to_db_tags()`'s encoding
- `pyproject.toml` is unchanged apart from the pin

## Notes

- kospex's venv may still carry panopticas 0.0.16 — the pyproject bump does not
  reinstall it. Run `pip install -e .` before expecting the new tags locally.
- panopticas 0.0.17 also adds a `panopticas ai [DIRECTORY]` command that lists
  AI artifacts with their product and kind. kospex does not use it; the
  library functions `get_ai_metadata(path)` and
  `find_ai_files(directory, all_files=False)` are exported if a kospex-side
  rollup is ever wanted.
- `AI_RULES` encodes 20 vendors' file conventions, which change without notice.
  panopticas' `CLAUDE.md` carries the rule "verify against the product's current
  official docs before adding — a missing rule is better than a wrong one". Two
  candidate rules were dropped during 0.0.17 for being user-level
  (`~/.config`) paths rather than repository artifacts.
