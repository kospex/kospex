"""get_repo_authors must look up the canonical repo_id (#94 Step 2).

It built the id with KospexGit.repo_id_from_url_parts, an f-string duplicate of
generate_repo_id that omits the '/' -> '~~' encoding:

    repo_id_from_url_parts -> gitlab.com~group/subgroup~repo
    generate_repo_id       -> gitlab.com~group~~subgroup~repo

Sync writes the encoded form, so the lookup used an id that cannot exist in the
database and the author count came back 0 for every nested-group repository --
silently, since 0 is a plausible answer.
"""
from kospex_dependencies import KospexDependencies
from kospex_git import KospexGit


class _RecordingQuery:
    """Captures the repo_id it is asked about, and reports two authors."""

    def __init__(self):
        self.asked_for = None

    def authors_by_repo(self, repo_id):
        self.asked_for = repo_id
        return [{"author_email": "a@e.com"}, {"author_email": "b@e.com"}]


def _lookup(url):
    q = _RecordingQuery()
    deps = KospexDependencies(kospex_db=None, kospex_query=q)
    count = deps.get_repo_authors(url)
    return q.asked_for, count


def test_flat_url_uses_the_canonical_repo_id():
    asked, count = _lookup("https://github.com/acme/svc.git")
    assert asked == "github.com~acme~svc"
    assert count == 2


def test_nested_group_url_uses_the_encoded_repo_id():
    asked, count = _lookup("https://gitlab.com/group/subgroup/repo.git")
    assert asked == "gitlab.com~group~~subgroup~repo"
    assert count == 2


def test_lookup_id_matches_generate_repo_id_for_every_shape():
    """The property that matters: one builder, used everywhere."""
    for url in [
        "https://github.com/acme/svc.git",
        "git@github.com:acme/svc.git",
        "https://gitlab.com/group/subgroup/repo.git",
        "https://dev.azure.com/myorg/MyProject/_git/MyRepo",
        "https://bitbucket.example.com/scm/PROJ/repo.git",
    ]:
        parts = KospexGit.parse_git_remote(url)
        expected = KospexGit.generate_repo_id(
            parts["remote"], parts["org"], parts["repo"])
        asked, _ = _lookup(url)
        assert asked == expected, f"{url}: asked {asked!r}, expected {expected!r}"


def test_unparseable_url_reports_no_authors():
    asked, count = _lookup("ftp://not-a-git-host/whatever")
    assert asked is None
    assert count == 0
