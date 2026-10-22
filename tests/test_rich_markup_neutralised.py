"""Values from git metadata must print literally, never as rich markup (CWE-150).

Author emails, remote URLs and repo_ids are controlled by whoever commits to,
or names, a repo. git accepts brackets in all of them. A str handed to a rich
console, as a print argument, table cell or title, is parsed as markup unless
the console has markup=False: "a[/]@b" raises MarkupError, "[link=...]" renders
a hyperlink to another target and "[conceal]" hides the value.
"""
import importlib
import io
import subprocess

import pytest
from click.testing import CliRunner
from rich.console import Console
from rich.table import Table

HOSTILE = [
    "evil[/]@example.com",
    "[link=https://attacker.example]ceo@example.com[/link]",
    "[conceal]hidden@example.com[/conceal]",
]


@pytest.mark.parametrize("module, name", [
    ("kgit", "console"),
    ("kospex_cli", "console"),
    ("krunner", "console"),
    ("kospex_core", "_console"),
])
def test_module_console_prints_markup_literally(module, name):
    console = getattr(importlib.import_module(module), name)
    table = Table(title=HOSTILE[0])
    table.add_column("value")
    for value in HOSTILE:
        table.add_row(value)

    with console.capture() as capture:
        console.print(HOSTILE[0])
        console.print(table)
    out = capture.get()

    for value in HOSTILE:
        assert value in out


def test_print_table_piped_ignores_markup_on_callers_console():
    """The piped branch prints through its own console: markup off regardless."""
    import kospex_utils as KospexUtils

    table = Table()
    table.add_column("author")
    for value in HOSTILE:
        table.add_row(value)
    out = io.StringIO()  # not a terminal, so the piped branch
    console = Console(file=out, markup=True, width=80)

    KospexUtils.print_table(table, console)

    for value in HOSTILE:
        assert value in out.getvalue()


def test_kgit_status_prints_hostile_remote_literally(tmp_path, monkeypatch):
    import kgit

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    remote = "https://github.com/acme/[conceal]x[/conceal]repo[/].git"
    stats = {"total_commits": 1, "remote_url": remote, "status": "Active",
             "total_size": 1024, "git_dir_size": 1024, "repo_size_without_git": 0}
    monkeypatch.setattr(kgit.KospexUtils, "get_git_stats", lambda repo, days: stats)

    result = CliRunner().invoke(kgit.cli, ["status", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert remote in result.output


def test_kospex_stats_prints_hostile_authors_and_repo_id_literally(monkeypatch):
    import kospex_cli

    repo_id = "github.com~acme~[bold]app[/]"
    # stats doesn't print through print_table yet, so piped it fits 80 columns
    # and truncates; widen it so the assertion is about markup, not width.
    monkeypatch.setattr(kospex_cli.console, "width", 400)
    rows = [{"author_email": h, "total_commits": 3} for h in HOSTILE]
    monkeypatch.setattr(kospex_cli.KospexQuery, "__init__", lambda self, **kw: None)
    monkeypatch.setattr(kospex_cli.KospexQuery, "get_developer_stats",
                        lambda self, repo_id=None: rows)

    result = CliRunner().invoke(kospex_cli.cli, ["stats", repo_id])

    assert result.exit_code == 0, result.output
    assert repo_id in result.output
    for value in HOSTILE:
        assert value in result.output


def test_kospex_stats_unknown_repo_message_is_literal(monkeypatch):
    import kospex_cli

    repo_id = "github.com~acme~app[/]"
    monkeypatch.setattr(kospex_cli.KospexQuery, "__init__", lambda self, **kw: None)
    monkeypatch.setattr(kospex_cli.KospexQuery, "get_developer_stats",
                        lambda self, repo_id=None: [])

    result = CliRunner().invoke(kospex_cli.cli, ["stats", repo_id])

    assert result.exit_code == 0, result.output
    assert f"No developer stats found for {repo_id}." in result.output
