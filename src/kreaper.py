#!/usr/bin/env python3
"""This is the kospex reaper command line tool."""
import os
import os.path
import click
from kospex_core import Kospex
import kospex_utils as KospexUtils
import kospex_schema as KospexSchema

kospex = Kospex()

@click.group()
def cli():
    """kreaper (Kospex Reaper) is a utility for destroying and deleting thigs in the kospex DB.

    For documentation on how commands run `kreaper COMMAND --help`.

    """
@cli.command("repos")
def repo_ids():
    """ List the repo_ids (based on commits table)."""
    for repo_id in kospex.kospex_query.get_repo_ids():
        print(repo_id)

@cli.command("delete-repo")
@click.option('-repo_id', type=click.STRING)
@click.option('-table', type=click.STRING, help="Only delete rows with repo_id elements from this table.")
@click.option('-yes/-no', default=False)
def delete_repo(repo_id,table,yes):
    """ Delete a repo_id from all tables."""

    # TODO - Implement for all tables!
    print("Warning : This function only implements table!")

    if table:
        if table in KospexSchema.KOSPEX_TABLES:
            print(f"table {table} is a valid table name.")
            if not yes:
                print("Please specify -yes to confirm deletion.")
                return
            else:
                print()
                print(f"About to delete repo_id {repo_id}")
                print(f"from table {table}")
                results = kospex.delete_repo_id_from_table(table,repo_id)
                print(f"{results} rows deleted.\n")

        else:
            print(f"table {table} is NOT a valid table name.")
            print("Here's a list of valid tables:")
            for table in KospexSchema.KOSPEX_TABLES:
                print(table)

    elif repo_id:

        if not yes:
            print("Please specify -yes to confirm deletion.")
            return

        for table in KospexSchema.REPO_TABLES:
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
    if table and table in KospexSchema.KOSPEX_TABLES:
        print(f"Found a valid table '{table}' to drop.")
        if yes:
            # We have permission to drop the table
            KospexSchema.drop_table(table)
        else:
            print("Please specify -yes to confirm deletion.")
            return
    else:
        print("Please specify a valid table to drop from the following options:\n")
        for table in KospexSchema.KOSPEX_TABLES:
            print(table)
        print()
        #return

if __name__ == '__main__':
    cli()
