"""Writing out a `kgit sync --org/--user` command.

kgit's suggestions ("Continue with ...", "Resume with ..."), the options and
command recorded in org_sync_commands, and kweb's /syncs/ all build the text
here, so a copied suggestion repeats the command that was run and kgit and the
page always print the same thing.

Design: changes/202611-kweb-syncs.md
"""
import shlex

# Flags that list or restart, or retry once: a command that continues the run
# leaves them out.
_NOT_CONTINUED = ("-inventory", "-refresh", "-restart", "-retry-failed")


def options(*, inventory=False, refresh=False, restart=False, active=None, next_=None,
            retry_failed=False, include_forks=False, skip_forks=False,
            skip_archived=False, ssh=False, no_prompt=False, no_auth=False):
    """The options as typed, always in the same order."""
    opts = []
    for flag, on in (("-inventory", inventory), ("-refresh", refresh),
                     ("-restart", restart)):
        if on:
            opts.append(flag)
    if active is not None:
        opts += ["-active", str(active)]
    if next_ is not None:
        opts += ["-next", str(next_)]
    for flag, on in (("-retry-failed", retry_failed), ("-include-forks", include_forks),
                     ("-skip-forks", skip_forks), ("-skip-archived", skip_archived),
                     ("-ssh", ssh), ("-no-prompt", no_prompt), ("-no-auth", no_auth)):
        if on:
            opts.append(flag)
    return opts


def continuing(opts):
    """The options for carrying on with the run: no listing flags, no retry."""
    return [opt for opt in opts if opt not in _NOT_CONTINUED]


def command(flag, owner, opts):
    """`kgit sync --org acme -active 90`, every value shell-quoted."""
    return " ".join(["kgit", "sync", flag, shlex.quote(owner)]
                    + [shlex.quote(opt) for opt in opts])


def suggestion(flag, owner, typed, **changes):
    """The command to suggest next: the run's options as typed, plus `changes`.

    typed: the keyword arguments of `options()` the user's command had. Its
    listing flags and -retry-failed are dropped first, so `changes` can add
    `inventory=True, refresh=True` or `next_=10` to what carries on.
    """
    carried = {name: value for name, value in typed.items()
               if name not in ("inventory", "refresh", "restart", "retry_failed")}
    return command(flag, owner, options(**{**carried, **changes}))


def parse(options_text):
    """The stored options text back as a list."""
    return shlex.split(options_text) if options_text else []
