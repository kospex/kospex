"""The one place a `kgit sync --org/--user` command is written out.

kgit's suggestions, the recorded `options` / `command` columns and kweb's
/syncs/ all use it, so they print the same text.
"""
from kospex import org_sync_command as C


def test_order_is_fixed_whatever_the_keyword_order():
    assert C.options(no_auth=True, next_=10, active=90) == [
        "-active", "90", "-next", "10", "-no-auth"]


def test_every_option_in_order():
    assert C.options(no_auth=True, no_prompt=True, ssh=True, skip_archived=True,
                     skip_forks=True, include_forks=True, retry_failed=True, next_=5,
                     active=30, restart=True, refresh=True, inventory=True) == [
        "-inventory", "-refresh", "-restart", "-active", "30", "-next", "5",
        "-retry-failed", "-include-forks", "-skip-forks", "-skip-archived", "-ssh",
        "-no-prompt", "-no-auth"]


def test_no_options():
    assert C.options() == []


def test_continuing_drops_listing_flags_and_retry_failed():
    opts = C.options(inventory=True, refresh=True, restart=True, retry_failed=True,
                     active=90, next_=10, ssh=True, no_auth=True)
    assert C.continuing(opts) == ["-active", "90", "-next", "10", "-ssh", "-no-auth"]


def test_command_text():
    assert C.command("--org", "acme", C.options(active=90, next_=10, no_auth=True)) == (
        "kgit sync --org acme -active 90 -next 10 -no-auth")
    assert C.command("--user", "octocat", []) == "kgit sync --user octocat"


def test_owner_is_shell_quoted():
    assert C.command("--org", "a b", []) == "kgit sync --org 'a b'"
    assert C.command("--org", "x;rm -rf ~", []) == "kgit sync --org 'x;rm -rf ~'"


def test_suggestion_keeps_the_run_options_and_applies_changes():
    typed = dict(inventory=True, restart=True, retry_failed=True, active=90, ssh=True,
                 no_auth=True)
    assert C.suggestion("--org", "acme", typed) == (
        "kgit sync --org acme -active 90 -ssh -no-auth")
    assert C.suggestion("--org", "acme", typed, next_=10) == (
        "kgit sync --org acme -active 90 -next 10 -ssh -no-auth")
    assert C.suggestion("--org", "acme", typed, inventory=True, refresh=True) == (
        "kgit sync --org acme -inventory -refresh -active 90 -ssh -no-auth")


def test_parse_round_trips():
    opts = C.options(active=90, next_=10, ssh=True, no_auth=True)
    assert C.parse(" ".join(opts)) == opts
    assert C.parse(None) == []
    assert C.parse("") == []
