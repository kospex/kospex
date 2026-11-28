"""An author's technology by language, as the developer pages show it.

The developer view listed file extensions while the repo view listed languages.
author_languages() names each historical path the way the repo view would, so
files since deleted or moved are still counted.
"""
import json
import os
import re

import pytest
import sqlite_utils
from starlette.requests import Request

import kospex_schema as KospexSchema
import kospex_utils as KospexUtils
from kospex_query import KospexQuery

import kweb2

DEV = "dev@example.com"
WHEN = "2026-01-01T00:00:00+00:00"


@pytest.fixture(autouse=True)
def _preserve_kospex_env():
    """Constructing KospexQuery sets KOSPEX_* env vars as a side effect."""
    saved = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(saved)


def _db():
    db = sqlite_utils.Database(memory=True)
    db.execute(KospexSchema.SQL_CREATE_COMMITS)
    db.execute(KospexSchema.SQL_CREATE_COMMIT_FILES)
    db.execute(KospexSchema.SQL_CREATE_REPOS)
    return db


def _change(db, repo, hash_, path, when=WHEN, email=DEV):
    db[KospexSchema.TBL_COMMITS].insert(
        {"_repo_id": repo, "hash": hash_, "author_email": email,
         "author_when": when, "committer_when": when},
        pk=["_repo_id", "hash"], ignore=True)
    db[KospexSchema.TBL_COMMIT_FILES].insert(
        {"_repo_id": repo, "hash": hash_, "file_path": path,
         "committer_when": when},
        pk=["hash", "file_path", "_repo_id"])


def _languages(db, email=DEV):
    return KospexQuery(kospex_db=db).author_languages(email)


def _by_language(db):
    return {row["language"]: row for row in _languages(db)}


def test_commits_are_counted_once_per_language():
    db = _db()
    for path in ("a.py", "b.py", "c.html"):
        _change(db, "s~o~r", "h1", path)

    rows = _by_language(db)

    assert rows["Python"]["commits"] == 1
    assert rows["Python"]["files"] == 2
    assert rows["HTML"]["commits"] == 1


def test_one_file_in_many_commits():
    db = _db()
    for hash_ in ("h1", "h2", "h3"):
        _change(db, "s~o~r", hash_, "a.py")

    row = _by_language(db)["Python"]

    assert (row["commits"], row["files"]) == (3, 1)


def test_shared_hash_in_two_repos_is_not_multiplied():
    db = _db()
    _change(db, "s~o~a", "h1", "README.md")
    _change(db, "s~o~b", "h1", "README.md")

    row = _by_language(db)["Markdown"]

    assert (row["commits"], row["files"], row["repos"]) == (2, 2, 2)


def test_path_no_longer_present_is_still_counted():
    """There is no file_metadata table at all: history alone names the language."""
    db = _db()
    _change(db, "s~o~r", "h1", "old/gone.go")

    assert [row["language"] for row in _languages(db)] == ["Go"]


def test_unnameable_and_null_paths_are_unknown():
    db = _db()
    for hash_, path in (("h1", "noext"), ("h2", "x.zzz"), ("h3", None)):
        _change(db, "s~o~r", hash_, path)

    rows = _languages(db)

    assert [(row["language"], row["commits"]) for row in rows] == [("Unknown", 3)]


def test_dates_compare_as_instants():
    """As text the +01:00 value sorts later, but it is the earlier instant."""
    db = _db()
    _change(db, "s~o~r", "h1", "a.py", when="2026-04-16T18:34:03+01:00")
    _change(db, "s~o~r", "h2", "a.py", when="2026-04-16T16:59:54-04:00")

    row = _by_language(db)["Python"]

    assert row["first_commit"] == "2026-04-16T17:34:03Z"
    assert row["last_commit"] == "2026-04-16T20:59:54Z"


def test_null_author_when():
    db = _db()
    _change(db, "s~o~r", "h1", "a.py", when=None)

    row = _by_language(db)["Python"]

    assert row["last_commit"] is None
    assert row["days_active"] == 0


def test_only_the_exact_author():
    db = _db()
    _change(db, "s~o~r", "h1", "a.py")
    _change(db, "s~o~r", "h2", "x.go", email="other@example.com")

    assert [row["language"] for row in _languages(db)] == ["Python"]
    assert _languages(db, "nobody@example.com") == []


def test_ordered_by_commits():
    db = _db()
    _change(db, "s~o~r", "h1", "a.py")
    _change(db, "s~o~r", "h2", "a.go")
    _change(db, "s~o~r", "h3", "b.go")

    assert [row["language"] for row in _languages(db)] == ["Go", "Python"]


# --- the two handlers that render developer_view.html ---------------------

HANDLERS = [("developers", "/developers/"), ("developer_view", "/developer/")]


def _page(monkeypatch, db, handler, path, query_string):
    monkeypatch.setattr(kweb2, "KospexQuery", lambda: KospexQuery(kospex_db=db))
    request = Request({
        "type": "http", "method": "GET", "path": path, "headers": [],
        "query_string": query_string.encode(), "app": kweb2.app,
        "router": kweb2.app.router, "server": ("localhost", 8000),
        "scheme": "http", "root_path": "",
    })
    response = getattr(kweb2, handler)(request)
    return response.status_code, response.body.decode()


