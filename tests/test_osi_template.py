"""Render tests for the /osi/ page (sub-project B)."""
import kweb2


def _render(context):
    tmpl = kweb2.templates.get_template("osi.html")
    base = {"request": None, "data": [], "file_number": 0,
            "dep_files": [], "status": {},
            "commentary": {"no_parser": {}, "not_a_source": {}, "not_scanned": 0},
            "status_days": {"active": 90, "aging": 180, "stale": 365}}
    base.update(context)
    return tmpl.render(base)


def test_extracted_badges_and_commentary():
    rows = [
        {"_repo_id": "s~o~r", "_git_repo": "r", "Provider": "requirements.txt",
         "committer_when": "2025-01-01", "status": "Active", "days_ago": 1, "extracted": True},
        {"_repo_id": "s~o~r", "_git_repo": "r", "Provider": "yarn.lock",
         "committer_when": "2025-01-01", "status": "Active", "days_ago": 1, "extracted": False},
    ]
    commentary = {"no_parser": {"Package manifests": {"yarn.lock": 49}},
                  "not_a_source": {"SCA config": {"dependabot.yml": 40}},
                  "not_scanned": 5}
    html = _render({"data": rows, "file_number": 2, "commentary": commentary})
    assert "Extracted?" in html
    assert "Yes" in html and "No" in html
    assert "Package manifests" in html and "yarn.lock" in html and "49" in html
    assert "Recognised, but not scanned for packages" in html and "SCA config" in html
    assert "not yet scanned" in html.lower()


def test_extracted_file_links_to_its_dependencies():
    rows = [
        {"_repo_id": "s~o~r", "_git_repo": "r", "Provider": "docs/requirements.txt",
         "committer_when": "2025-01-01", "extracted": True},
        {"_repo_id": "s~o~r", "_git_repo": "r", "Provider": "yarn.lock",
         "committer_when": "2025-01-01", "extracted": False},
    ]
    html = _render({"data": rows, "file_number": 2})
    assert 'href="/dependencies/s~o~r?file_path=docs/requirements.txt"' in html
    # Nothing extracted => no dependencies to show, so no link
    assert "file_path=yarn.lock" not in html


def test_all_extracted_message():
    html = _render({"commentary": {"no_parser": {}, "not_a_source": {}, "not_scanned": 0}})
    assert "all discovered dependency files have extracted details" in html.lower()


def test_coverage_lives_in_dependency_files_card_behind_toggle():
    html = _render({"file_number": 3})
    assert "Dependency Files and Coverage" in html
    assert "Dependency extraction coverage" not in html
    # Coverage detail is collapsed by default, revealed by the show/hide link
    assert 'id="coverage_details" class="mt-4 hidden"' in html
    assert 'id="coverage_toggle"' in html and 'aria-expanded="false"' in html
    assert ">show details<" in html
    # Count line comes before the collapsed details
    assert html.index("3 dependency files found") < html.index('id="coverage_details"')


def test_coverage_toggle_is_wired_before_the_file_table():
    # On the all-repos page the Individual Files table is ~33 MB, and jQuery
    # loads after it, so a handler bound in $(document).ready left the link
    # dead for seconds after it was on screen. The binding must sit right
    # after the toggle, in plain JS, ahead of the table.
    html = _render({"file_number": 3})
    bind = html.index('getElementById("coverage_toggle")')
    assert html.index('id="coverage_toggle"') < bind < html.index('id="myTable"')
    assert '$("#coverage_toggle")' not in html


def test_unsupported_warning_visible_without_expanding_details():
    import kospex_web as KospexWeb
    files = [{"_repo_id": "s~o~r", "Provider": p}
             for p in ("a/build.gradle", "b/build.gradle", "package.json")]
    _, commentary = KospexWeb.osi_extraction_view(files, extracted_keys=set())
    html = _render({"commentary": commentary, "file_number": 3,
                    "unsupported": KospexWeb.unsupported_dependency_files(files)})
    # Outside the collapsed #coverage_details block
    visible = html.split('id="coverage_details"')[0]
    assert 'id="unsupported_files"' in visible
    assert "build.gradle" in visible
    assert ">explain why<" in html
    # The toggle sits under the warning it explains
    assert html.index('id="unsupported_files"') < html.index('id="coverage_toggle"')


