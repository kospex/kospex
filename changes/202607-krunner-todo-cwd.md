# krunner todo — explicit cwd + a correct grep

**Status:** Done
**Owner:** Peter
**Date:** 2026-07-30

## Context

`krunner todo` was the last krunner command still relying on an implicit working
directory. The CWE-78 series (26–27 Jul) converted `trufflehog`, `grep`,
`gitleaks` and `semgrep` to `_run_scanner(argv, cwd=d)`, and
`changes/202607-repo-path-conflict.md` converted `git-pull`; `todo` was missed
because it was already argv-based (no shell), so it didn't look like a shell
finding.

It still worked the old way:

```python
kospex.set_repo_dir(d)                  # chdirs the whole process into the repo
...
cmd = ["grep", "-Rn", "TODO *"]
result = subprocess.run(cmd, ...)       # no cwd= — depends on that chdir
...
os.chdir(cwd)                           # manual restore at the end of each repo
```

## What was actually wrong

**1. Junk observations (real bug).** With no path operand, grep recursed into
`.git/`, so every repo contributed matches from git's own shipped hook samples:

```
./.git/hooks/sendemail-validate.sample:22:# Replace the TODO placeholders with ...
./.git/hooks/sendemail-validate.sample:27:  # TODO: Replace with appropriate checks ...
```

Four bogus `GREP_TODO` rows per repo, written to the observations table.

**2. A pattern that worked by accident.** `"TODO *"` reads like a shell glob but
is a BRE, where `*` means zero-or-more of the preceding *space* — so it matched a
bare `TODO` only incidentally. It was never a literal-string bug.

**3. Implicit cwd.** The bare grep only searched the right place because
`set_repo_dir()` had chdir'd the process, with a hand-rolled `os.chdir(cwd)` to
undo it. Any early return or raise between the two would strand the process — the
same defect class as the `get_branches` crash.

## Changes

```python
kgit = KospexGit()
kgit.set_repo(d)          # reads git metadata via 'git -C', no chdir
details = kgit.add_git_to_dict({})
details["hash"] = kgit.current_hash
...
cmd = ["grep", "-Rn", "--exclude-dir=.git", "TODO", "."]
result = subprocess.run(cmd, cwd=d, capture_output=True, text=True)
```

`KospexGit.set_repo()` is chdir-free — `KospexUtils.get_git_hash()` and
`get_git_remote_url()` both shell out as `git -C <dir>`. This is the same pattern
`krunner branches` already uses.

**krunner now contains no `os.chdir` and no `os.system` at all.**

## Tests

`tests/test_krunner_todo.py` — records the TODO lines it finds with the right
repo_id; matches a bare `TODO` with nothing after it (pins the intent the old
BRE only met by accident); leaves the process working directory alone; and, with
two repos where only one has a TODO, attributes the match to the right one
(which fails if the grep runs anywhere but the repo).

## Notes

- The junk rows never reached the real DB — `krunner todo` had not been run at
  scale, so no remediation is needed.
- `--exclude-dir` is supported by both GNU and BSD/macOS grep, the same
  portability assumption already made by `-R`.
- `kospex_core.Kospex.set_repo_dir()` still chdirs, and 13 call sites still
  depend on it (10 in `kospex_core.py`, 4 in `krunner.py`). Removing that is a
  separate, larger piece of work.