def _technology_table(body):
    return body.split('id="devTable"', 1)[1].split("</table>", 1)[0]


def _cells(table):
    return [cell.strip() for cell in re.findall(r"<td[^>]*>(.*?)</td>", table, re.S)]


@pytest.mark.parametrize("handler,path", HANDLERS)
def test_page_shows_languages_and_file_count(monkeypatch, handler, path):
    db = _db()
    _change(db, "s~o~r", "h1", "a.py")

    status, body = _page(monkeypatch, db, handler, path, f"author_email={DEV}")

    table = _technology_table(body)
    assert status == 200
    assert "# Files" in table
    assert _cells(table)[:4] == ["Python", "1", "1", "1"]


@pytest.mark.parametrize("handler,path", HANDLERS)
def test_author_with_no_changes_renders(monkeypatch, handler, path):
    status, body = _page(monkeypatch, _db(), handler, path,
                         "author_email=nobody@example.com")

    assert status == 200
    assert _cells(_technology_table(body)) == []


@pytest.mark.parametrize("handler,path", HANDLERS)
def test_plus_in_email_survives(monkeypatch, handler, path):
    email = "1+dev@users.noreply.github.com"
    db = _db()
    _change(db, "s~o~r", "h1", "a.py", email=email)

    status, body = _page(monkeypatch, db, handler, path, f"author_email={email}")

    assert status == 200
    assert "Python" in _cells(_technology_table(body))


# --- the header: name, GitHub link, radar ----------------------------------

PRIVACY = "users.noreply.github.com"


@pytest.mark.parametrize("email,expected", [
    (f"123+octo-dev@{PRIVACY}", ("octo-dev", "octo-dev")),
    (f"octo-dev@{PRIVACY}", ("octo-dev", "octo-dev")),
    ("123+octo-dev@Users.NoReply.GitHub.com", ("octo-dev", "octo-dev")),
    # An ordinary address is never assumed to be a GitHub account.
    ("jane.doe@example.com", ("jane.doe", None)),
    ("jane+work@example.com", ("jane+work", None)),
    ("dev@github.com", ("dev", None)),
    ("dev@users.noreply.github.com.example.com", ("dev", None)),
    # GitHub marks app accounts with [bot].
    (f"49699333+dependabot[bot]@{PRIVACY}", ("dependabot[bot]", None)),
    (f"renovate[BOT]@{PRIVACY}", ("renovate[BOT]", None)),
    # git accepts any text as an email; only a GitHub login is linked.
    (f"1+../settings@{PRIVACY}", ("../settings", None)),
    (f"1+a?tab=x@{PRIVACY}", ("a?tab=x", None)),
    ("no-at-sign", ("no-at-sign", None)),
    ("", ("", None)),
    (None, ("", None)),
])
def test_developer_identity(email, expected):
    assert KospexUtils.developer_identity(email) == expected


def _author_page(monkeypatch, handler, path, email, files=("a.py",)):
    db = _db()
    for number, name in enumerate(files):
        _change(db, "s~o~r", f"h{number}", name, email=email)
    status, body = _page(monkeypatch, db, handler, path, f"author_email={email}")
    assert status == 200
    return body


@pytest.mark.parametrize("handler,path", HANDLERS)
def test_header_shows_name_email_and_link(monkeypatch, handler, path):
    email = f"123+octo-dev@{PRIVACY}"
    body = _author_page(monkeypatch, handler, path, email)

    assert re.search(r"<h1[^>]*>\s*octo-dev\s*</h1>", body)
    assert re.search(r'id="developerEmail"[^>]*>\s*' + re.escape(email), body)
    assert 'href="https://github.com/octo-dev"' in body


@pytest.mark.parametrize("handler,path", HANDLERS)
@pytest.mark.parametrize("email", [
    "jane.doe@example.com", f"1+dependabot[bot]@{PRIVACY}"])
def test_no_github_link(monkeypatch, handler, path, email):
    body = _author_page(monkeypatch, handler, path, email)

    assert "https://github.com/" not in body.split("Repository Activity")[0].split(
        "developerHeader", 1)[1]


def _radar_labels(body):
    return json.loads(re.search(r"labels: (\[.*?\])", body).group(1))


@pytest.mark.parametrize("handler,path", HANDLERS)
def test_radar_sits_in_the_header_with_eight_named_languages(
        monkeypatch, handler, path):
    files = ["a.py", "a.go", "a.rb", "a.rs", "a.js", "a.java", "a.c", "a.md",
             "a.html", "a.sql", "nothing.zzz-unknown"]
    body = _author_page(monkeypatch, handler, path, DEV, files)

    assert body.index('id="radarChart"') < body.index("Repository Activity")
    labels = _radar_labels(body)
    assert len(labels) == 8
    assert "Unknown" not in labels
    assert "Unknown" in _cells(_technology_table(body))


@pytest.mark.parametrize("handler,path", HANDLERS)
@pytest.mark.parametrize("files,shown", [
    (("a.py", "a.go"), False),
    (("a.py", "a.go", "nothing.zzz-unknown"), False),
    (("a.py", "a.go", "a.rb"), True),
])
def test_radar_needs_three_languages(monkeypatch, handler, path, files, shown):
    body = _author_page(monkeypatch, handler, path, DEV, files)

    assert ('id="radarChart"' in body) is shown
