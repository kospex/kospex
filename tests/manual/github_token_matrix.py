"""Manual live check: which GitHub token environment variable does kospex use?

Runs the real CLI, `kgit github -test-auth`, once per token configuration
against the real GitHub API, and checks each result against a literal table.
Not part of CI: the name does not match test_*.py, so pytest never collects it.

Each row pairs the real token with a deliberately invalid one, so a successful
login or a 401 shows which variable kgit read.

    python tests/manual/github_token_matrix.py                    # prompts for the token
    python tests/manual/github_token_matrix.py --token-file PATH
    python tests/manual/github_token_matrix.py --baseline         # before the #343 change
    python tests/manual/github_token_matrix.py --list             # rows only, no network

Use a fine-grained PAT with "Public repositories (read-only)" and no
permissions. The token is read from a prompt, a file, or
KOSPEX_TEST_GITHUB_TOKEN, never from argv (argv is visible in `ps` and shell
history), and reaches child processes only through their environment.

Design: changes/202611-github-token-env-consistency.md (testing layer 4).
"""
import argparse
import getpass
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

TOKEN_ENV_VARS = ("GITHUB_AUTH_TOKEN", "GH_TOKEN", "GITHUB_TOKEN")
TOKEN_SOURCE_ENV = "KOSPEX_TEST_GITHUB_TOKEN"
INVALID_TOKEN = "invalid-token"

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"
LIVE_PYTEST_FILE = REPO_ROOT / "tests" / "test_kospex_github_token_env.py"

# Expected outcomes, as classified from kgit's output.
OK = "ok"            # "Authenticated as:"
REJECTED = "401"     # "Invalid token"
MISSING = "missing"  # "Could not find" - no token in the environment
ERROR = "error"      # anything else: network, rate limit, crash

# Value specs a row can give a variable.
VALID, BAD, EMPTY, PADDED = "valid", "invalid", "empty", "padded"

# Rows whose result is expected to change with #343. Under --baseline (today's
# code) they are expected to fail; every other row must pass on both sides.
NEW, FIX = "new", "fix"

# kgit is imported from this checkout's src, so a worktree tests its own code
# rather than the main checkout's editable install.
BOOTSTRAP = (
    "import sys; sys.path.insert(0, sys.argv[1]); "
    "sys.argv = ['kgit'] + sys.argv[2:]; "
    "import kgit; kgit.cli()"
)
WHERE = (
    "import sys; sys.path.insert(0, sys.argv[1]); "
    "import kgit, kospex_github; print(kgit.__file__); print(kospex_github.__file__)"
)


@dataclass(frozen=True)
class Row:
    n: int
    values: dict          # variable -> VALID / BAD / EMPTY / PADDED; absent = unset
    expect: str           # OK / REJECTED / MISSING
    source: str | None    # variable that must supply the token, None if none
    tag: str = ""         # NEW / FIX, or "" for behaviour that must not change


# Literal on purpose: computing this from a precedence rule would just repeat
# the logic under test. Precedence after #343:
# GITHUB_AUTH_TOKEN > GH_TOKEN > GITHUB_TOKEN.
ROWS = (
    Row(1, {"GITHUB_AUTH_TOKEN": VALID}, OK, "GITHUB_AUTH_TOKEN"),
    Row(2, {"GH_TOKEN": VALID}, OK, "GH_TOKEN"),
    Row(3, {"GITHUB_TOKEN": VALID}, OK, "GITHUB_TOKEN", NEW),
    Row(4, {"GITHUB_AUTH_TOKEN": VALID, "GH_TOKEN": BAD}, OK, "GITHUB_AUTH_TOKEN"),
    Row(5, {"GITHUB_AUTH_TOKEN": BAD, "GH_TOKEN": VALID}, REJECTED, "GITHUB_AUTH_TOKEN"),
    Row(6, {"GITHUB_AUTH_TOKEN": VALID, "GITHUB_TOKEN": BAD}, OK, "GITHUB_AUTH_TOKEN"),
    Row(7, {"GITHUB_AUTH_TOKEN": BAD, "GITHUB_TOKEN": VALID}, REJECTED, "GITHUB_AUTH_TOKEN"),
    Row(8, {"GH_TOKEN": VALID, "GITHUB_TOKEN": BAD}, OK, "GH_TOKEN"),
    Row(9, {"GH_TOKEN": BAD, "GITHUB_TOKEN": VALID}, REJECTED, "GH_TOKEN"),
    Row(10, {"GITHUB_AUTH_TOKEN": VALID, "GH_TOKEN": BAD, "GITHUB_TOKEN": BAD},
        OK, "GITHUB_AUTH_TOKEN"),
    Row(11, {}, MISSING, None),
    Row(12, {"GITHUB_AUTH_TOKEN": EMPTY, "GH_TOKEN": VALID}, OK, "GH_TOKEN", FIX),
    Row(13, {"GITHUB_AUTH_TOKEN": PADDED}, OK, "GITHUB_AUTH_TOKEN", FIX),
)


