# Kospex DB Migrations

Each migration is identified by a 4-digit prefix. The current baseline is
version 2 (frozen in `kospex_schema.py`), so the next migration starts at `0003`.

## Adding a new migration

1. Create `NNNN_<short-slug>.sql` (next sequential number). Example:
   `0003_add_repos_size_bytes.sql`.
2. (Optional) Create `NNNN_<short-slug>.py` with the same prefix and slug if
   you need a Python backfill. It must export `def up(db): ...` which runs
   *after* the SQL, in the same transaction.
3. Don't include `BEGIN` / `COMMIT` / `ROLLBACK` in your SQL — the runner
   manages the transaction.
4. Once a migration is committed and shipped, **do not edit it.** Write a
   new one.

## Running

- `kospex upgrade-db` — show status (dry run)
- `kospex upgrade-db -apply` — apply pending migrations

See `changes/202605-db-migration-system.md` for the full design.

## Limitations

The runner splits each `.sql` file on `;` to execute statements individually
inside a single transaction. This means the following constructs are NOT
currently safe in a migration file:

- **`CREATE TRIGGER ... BEGIN ... END;`** — the inner statements get split.
  Workaround: do trigger creation in the Python `up(db)` step instead, using
  `db.execute("CREATE TRIGGER ... BEGIN ...")` as a single string.
- **String literals containing `;`** — e.g. `INSERT INTO config VALUES ('Hello; world')`
  gets fractured inside the quotes. Workaround: same as above, or escape via
  the Python step.
- **`/* ... */` block comments containing `;`** — split inside the comment.
  Workaround: use `--` line comments instead.

If you need any of these patterns, do the schema change in `.sql` and the
nuanced part in the paired `.py` file's `up(db)` function — `up()` runs the
raw string against the connection and is not subject to splitting.

This is a known limitation; tracked for future improvement.
