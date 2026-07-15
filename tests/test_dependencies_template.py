"""Regression test for the /dependencies/ page rendering.

`versions_behind` can be a non-numeric value ("Unknown" from krunner osi when
deps.dev can't resolve a package@version, or "" from sca), and the template
compared it with `> 0`, which raised TypeError on the truthy "Unknown" string
and 500'd the page. The template must tolerate non-numeric values.
"""
import kweb2


def _render(rows):
    tmpl = kweb2.templates.get_template("dependencies.html")
    return tmpl.render({"data": rows, "request": None})


def test_dependencies_renders_with_non_numeric_versions_behind():
    rows = [
        {"package_name": "a", "package_version": "3.0", "package_type": "pypi",
         "versions_behind": 3},          # numeric, behind -> shows the count
        {"package_name": "b", "package_version": "^1.0", "package_type": "npm",
         "versions_behind": "Unknown"},  # truthy string -> used to crash
        {"package_name": "c", "package_version": "1.0", "package_type": "pypi",
         "versions_behind": ""},         # empty string (sca)
        {"package_name": "d", "package_version": "1.0", "package_type": "pypi",
         "versions_behind": None},       # null
        {"package_name": "e", "package_version": "1.0", "package_type": "pypi",
         "versions_behind": 0},          # up to date
    ]

    html = _render(rows)  # must not raise

    assert "3" in html            # the numeric-behind count is shown
    assert "Up to date" in html   # non-numeric / 0 rows fall to the up-to-date branch
    # every row rendered
    for name in ("a", "b", "c", "d", "e"):
        assert name in html


def test_dependencies_shows_resolution_category():
    rows = [
        {"package_name": "a", "package_type": "pypi", "package_version": "1", "resolution": "resolved", "versions_behind": 3},
        {"package_name": "b", "package_type": "npm", "package_version": "^1", "resolution": "unresolved_spec", "versions_behind": None},
        {"package_name": "c", "package_type": "pypi", "package_version": "9", "resolution": "package_not_found", "versions_behind": None},
        {"package_name": "d", "package_type": "pypi", "package_version": "0", "resolution": "resolved", "versions_behind": 0},
    ]
    html = _render(rows)   # helper already in this file
    assert "3" in html and "Up to date" in html
    assert "unresolved spec" in html.lower()
    assert "not found" in html.lower()
