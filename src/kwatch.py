#!/usr/bin/env python3
"""This is the kospex watch tool. Track critical files in a git repo."""
import os
import os.path
import click
from kospex_core import Kospex
import kospex_utils as KospexUtils


kospex = Kospex()

@click.group()
def cli():
    """kwatch (Kospex Watcher) is a utility for tracking when important files change. 

    For documentation on how commands run `kwatch COMMAND --help`.
    
    """

@cli.command("watch")
@click.argument('file', type=click.Path(exists=True))
def test(file):
    """Track a file."""
    print("\nFile: " + os.path.abspath(file))
    #kospex.watch_file(file)


if __name__ == '__main__':
    cli()