def test_column_descriptions_match_development_status_bands():
    import re
    import kospex_utils as KospexUtils
    days = {"active": KospexUtils.DEV_STATUS_ACTIVE_DAYS,
            "aging": KospexUtils.DEV_STATUS_AGING_DAYS,
            "stale": KospexUtils.DEV_STATUS_STALE_DAYS}
    html = _render({"status_days": days})
    details = html.split('id="column_details"')[1].split("</dl>")[0]
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", details))

    # Each band's stated edges give the status development_status() returns there
    edges = {"Active": (0, days["active"]),
             "Aging": (days["active"] + 1, days["aging"]),
             "Stale": (days["aging"] + 1, days["stale"])}
    for status, (low, high) in edges.items():
        assert KospexUtils.development_status(low) == status
        assert KospexUtils.development_status(high) == status
    assert KospexUtils.development_status(days["stale"] + 1) == "Unmaintained"

    assert f"Active - modified in the last {days['active']} days" in text
    assert f"Aging - last modified {days['active'] + 1} to {days['aging']} days ago" in text
    assert f"Stale - last modified {days['aging'] + 1} to {days['stale']} days ago" in text
    assert f"Unmaintained - not modified for more than {days['stale']} days" in text
    assert "Extracted? - kospex has parsed and enriched" in text
    # Moved into the descriptions, not left under the table as well
    assert html.count("has parsed and enriched dependency details") == 1


def test_fix_commands_fill_in_a_repo_and_use_placeholders_otherwise():
    import kospex_web as KospexWeb
    repo = "github.com~androidx~androidx"
    assert KospexWeb.osi_fix_commands(repo, repo) == {
        "osi_cmd": f"krunner osi {repo}",
        "repo_dir": "$KOSPEX_CODE/github.com/androidx/androidx"}
    nested = "gitlab.com~group~~sub~repo"
    assert KospexWeb.osi_fix_commands(nested, nested)["repo_dir"] == \
        "$KOSPEX_CODE/gitlab.com/group/sub/repo"
    assert KospexWeb.osi_fix_commands("github.com~androidx") == {
        "osi_cmd": "krunner osi github.com~androidx",
        "repo_dir": "$KOSPEX_CODE/SERVER/ORG/REPO"}
    assert KospexWeb.osi_fix_commands(None)["osi_cmd"] == "krunner osi -all"


def test_fix_commands_keep_shell_syntax_out():
    # CWE-77: the id comes from the request path and the commands are pasted into a shell
    import kospex_web as KospexWeb
    for bad in ("github.com~x$(id)~y", "github.com~x;id~y", "x`id`"):
        fix = KospexWeb.osi_fix_commands(bad, bad)
        assert fix == {"osi_cmd": "krunner osi <scope>",
                       "repo_dir": "$KOSPEX_CODE/SERVER/ORG/REPO"}, bad


def test_not_scanned_fix_lists_both_commands():
    commentary = {"no_parser": {}, "not_a_source": {}, "not_scanned": 5}
    html = _render({"commentary": commentary, "file_number": 5, "osi_cmd": "krunner osi s~o~r",
                    "repo_dir": "$KOSPEX_CODE/s/o/r"})
    assert "To fix, run:" in html
    fix = html.split('id="not_scanned_fix"')[1].split("</pre>")[0]
    lines = [line.strip() for line in fix.split(">", 1)[1].splitlines()]
    assert lines == [
        "krunner osi s~o~r    # sync all supported dependencies",
        "or",
        "cd $KOSPEX_CODE/s/o/r",
        "kospex sca -save &lt;path to dependency file&gt;    # sync a single file",
    ]


def test_toggle_says_show_details_when_nothing_to_explain():
    html = _render({"file_number": 1, "unsupported": None})
    assert ">show details<" in html
    assert 'id="unsupported_files"' not in html
