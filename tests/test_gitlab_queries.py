"""GitLab group and project listing, and what goes into a clone list."""
from datetime import datetime, timezone

import pytest

from kospex.integrations.gitlab.client import GitLabError
from kospex.integrations.gitlab.queries import (
    active_projects,
    clone_urls,
    group_ref,
    list_groups,
    list_projects,
    printable,
    skip_reason,
    without_secrets,
)

SERVER = "https://gitlab.example.com"
NOW = datetime(2026, 11, 27, tzinfo=timezone.utc)


class FakeClient:
    def __init__(self, items=(), server=SERVER):
        self.server = server
        self.items = list(items)
        self.calls = []

    def paginate(self, path, params=None):
        self.calls.append((path, params))
        return list(self.items)


def project(name, **kw):
    return {"path_with_namespace": f"acme/{name}",
            "http_url_to_repo": f"{SERVER}/acme/{name}.git",
            "ssh_url_to_repo": f"git@gitlab.example.com:acme/{name}.git", **kw}


@pytest.mark.parametrize("value, expected", [
    ("acme/platform", "acme/platform"),
    ("/acme/platform/", "acme/platform"),
    ("42", "42"),
    (f"{SERVER}/groups/acme/platform", "acme/platform"),
    (f"{SERVER}/acme/platform", "acme/platform"),
    (f"{SERVER}/groups/acme/platform/-/group_members", "acme/platform"),
])
def test_group_ref(value, expected):
    assert group_ref(value, SERVER) == expected


def test_group_ref_under_a_path_install():
    assert group_ref("https://host/gitlab/groups/acme", "https://host/gitlab") == "acme"


@pytest.mark.parametrize("value", ["", "  ", "/", "https://other.example.com/groups/acme",
                                   f"{SERVER}/"])
def test_group_ref_refuses(value):
    with pytest.raises(GitLabError):
        group_ref(value, SERVER)


def test_list_projects_asks_for_the_whole_tree_without_shared_projects():
    client = FakeClient([project("b"), project("A")])
    found = list_projects(client, "acme/platform")
    assert client.calls == [("/groups/acme%2Fplatform/projects", {
        "include_subgroups": "true", "with_shared": "false",
        "order_by": "id", "sort": "asc"})]
    assert [p["path_with_namespace"] for p in found] == ["acme/A", "acme/b"]


def test_list_groups():
    client = FakeClient([{"full_path": "b"}, {"full_path": "A/z"}])
    assert [g["full_path"] for g in list_groups(client)] == ["A/z", "b"]
    assert client.calls == [("/groups", {"order_by": "id", "sort": "asc"})]


def test_list_groups_under_a_group_lists_every_level():
    client = FakeClient()
    list_groups(client, "acme/platform")
    assert client.calls == [("/groups/acme%2Fplatform/descendant_groups",
                             {"order_by": "id", "sort": "asc"})]


@pytest.mark.parametrize("stamp, active", [
    ("2026-08-30T00:00:00Z", True),          # 89 days before NOW
    ("2026-08-28T00:00:00.123Z", False),     # 91 days
    ("2026-11-26T10:00:00+10:00", True),
    (None, False),
    ("not a date", False),
])
def test_active_projects(stamp, active):
    found = active_projects([project("a", last_activity_at=stamp)], 90, now=NOW)
    assert bool(found) is active


MIXED = [
    project("normal"),
    project("fork", forked_from_project={"id": 1}),
    project("archived", archived=True),
    project("empty", empty_repo=True),
    project("archived-fork", archived=True, forked_from_project={"id": 2}),
]


def names(urls):
    return [u.rsplit("/", 1)[1].removesuffix(".git") for u in urls]


def test_skip_reason():
    assert [skip_reason(p) for p in MIXED] == [None, "fork", None, "empty", "fork"]
    assert skip_reason(MIXED[4], include_forks=True, skip_archived=True) == "archived"


@pytest.mark.parametrize("options, expected", [
    ({}, ["normal", "archived"]),
    ({"include_forks": True}, ["normal", "fork", "archived", "archived-fork"]),
    ({"skip_archived": True}, ["normal"]),
])
def test_clone_urls(options, expected):
    assert names(clone_urls(MIXED, **options)) == expected


def test_clone_urls_ssh_and_missing():
    no_url = {"path_with_namespace": "acme/x"}
    assert clone_urls([project("a"), no_url], ssh=True) == [
        "git@gitlab.example.com:acme/a.git"]


@pytest.mark.parametrize("url, reason", [
    (None, "no clone URL"),
    ("", "no clone URL"),
    (f"{SERVER}/acme/a.git\nhttps://other.example.com/x.git", "unsafe clone URL"),
    (f"{SERVER}/acme/\x1b[31ma.git", "unsafe clone URL"),
    (f"{SERVER}/acme/a b.git", "unsafe clone URL"),
])
def test_a_project_without_a_usable_url_is_left_out_with_a_reason(url, reason):
    item = project("a", http_url_to_repo=url)
    assert skip_reason(item) == reason
    assert clone_urls([item]) == []
    # The other protocol's URL is fine, so SSH still takes it.
    assert skip_reason(item, ssh=True) is None
    assert len(clone_urls([item], ssh=True)) == 1


def test_without_secrets():
    item = project("a", runners_token="s1",
                   forked_from_project={"id": 1, "runners_token": "s2"})
    cleaned = without_secrets([item])
    assert "s1" not in str(cleaned) and "s2" not in str(cleaned)
    assert cleaned[0]["forked_from_project"] == {"id": 1}
    assert item["runners_token"] == "s1"


def test_printable():
    assert printable("a\x1b[31mb\n") == "a\\x1b[31mb\\x0a"
    assert printable("x\x7f\x9b") == "x\\x7f\\x9b"
    assert printable(None) == ""
    assert printable(42) == "42"
    assert printable("ünï [red]") == "ünï [red]"
