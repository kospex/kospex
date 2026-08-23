"""Characterisation tests for git URL parsing and repo_id generation.

This suite does NOT assert correct behaviour. It pins **current** behaviour —
bugs included — to a golden file so that any change to `parse_git_remote` or
`generate_repo_id` produces a readable diff of exactly which URLs changed.

Why: the parsers are being consolidated (#94) and `repo_id` case handling is
under review (#147). Both change how ids are derived, and a changed id means
existing `_repo_id` rows in a user's database stop matching. The risk worth
guarding is not "is this URL parsed correctly" — it is "did this refactor
silently change an id that already worked".

Workflow
--------
1. Refactor the parser.
2. Run this suite. It fails, listing every URL whose parse changed.
3. Confirm each change is one you intended, then regenerate:

       UPDATE_URL_GOLDEN=1 pytest tests/test_url_parsing_characterisation.py

4. Commit the golden diff alongside the code change. The diff is the review
   artefact — it shows the blast radius of a parser change in one place.

Entries marked ``"known_wrong"`` capture output that is currently incorrect.
They are deliberately pinned so a fix shows up as a diff rather than passing
silently. Fixing one is expected to change its golden entry.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from kospex_git import KospexGit

GOLDEN_PATH = Path(__file__).parent / "data" / "url_parsing_golden.json"

# Corpus grouped by provider. Weighted towards Azure DevOps and Bitbucket:
# those carry the most URL shapes, the most divergence between shapes for the
# same repository, and the least existing test coverage.
#
# `known_wrong` marks output that is incorrect today. It is documentation, not
# an assertion — the golden pins whatever the parser currently returns.
CORPUS: list[dict] = [
    # ---------------------------------------------------------------- GitHub
    {"url": "https://github.com/acme/svc", "group": "github"},
    {"url": "https://github.com/acme/svc.git", "group": "github"},
    {"url": "git@github.com:acme/svc.git", "group": "github"},
    {"url": "ssh://git@github.com/acme/svc.git", "group": "github"},
    {"url": "https://github.com/acme/dashboard.js.git", "group": "github",
     "note": "dotted repo name; regression guard for the scp-style fix"},
    {"url": "https://github.com/Acme/Svc", "group": "github",
     "note": "mixed case — pinned so #147 shows as a diff here"},

    # ---------------------------------------------------------------- GitLab
    {"url": "https://gitlab.com/group/repo.git", "group": "gitlab"},
    {"url": "https://gitlab.com/group/subgroup/repo.git", "group": "gitlab",
     "note": "nested group; org encodes '/' as '~~'"},
    {"url": "https://gitlab.com/a/b/c/repo.git", "group": "gitlab",
     "note": "two levels of subgroup"},
    {"url": "git@gitlab.com:group/subgroup/repo.git", "group": "gitlab"},

    # ----------------------------------------------------------- Azure DevOps
    # Four URL shapes reach the same repository. They currently produce four
    # different repo_ids -- the migration-relevant defect.
    {"url": "https://dev.azure.com/myorg/MyProject/_git/MyRepo", "group": "ado",
     "known_wrong": "org and project are joined with '-', which is ambiguous "
                    "because '-' is legal in ADO org names"},
    {"url": "https://dev.azure.com/myorg/MyProject/_git/MyRepo.git", "group": "ado"},
    {"url": "https://myorg@dev.azure.com/myorg/MyProject/_git/MyRepo", "group": "ado",
     "known_wrong": "this is ADO's own Clone-button URL. Username lands in the "
                    "host and '_git' survives as a path segment"},
    {"url": "https://myorg.visualstudio.com/MyProject/_git/MyRepo", "group": "ado",
     "known_wrong": "legacy host form: the org lives in the hostname and is "
                    "lost, so this yields a different id than dev.azure.com"},
    {"url": "https://myorg.visualstudio.com/DefaultCollection/MyProject/_git/MyRepo",
     "group": "ado", "known_wrong": "collection form is not recognised at all"},
    {"url": "git@ssh.dev.azure.com:v3/myorg/MyProject/MyRepo", "group": "ado",
     "known_wrong": "'v3' is taken as the org; host differs from the HTTPS form "
                    "so the same repo gets yet another id"},
    {"url": "ssh://git@ssh.dev.azure.com:v3/myorg/MyProject/MyRepo", "group": "ado",
     "known_wrong": "ssh:// with a port-like segment; user survives in the host"},
    {"url": "https://dev.azure.com/myorg/My Project/_git/MyRepo", "group": "ado",
     "known_wrong": "space in the project name reaches the repo_id"},

    # ------------------------------------------------------------- Bitbucket
    {"url": "https://bitbucket.org/team/repo.git", "group": "bitbucket-cloud"},
    {"url": "git@bitbucket.org:team/repo.git", "group": "bitbucket-cloud"},
    {"url": "https://USERNAME@bitbucket.org/team/repo.git", "group": "bitbucket-cloud",
     "note": "cloud clone URLs embed the username"},
    {"url": "https://bitbucket.example.com/scm/PROJ/repo.git", "group": "bitbucket-server",
     "note": "'/scm/' is a Bitbucket Server URL convention and is correctly "
             "dropped; project keys are uppercase by convention"},
    {"url": "https://user@bitbucket.example.com/scm/PROJ/repo.git",
     "group": "bitbucket-server",
     "known_wrong": "only a literal 'git@' is stripped, so other usernames "
                    "remain in the host"},
    {"url": "ssh://git@bitbucket.example.com:7999/PROJ/repo.git",
     "group": "bitbucket-server",
     "known_wrong": "#160 — Bitbucket Server's default SSH URL. Credentials "
                    "and port land in the host and the repo keeps a '/', so the "
                    "same repo over HTTPS and SSH gets two different ids"},
    {"url": "ssh://git@bitbucket.example.com/PROJ/repo.git", "group": "bitbucket-server",
     "note": "portless ssh:// parses correctly — isolates the port as the cause"},
    {"url": "https://bitbucket.example.com/scm/~personal/repo.git",
     "group": "bitbucket-server",
     "note": "personal repos are '~username'. The resulting '~~' round-trips "
             "correctly; it only collides if a *repo* name contains a '~'"},

    # ------------------------------------------------------------ Google / Go
    {"url": "https://go.googlesource.com/oauth2", "group": "google",
     "note": "single-segment Gerrit project; org is empty by design"},
    {"url": "https://chromium.googlesource.com/chromium/src", "group": "google",
     "note": "multi-segment already parses correctly via the generic rule"},
    {"url": "https://android.googlesource.com/platform/frameworks/base",
     "group": "google", "note": "deep path uses the nested-group encoding"},
    {"url": "https://boringssl.googlesource.com/boringssl", "group": "google"},

    # ----------------------------------------------------------- Should reject
    # Nothing here is a git remote. Every one currently returns a populated
    # dict rather than None (#160).
    {"url": "ftp://not-a-git-host/whatever", "group": "reject",
     "known_wrong": "#160 — non-git scheme is accepted"},
    {"url": "https://example.com/single", "group": "reject",
     "known_wrong": "#160 — single path segment, no org, is accepted"},
    {"url": "https://example.com/a/b/c/d/e/f", "group": "reject",
     "known_wrong": "#160 — arbitrarily deep path is accepted"},
    {"url": "not a url at all", "group": "reject"},
    {"url": "", "group": "reject"},
]


def _capture(url: str) -> dict:
    """Current parser output for one URL. Never raises — a crash is a result."""
    try:
        parts = KospexGit.parse_git_remote(url)
    except Exception as exc:  # noqa: BLE001 - capturing failure is the point
        return {"parts": None, "repo_id": None, "error": f"{type(exc).__name__}: {exc}"}

    if not parts:
        return {"parts": None, "repo_id": None}

    try:
        repo_id = KospexGit.generate_repo_id(
            parts.get("remote"), parts.get("org"), parts.get("repo")
        )
    except Exception as exc:  # noqa: BLE001
        return {"parts": parts, "repo_id": None,
                "error": f"{type(exc).__name__}: {exc}"}

    return {"parts": parts, "repo_id": repo_id}


def _capture_all() -> dict:
    return {entry["url"]: _capture(entry["url"]) for entry in CORPUS}


def _load_golden() -> dict:
    if not GOLDEN_PATH.is_file():
        pytest.fail(
            f"Golden file missing: {GOLDEN_PATH}\n"
            "Generate it with:  UPDATE_URL_GOLDEN=1 pytest "
            "tests/test_url_parsing_characterisation.py"
        )
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


def _write_golden(captured: dict) -> None:
    GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "_README": [
            "Characterisation golden for git URL parsing. Records CURRENT "
            "behaviour, bugs included -- it is not a statement of correctness.",
            "Regenerate: UPDATE_URL_GOLDEN=1 pytest "
            "tests/test_url_parsing_characterisation.py",
            "A diff here is the blast radius of a parser change. Review every "
            "line before committing it.",
        ],
        "results": captured,
    }
    GOLDEN_PATH.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def test_url_parsing_matches_golden() -> None:
    """Every URL in the corpus parses exactly as the golden file records."""
    captured = _capture_all()

    if os.environ.get("UPDATE_URL_GOLDEN"):
        _write_golden(captured)
        pytest.skip("golden regenerated — re-run without UPDATE_URL_GOLDEN to verify")

    golden = _load_golden()["results"]

    changed = []
    for url, now in captured.items():
        before = golden.get(url, "<not in golden>")
        if before != now:
            changed.append(f"  {url}\n     was: {before}\n     now: {now}")

    dropped = sorted(set(golden) - set(captured))

    problems = []
    if changed:
        problems.append(
            f"{len(changed)} URL(s) parse differently than the golden records:\n"
            + "\n".join(changed)
        )
    if dropped:
        problems.append(
            "URL(s) in the golden but no longer in CORPUS: " + ", ".join(dropped)
        )

    assert not problems, (
        "\n\n".join(problems)
        + "\n\nIf every change above is intended, regenerate the golden:\n"
        "  UPDATE_URL_GOLDEN=1 pytest tests/test_url_parsing_characterisation.py"
    )


def test_corpus_and_golden_cover_the_same_urls() -> None:
    """Guards against a URL being dropped from the corpus without notice."""
    golden = _load_golden()["results"]
    assert sorted(golden) == sorted(e["url"] for e in CORPUS)


def test_same_ado_repo_currently_yields_multiple_ids() -> None:
    """Pins the migration-relevant ADO defect as an explicit, named fact.

    Four clone URL shapes address one repository (myorg / MyProject / MyRepo).
    They currently produce different repo_ids, so one repo can be recorded
    several times. When the ADO parsing is fixed this test should fail and be
    rewritten to assert they *agree*.
    """
    same_repo = [
        "https://dev.azure.com/myorg/MyProject/_git/MyRepo",
        "https://myorg@dev.azure.com/myorg/MyProject/_git/MyRepo",
        "https://myorg.visualstudio.com/MyProject/_git/MyRepo",
        "git@ssh.dev.azure.com:v3/myorg/MyProject/MyRepo",
    ]
    ids = {_capture(u)["repo_id"] for u in same_repo}
    assert len(ids) > 1, (
        "ADO URL shapes now agree on a repo_id. That is the desired outcome — "
        "replace this test with one asserting they are all equal."
    )
