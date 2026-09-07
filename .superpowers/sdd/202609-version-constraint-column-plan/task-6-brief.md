### Task 6: Changelog and upgrade notes

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Add the Added entry**

Under `## Unreleased` → `### Added`:

```markdown
- **Dependency constraints are now recorded.** Four columns on
  `dependency_data` (migration `0006`): `version_kind` classifies how a
  dependency is constrained (pinned / commit / caret / tilde / gte / bounded /
  latest / workspace / link / catalog / alias / patch / none),
  `version_operator` keeps the raw declared operator, `resolved_version`
  records the version deps.dev was actually asked about, and `last_checked`
  records when. Previously the classification was computed and discarded, so
  "which of our dependencies float?" could not be asked of the database, and an
  advisory count could not be read without knowing which version it referred to
  or how old it was. `tilde` is deliberately separate from `caret` — `~29.0.0`
  permits patch drift where `^4.18.0` permits minor and patch — and `commit` is
  separate from `pinned`, since a Go pseudo-version is maximally pinned but has
  no published release to match against. Columns are NULL on existing rows
  until re-sync; a staleness indicator must treat NULL as "never checked", not
  "checked long ago". See `changes/202609-version-constraint-column.md`.
```

- [ ] **Step 2: Add the upgrade note**

`CHANGELOG.md` → `## Unreleased` → `### Upgrade notes` opens with **"Reported
numbers change in this release, in seven ways."** Change `seven` to `eight`
(the word, not a digit — match the existing prose) and append item `8.` to the
end of that numbered list:

```markdown
8. **Python requirements rows show their operator.** `requirements.txt` was the
   only parser that split the operator out of `package_version`, so `flask>=2.0`
   stored `2.0` and looked pinned. It now stores `>=2.0`, matching
   `pyproject.toml`, `package.json`, `go.mod` and `.csproj`. About 317 rows
   change; `package_version` is in the primary key, so the old rows are
   superseded rather than updated and demoted by the next `krunner osi` run.
   Anything reading that column verbatim will show the operator — which is
   accurate, and what `version_kind` now lets you filter on instead.
```

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): record the version constraint columns"
```

---

## Final Verification

- [ ] **Full suite:** `PYTHONPATH=$PWD/src pytest -q` — all pass.
- [ ] **Clean install reaches version 6:**
```bash
rm -rf /tmp/kospex-final && mkdir -p /tmp/kospex-final
KOSPEX_HOME=/tmp/kospex-final PYTHONPATH=$PWD/src python -m kospex_cli upgrade-db
```
Expect `No pending migrations. DB is at version 6.`
- [ ] **Classifier covers the live estate** — re-run the Task 1 Step 5 script; every row classifies, nothing unexpected in `none`.
- [ ] **End-to-end write:** back up `~/kospex/kospex.db`, apply the migration to it, run `krunner osi` over one small repo, and confirm the four columns are populated on the new rows. Verifying in isolation is what let the Go `v`-prefix defect ship twice.

## Notes for the implementer

**Why `last_checked` is not a column DEFAULT.** `created_at` is
`DEFAULT CURRENT_TIMESTAMP`, which SQLite applies on INSERT only. Re-running
`krunner osi` over `mergestat` re-fetched 95 npm rows from deps.dev and they
still read their original July date. If you "simplify" this to a default, the
column becomes useless for staleness and the failure is invisible.

**Why the classifier must not raise.** It runs inside extraction. An exception
does not lose one row, it loses the manifest — and the caller logs and moves on,
so nothing looks broken.

**Why `package_version` keeps declared text.** It is part of the primary key.
Rewriting it inserts duplicate rows on re-sync instead of updating, which is the
divergence C2a fixed for npm.

**#175 and #176 sit on this path.** They concern `upgrade-db` failing to recover
or diagnose a schema/ledger conflict. Neither blocks writing `0006`, but if
applying it locally goes wrong they are why it is hard to read. Fix the cause
rather than working around it.