@dataclass
class Result:
    row: Row
    status: str               # PASS / FAIL / ERROR / LEAK / XFAIL / XPASS
    got: str
    notes: list = field(default_factory=list)


def value_for(spec, token):
    return {VALID: token, BAD: INVALID_TOKEN, EMPTY: "", PADDED: f" {token}\n"}[spec]


def build_env(base, row, token, gh_config_dir):
    """The child environment for one row: no inherited token, only the row's."""
    env = {k: v for k, v in base.items()
           if k not in TOKEN_ENV_VARS and k != TOKEN_SOURCE_ENV}
    for var, spec in row.values.items():
        env[var] = value_for(spec, token)
    # Keeps a real `gh` login out of reach, should kospex ever fall back to it.
    env["GH_CONFIG_DIR"] = str(gh_config_dir)
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def command(src=SRC):
    """argv for one row. The token is never in it."""
    return [sys.executable, "-c", BOOTSTRAP, str(src), "github", "-test-auth"]


def classify(returncode, output):
    if "Authenticated as:" in output and returncode == 0:
        return OK
    # Today's kgit prints requests' HTTPError text on a 401; test_auth()'s own
    # "Invalid token" message is unreachable because a 401 Response is falsy.
    # Accept both so the runner works before and after that is fixed.
    if "401 Client Error" in output or "Invalid token" in output:
        return REJECTED
    if "Could not find" in output and returncode != 0:
        return MISSING
    return ERROR


def leaked(output, token):
    """True if the token, or a long-enough tail of it, appears in output."""
    if token in output:
        return True
    tail = token[-16:]
    return len(tail) == 16 and tail in output


def check(row, returncode, output, token, baseline=False):
    got = classify(returncode, output)
    notes = []

    if leaked(output, token):
        return Result(row, "LEAK", got, ["token value appears in output"])
    if got == ERROR:
        last = output.strip().splitlines()[-1:] or ["(no output)"]
        return Result(row, "ERROR", got, [f"unrecognised result: {last[0][:120]}"])

    ok = got == row.expect
    if not baseline:
        # The source line is new with #343; today's code doesn't print it.
        if row.source and f"Using token from {row.source}" not in output:
            ok = False
            notes.append(f"expected 'Using token from {row.source}'")
        if row.source is None and "Using token from" in output:
            ok = False
            notes.append("reported a token source when none was set")

    if baseline and row.tag:
        return Result(row, "XPASS" if ok else "XFAIL", got, notes)
    return Result(row, "PASS" if ok else "FAIL", got, notes)


def run_row(row, token, gh_config_dir, cwd, timeout=60):
    proc = subprocess.run(
        command(), env=build_env(os.environ, row, token, gh_config_dir),
        cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True, text=True,
        timeout=timeout,
    )
    return proc.returncode, proc.stdout + proc.stderr


def describe(row):
    cells = []
    for var in TOKEN_ENV_VARS:
        spec = row.values.get(var)
        cells.append({None: "-", EMPTY: '""', PADDED: "padded"}.get(spec, spec))
    return cells


def print_rows(rows=ROWS):
    head = ["#", *TOKEN_ENV_VARS, "expect", "source", "tag"]
    print(" | ".join(head))
    for row in rows:
        print(" | ".join([str(row.n), *describe(row), row.expect,
                          row.source or "-", row.tag or "-"]))


def print_results(results):
    print(f"{'#':>3}  {'status':<6} {'expect':<8} {'got':<8} "
          f"{'GITHUB_AUTH_TOKEN':<18} {'GH_TOKEN':<9} {'GITHUB_TOKEN':<13} notes")
    for r in results:
        a, g, t = describe(r.row)
        tag = f"[{r.row.tag}] " if r.row.tag else ""
        print(f"{r.row.n:>3}  {r.status:<6} {r.row.expect:<8} {r.got:<8} "
              f"{a:<18} {g:<9} {t:<13} {tag}{'; '.join(r.notes)}")


