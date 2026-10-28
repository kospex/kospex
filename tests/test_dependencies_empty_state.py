"""An empty /dependencies/ page must say why, and what to run.

A newly synced repo has file metadata but no dependency_data until `krunner osi`
examines it, and the page used to render an empty table with no explanation --
indistinguishable from a repo that genuinely declares nothing.
"""
import re

import kweb2
import kospex_web as KospexWeb

REPO = "github.com~acme~app"


def _files(*paths):
    return [{"_repo_id": REPO, "Provider": p} for p in paths]


def _state(**kwargs):
    args = {"scope_id": REPO, "repo_scope": True, "repo": {"_repo_id": REPO, "file_path": "/code/acme/app",
                                       "last_sync": "2026-10-26 14:40:57"},
            "file_count": 100, "dependency_files": [], "outcome": None}
    args.update(kwargs)
    return KospexWeb.dependencies_empty_state(**args)


def test_repo_not_synced():
    state = _state(repo=None, file_count=0)
    assert state["reason"] == "not_synced"
    assert any("kgit clone" in a for a in state["actions"])


def test_no_file_metadata_points_at_sync_then_osi():
    state = _state(file_count=0)
    assert state["reason"] == "no_file_metadata"
    assert state["actions"] == ["kospex sync-directory /code/acme/app", f"krunner osi {REPO}"]


def test_osi_never_run_counts_dependency_files():
    deps = _files("package.json", "app/build.gradle", "lib/build.gradle")
    state = _state(dependency_files=deps)
    assert state["reason"] == "osi_not_run"
    assert state["actions"] == [f"krunner osi {REPO}"]
    assert "3 dependency files" in state["detail"]
    assert state["actions_label"] == "1 of them can be analysed by running:"


def test_osi_never_run_with_nothing_parseable_says_so():
    state = _state(dependency_files=_files("build.gradle"))
    assert state["actions_label"].startswith("None of them can be analysed yet")
    assert state["actions"] == [f"krunner osi {REPO}"]


def test_no_dependency_files_needs_no_action():
    state = _state(dependency_files=[])
    assert state["reason"] == "no_dependency_files"
    assert state["actions"] == []


def test_examined_but_nothing_extracted_summarises_outcomes():
    outcome = {"examined_at": "2026-10-27T01:00:00Z",
               "files": {"build.gradle": "unsupported", "a/build.gradle": "unsupported",
                         "package.json": "parse_error"}}
    state = _state(dependency_files=_files("build.gradle"), outcome=outcome)
    assert state["reason"] == "nothing_extracted"
    assert "2026-10-27" in state["detail"]
    assert "2 with no parser" in state["detail"]
    assert "1 unreadable" in state["detail"]
    assert state["actions"] == []          # examined after the last sync
    assert state["osi_url"] == f"/osi/{REPO}"


def test_examined_before_last_sync_suggests_rerun():
    outcome = {"examined_at": "2026-10-01T00:00:00Z", "files": {"build.gradle": "unsupported"}}
    state = _state(dependency_files=_files("build.gradle"), outcome=outcome)
    assert state["reason"] == "nothing_extracted"
    assert state["actions"] == [f"krunner osi {REPO}"]


def test_file_path_filter_uses_that_files_outcome():
    outcome = {"examined_at": "2026-10-27T01:00:00Z", "files": {"build.gradle": "unsupported"}}
    state = _state(dependency_files=_files("build.gradle"), outcome=outcome,
                   file_path="build.gradle")
    assert state["reason"] == "file_not_extracted"
    assert "no parser" in state["detail"]


def test_wider_scope_suggests_osi_for_that_scope():
    state = KospexWeb.dependencies_empty_state(scope_id="github.com~acme")
    assert state["reason"] == "no_data"
    assert state["actions"] == ["krunner osi github.com~acme"]
    state = KospexWeb.dependencies_empty_state(scope_id=None)
    assert state["actions"] == ["krunner osi -all"]


# --- unsupported files ----------------------------------------------------

def test_unsupported_counts_files_with_no_parser_by_name():
    deps = _files("package.json", "a/build.gradle", "b/build.gradle", "yarn.lock", ".nvmrc")
    summary = KospexWeb.unsupported_dependency_files(deps)
    assert summary["total"] == 5
    assert summary["count"] == 4
    assert summary["by_name"] == {"build.gradle": 2, "yarn.lock": 1, ".nvmrc": 1}
    assert list(summary["by_name"])[0] == "build.gradle"     # most common first


