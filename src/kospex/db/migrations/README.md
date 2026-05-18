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