def failed(results):
    return [r for r in results if r.status in ("FAIL", "ERROR", "LEAK")]


def read_token(token_file=None, environ=os.environ, prompt=getpass.getpass):
    if token_file:
        token = Path(token_file).expanduser().read_text(encoding="utf-8").strip()
    elif environ.get(TOKEN_SOURCE_ENV, "").strip():
        token = environ[TOKEN_SOURCE_ENV].strip()
    else:
        token = prompt("GitHub token (input hidden): ").strip()
    if not token or any(c.isspace() for c in token):
        raise ValueError("token is empty or contains whitespace")
    if token == INVALID_TOKEN:
        raise ValueError(f"token must not be the literal {INVALID_TOKEN!r}")
    return token


def where_kgit_loads(cwd):
    proc = subprocess.run([sys.executable, "-c", WHERE, str(SRC)], cwd=cwd,
                          stdin=subprocess.DEVNULL, capture_output=True, text=True)
    return proc.stdout.split()


def run_live_pytest(token, cwd):
    if not LIVE_PYTEST_FILE.exists():
        print(f"\nLive pytest: skipped, {LIVE_PYTEST_FILE.name} not present yet")
        return 0
    env = {k: v for k, v in os.environ.items() if k not in TOKEN_ENV_VARS}
    env[TOKEN_SOURCE_ENV] = token
    env["PYTHONPATH"] = str(SRC)
    print(f"\nLive pytest: {LIVE_PYTEST_FILE.relative_to(REPO_ROOT)} -m live")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", str(LIVE_PYTEST_FILE), "-m", "live", "-rs",
         "-p", "no:cacheprovider"],
        env=env, cwd=REPO_ROOT, stdin=subprocess.DEVNULL,
    )
    return proc.returncode


def main(argv=None, runner=run_row):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--token-file", help="file holding the token (first line)")
    p.add_argument("--baseline", action="store_true",
                   help="today's code: tagged rows may fail, source line not checked")
    p.add_argument("--list", action="store_true", help="print the rows and exit")
    p.add_argument("--no-pytest", action="store_true", help="skip the live pytest step")
    p.add_argument("--row", type=int, action="append", help="run only this row (repeatable)")
    args = p.parse_args(argv)

    if args.list:
        print_rows()
        return 0

    try:
        token = read_token(args.token_file)
    except (OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    rows = [r for r in ROWS if not args.row or r.n in args.row]
    results = []
    with tempfile.TemporaryDirectory(prefix="kospex-token-matrix-") as tmp:
        gh_config_dir = Path(tmp, "gh")
        gh_config_dir.mkdir()
        cwd = Path(tmp, "cwd")
        cwd.mkdir()

        if runner is run_row:
            loaded = where_kgit_loads(cwd)
            print("kgit loads from:", *loaded, sep="\n  ")
            if not loaded or not all(Path(f).is_relative_to(SRC) for f in loaded):
                print(f"error: kgit is not loading from {SRC}", file=sys.stderr)
                return 2
        print(f"mode: {'baseline (pre-#343)' if args.baseline else 'post-#343'}\n")

        for row in rows:
            try:
                returncode, output = runner(row, token, gh_config_dir, cwd)
            except subprocess.TimeoutExpired:
                results.append(Result(row, "ERROR", ERROR, ["timed out"]))
                continue
            results.append(check(row, returncode, output, token, args.baseline))

        print_results(results)
        bad = failed(results)
        if any(r.row.n == 1 and r.status != "PASS" for r in results):
            print("\nRow 1 failed: check the token itself authenticates "
                  "(expired, revoked, or mistyped).")
        print(f"\n{len(results) - len(bad)}/{len(results)} rows as expected"
              + (f"; failing: {', '.join(str(r.row.n) for r in bad)}" if bad else ""))

        pytest_rc = 0
        if not args.no_pytest and runner is run_row:
            pytest_rc = run_live_pytest(token, cwd)
            if pytest_rc == 5:  # no tests collected: no live tests yet
                pytest_rc = 0

    return 1 if bad or pytest_rc else 0


if __name__ == "__main__":
    sys.exit(main())
