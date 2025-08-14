"""
Tests for KospexGit
"""
from kospex_git import KospexGit

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

    kg = KospexGit()

    sample = "https://github.com/kospex/kospex"
    kg.set_remote_url(sample)
    assert "github.com~kospex~kospex" == kg.repo_id

    kg.set_remote_url("git@github.com:kospex/panopticas.git")
    assert "github.com~kospex~panopticas" == kg.repo_id

    kg.set_remote_url("https://gitlab.com/gitlab-org/cloud-connector/gitlab-cloud-connector.git")
    assert "gitlab.com~gitlab-org~~cloud-connector~gitlab-cloud-connector" == kg.repo_id

    # Bitbucket examples
    # HTTPS
    kg.set_remote_url("https://bitbucket.org/gildas_cherruel/bb.git")
    assert "bitbucket.org~gildas_cherruel~bb" == kg.repo_id
    # SSH
    # git@bitbucket.org:gildas_cherruel/bb.git
    kg.set_remote_url("git@bitbucket.org:gildas_cherruel/bb.git")
    assert "bitbucket.org~gildas_cherruel~bb" == kg.repo_id

    gitlab_repo_id = KospexGit.generate_repo_id("gitlab.com","gitlab/bob","the_repo")
    assert gitlab_repo_id == "gitlab.com~gitlab~~bob~the_repo"
