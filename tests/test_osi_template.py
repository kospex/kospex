"""Render tests for the /osi/ page (sub-project B)."""
import kweb2


def _render(context):
    tmpl = kweb2.templates.get_template("osi.html")
    base = {"request": None, "data": [], "file_number": 0,
            "dep_files": [], "status": {}, "commentary": {"no_parser": {}, "not_scanned": 0}}
    base.update(context)
    return tmpl.render(base)


def test_extracted_badges_and_commentary():
    rows = [
        {"_repo_id": "s~o~r", "_git_repo": "r", "Provider": "requirements.txt",
         "committer_when": "2025-01-01", "status": "Active", "days_ago": 1, "extracted": True},
        {"_repo_id": "s~o~r", "_git_repo": "r", "Provider": "yarn.lock",
         "committer_when": "2025-01-01", "status": "Active", "days_ago": 1, "extracted": False},
    ]
    commentary = {"no_parser": {"package": {"yarn.lock": 49}, "container": {"Dockerfile": 3}},
                  "not_scanned": 5}
    html = _render({"data": rows, "file_number": 2, "commentary": commentary})
    assert "Extracted?" in html          # new column header
    assert "Yes" in html and "No" in html
    assert "yarn.lock" in html and "49" in html   # commentary names the type + count
    assert "not yet scanned" in html.lower()      # the not_scanned line


def test_all_extracted_message():
    html = _render({"commentary": {"no_parser": {}, "not_scanned": 0}})
    assert "all discovered dependency files have extracted details" in html.lower()