def test_unsupported_excludes_integrity_files():
    # go.sum holds checksums, not packages -- nothing is missing for it.
    summary = KospexWeb.unsupported_dependency_files(_files("go.sum", "go.mod"))
    assert summary is None


def test_unsupported_agrees_with_osi_no_parser_commentary():
    # Same rule as the /osi/ "format not currently supported" list, so the two
    # pages report the same count. Drives osi_extraction_view, not a copy of it.
    paths = ("package.json", "build.gradle", "x/build.gradle", "yarn.lock", ".nvmrc",
             "go.sum", "requirements.txt", "Dockerfile")
    _, commentary = KospexWeb.osi_extraction_view(_files(*paths), extracted_keys=set())
    osi_count = sum(n for names in commentary["no_parser"].values() for n in names.values())
    assert osi_count > 0
    assert KospexWeb.unsupported_dependency_files(_files(*paths))["count"] == osi_count


def test_unsupported_none_when_everything_has_a_parser():
    assert KospexWeb.unsupported_dependency_files(_files("package.json")) is None
    assert KospexWeb.unsupported_dependency_files([]) is None


# --- template -------------------------------------------------------------

def _render(context):
    tmpl = kweb2.templates.get_template("dependencies.html")
    return tmpl.render({"request": None, **context})


def test_empty_page_shows_message_not_table():
    state = _state(dependency_files=_files("build.gradle"))
    html = _render({"data": [], "empty_state": state})
    assert 'id="no_dependencies"' in html
    assert state["title"] in html
    assert f"krunner osi {REPO}" in html
    assert state["actions_label"] in html
    assert 'id="myTable"' not in html


def test_unsupported_warning_shows_with_data_and_links_to_osi():
    row = {"_repo_id": REPO, "file_path": "package.json", "package_type": "npm",
           "package_name": "left-pad", "package_version": "1.0.0", "versions_behind": 0}
    unsupported = KospexWeb.unsupported_dependency_files(
        _files("package.json", "a/build.gradle", "b/build.gradle"))
    html = _render({"data": [row], "unsupported": unsupported, "osi_url": f"/osi/{REPO}"})
    assert 'id="unsupported_files"' in html
    visible = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html))
    assert "2 of 3 dependency files not analysed" in visible
    assert "build.gradle (2)" in visible
    assert f'href="/osi/{REPO}"' in html
    assert 'id="myTable"' in html


def test_no_unsupported_warning_without_unsupported_files():
    html = _render({"data": [], "unsupported": None})
    assert 'id="unsupported_files"' not in html


def test_page_with_data_shows_table_not_message():
    row = {"_repo_id": REPO, "file_path": "package.json", "package_type": "npm",
           "package_name": "left-pad", "package_version": "1.0.0", "versions_behind": 0}
    html = _render({"data": [row], "empty_state": None})
    assert 'id="myTable"' in html
    assert 'id="no_dependencies"' not in html


# --- suggested commands (CWE-77) ------------------------------------------
# The scope id comes from the request path and is shown in a "To fix, run:"
# box to be pasted into a shell, so shell syntax in it must never reach the box.

def test_scope_id_with_shell_syntax_is_replaced_by_a_placeholder():
    for bad in ("x;rm -rf ~", "x$(id)", "x`id`", "x|sh", "x && y", "x\nid", "x'y"):
        state = KospexWeb.dependencies_empty_state(scope_id=bad)
        assert state["actions"] == ["krunner osi <scope>"], bad
        state = _state(scope_id=bad, repo=None, file_count=0)
        assert bad not in " ".join(state["actions"]), bad


def test_real_ids_are_kept_in_commands():
    for ok in ("github.com", "github.com~acme", "github.com~acme~app.js",
               "gitlab.com~group~~subgroup~repo", "github.com~a_b~c-d"):
        assert KospexWeb.safe_scope_id(ok) == ok


def test_sync_directory_path_is_shell_quoted():
    repo = {"_repo_id": REPO, "file_path": "/code/my repo;id", "last_sync": None}
    state = _state(repo=repo, file_count=0)
    assert state["actions"][0] == "kospex sync-directory '/code/my repo;id'"
