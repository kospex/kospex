"""krunner filenames must round-trip through the canonical repo_id (#94 Step 4).

A krunner report is named '{repo_id}.{function}.{ext}'. Parsing it back split
the whole name on '~' and indexed, then split the remainder on '.' and indexed:

  - a repo name containing a dot ('Chart.js') put half the name into `function`
    and shifted `ext`, producing repo_id 'github.com~chartjs~Chart'
  - a nested org ('gitlab.com~group~~subgroup~repo') raised IndexError

Both are fixed by splitting the extension and function off the right-hand end
and handing the remainder to parse_repo_id.
"""
import pytest

from kospex_core import Kospex
from kospex_git import KospexGit
import kospex_utils as KospexUtils

KRUNNER_HOME = "/k"

ROUND_TRIP_REPOS = [
    ("github.com", "acme", "svc"),
    ("github.com", "chartjs", "Chart.js"),          # dotted repo name
    ("gitlab.com", "group/subgroup", "repo"),       # nested org
    ("dev.azure.com", "myorg/MyProject", "MyRepo"),  # ADO org/project
    ("github.com", "acme", "dashboard.js"),
]


@pytest.mark.parametrize("server,org,repo", ROUND_TRIP_REPOS)
def test_krunner_filename_round_trips(server, org, repo):
    """The name kospex writes must be the name kospex can read back."""
    repo_id = KospexGit.generate_repo_id(server, org, repo)
    filename = f"{KRUNNER_HOME}/{repo_id}.developers.out"

    got = Kospex().extract_krunner_file_details(filename, krunner_home=KRUNNER_HOME)

    assert got["repo_id"] == repo_id
    assert got["git_server"] == server
    assert got["org"] == org
    assert got["repo"] == repo
    assert got["function"] == "developers"
    assert got["ext"] == "out"


def test_repo_id_is_built_by_the_canonical_builder():
    """Not reassembled by hand from the pieces."""
    repo_id = KospexGit.generate_repo_id("gitlab.com", "group/subgroup", "repo")
    got = Kospex().extract_krunner_file_details(
        f"{KRUNNER_HOME}/{repo_id}.osi.json", krunner_home=KRUNNER_HOME)
    assert got["repo_id"] == repo_id
    assert KospexUtils.parse_repo_id(got["repo_id"])["org"] == "group/subgroup"


def test_unparseable_filename_returns_none():
    assert Kospex().extract_krunner_file_details(
        f"{KRUNNER_HOME}/not-a-krunner-file", krunner_home=KRUNNER_HOME) is None
