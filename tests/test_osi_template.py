"""Render tests for the /osi/ page (sub-project B)."""
import kweb2


def _render(context):
    tmpl = kweb2.templates.get_template("osi.html")
    base = {"request": None, "data": [], "file_number": 0,
            "dep_files": [], "status": {},
            "commentary": {"no_parser": {}, "not_a_source": {}, "not_scanned": 0}}
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
