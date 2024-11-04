""" Tests for KospexGit """
from kospex_git import KospexGit
import kospex_utils as KospexUtils

def test_parse_git_remote():
    """ Test the  function """

    parts = KospexGit.parse_git_remote("https://go.googlesource.com/oauth2")
    assert parts is not None
    assert parts["repo"] == "oauth2"
    assert parts["remote_type"] == "https"
    assert parts["remote"] == "go.googlesource.com"
    assert parts["org"] == ""


def test_repo_id():
    """ Test repo_id generation """

    sample = "https://github.com/kospex/kospex"
    kg = KospexGit()
    kg.set_remote_url(sample)
    assert "github.com~kospex~kospex" == kg.repo_id
    print(kg.repo_id)

    kg.set_remote_url("git@github.com:kospex/panopticas.git")
    assert "github.com~kospex~panopticas" == kg.repo_id
    print(kg.repo_id)

    kg.set_remote_url("https://gitlab.com/gitlab-org/cloud-connector/gitlab-cloud-connector.git")
    print(kg.repo_id)

    #kospex_id = KospexUtils.git_url_to_repo_id("https://github.com/kospex/kospex")
    #print(kospex_id)
    #assert kospex_id == "github.com~kospex~kospex"

    gitlab_repo_id = KospexGit.generate_repo_id("gitlab.com","gitlab/bob","the_repo")
    assert gitlab_repo_id == "gitlab.com~gitlab~~bob~the_repo"
