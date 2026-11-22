#!/usr/bin/env python3
"""This is the kospex reaper command line tool."""
import os
import os.path
import click
from kospex_core import Kospex
import kospex_utils as KospexUtils
import kospex_schema as KospexSchema
from kospex.db.introspect import get_kospex_tables, get_repo_tables
from kospex.db.migrator import warn_if_behind

kospex = Kospex()

@click.group()
def cli():
    """kreaper (Kospex Reaper) is a utility for destroying and deleting thigs in the kospex DB.

    For documentation on how commands run `kreaper COMMAND --help`.

    """
    # Deleting from a behind DB is doubly worth warning about.
    warn_if_behind(kospex.kospex_db)
@cli.command("repos")
def repo_ids():
    """ List the repo_ids (based on commits table)."""
    for repo_id in kospex.kospex_query.get_repo_ids():
        print(repo_id)

@cli.command("delete-repo")
@click.option('-repo_id', type=click.STRING)
@click.option('-table', type=click.STRING, help="Only delete rows with repo_id elements from this table.")
@click.option('-yes/-no', default=False)
@click.option('-dry-run', 'dry_run', is_flag=True,
              help="Show the rows that would be deleted, without deleting them.")
def delete_repo(repo_id,table,yes,dry_run):
    """ Delete a repo_id from all tables.

    Clears the repo from every table carrying a _repo_id column, detected at
    runtime rather than hardcoded. That includes 'repos', so the sync
    provenance goes too and the next sync walks the full history - which is
    what makes this the way to reset a repo before a re-sync.

    -table TABLE deletes from that one table only. `-table commits`, then
    `kgit pull REPO_ID`, re-reads a repo's full history and keeps its 'repos'
    row, so the pull still finds the clone.

    Use -dry-run first to see the row counts per table. With -table it counts
    that table only.
    """
    if table and table not in get_kospex_tables(kospex.kospex_db):
        print(f"table {table} is NOT a valid table name.")
        print("Here's a list of valid tables:")
        for name in sorted(get_kospex_tables(kospex.kospex_db)):
            print(name)
        return

    if dry_run and repo_id:
        counts = kospex.repo_id_row_counts(repo_id, tables=[table] if table else None)
        where = f" in table {table}" if table else ""
        if not counts:
            print(f"No rows found for repo_id {repo_id}{where}")
            return

        print(f"Dry run - would delete the following for repo_id {repo_id}{where}:\n")
        for name in sorted(counts):
            print(f"  {name:<20} {counts[name]:>8} rows")
        print(f"\n  {'TOTAL':<20} {sum(counts.values()):>8} rows")
        print("\nNothing deleted. Re-run with -yes to delete.")
        return

    if table:
        print(f"table {table} is a valid table name.")
        if not yes:
            print("Please specify -yes to confirm deletion.")
            return
        print()
        print(f"About to delete repo_id {repo_id}")
        print(f"from table {table}")
        results = kospex.delete_repo_id_from_table(table,repo_id)
        print(f"{results} rows deleted.\n")

    elif repo_id:

        if not yes:
            print("Please specify -yes to confirm deletion.")
            return

        for table in sorted(get_repo_tables(kospex.kospex_db)):
            print(table)
            results = kospex.delete_repo_id_from_table(table,repo_id)
            print(f"{results} rows deleted.\n")




    else:
        print("Please specify a repo_id to delete.")

@cli.command("drop-table")
@click.option('-yes/-no', default=False)
@click.option('-table', type=click.STRING)
def drop_table(table,yes):
    """ Drop a table from the kospex DB."""
    if table and table in get_kospex_tables(kospex.kospex_db):
        print(f"Found a valid table '{table}' to drop.")
        if yes:
            # We have permission to drop the table
            KospexSchema.drop_table(table)
        else:
            print("Please specify -yes to confirm deletion.")
            return
    else:
        print("Please specify a valid table to drop from the following options:\n")
        for table in sorted(get_kospex_tables(kospex.kospex_db)):
            print(table)
        print()
        #return

if __name__ == '__main__':
    cli()
