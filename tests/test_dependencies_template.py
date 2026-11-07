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


_FILE_ROW = {"_repo_id": "s~o~r", "file_path": "docs/requirements.txt", "package_type": "pypi",
             "package_name": "mkdocs", "package_version": "1.6.0", "versions_behind": 0}


def test_file_links_to_that_files_dependencies_in_its_repo():
    html = _render([_FILE_ROW])
    assert 'href="/dependencies/s~o~r?file_path=docs/requirements.txt"' in html


def test_advisories_column_null_is_unknown_zero_is_zero():
    rows = [dict(_FILE_ROW, package_name="none", advisories=0),
            dict(_FILE_ROW, package_name="some", advisories=7),
            dict(_FILE_ROW, package_name="null", advisories=None),
            dict(_FILE_ROW, package_name="legacy", advisories="")]
    html = _render(rows)
    assert ">Advisories</th>" in html
    cells = html.split('data-order="')[1:]
    assert [c.split('"')[0] for c in cells] == ["0", "7", "-1", "-1"]
    assert html.count("bg-yellow-100 text-yellow-800\">Unknown</span>") == 2
    assert "bg-green-100 text-green-800\">0</span>" in html
    assert "bg-red-100 text-red-800\">7</span>" in html


def test_cache_note_shown_only_when_responses_are_cached():
    tmpl = kweb2.templates.get_template("dependencies.html")
    base = {"oldest": "2026-10-20 09:00:00", "newest": "2026-10-20 09:00:00",
            "never_checked": 0, "stale": False, "max_age_days": 2}
    cached = tmpl.render({"data": [_FILE_ROW], "request": None,
                          "freshness": dict(base, cache_window="1 day")})
    assert 'id="cache_note"' in cached and "up to 1 day older than this date" in cached
    uncached = tmpl.render({"data": [_FILE_ROW], "request": None,
                            "freshness": dict(base, cache_window=None)})
    assert 'id="cache_note"' not in uncached


def test_no_self_link_when_already_narrowed_to_a_file():
    tmpl = kweb2.templates.get_template("dependencies.html")
    html = tmpl.render({"data": [_FILE_ROW], "request": None, "file_path": "docs/requirements.txt"})
    assert "?file_path=" not in html


class TestVersionNotStatedInTheFile:
    """A version the file does not state, but kospex looked up (Maven tier 3).

    The declaration is blank and resolved_version holds the version a BOM
    supplied. A blank next to a `resolved` status reads as a contradiction;
    showing resolved_version unmarked would claim the file states it. So it is
    shown, marked as not from the file.
    """

    _BOM_ROW = {"package_name": "io.netty:netty-handler", "package_type": "maven",
                "package_version": "", "resolved_version": "4.2.18.Final",
                "resolution": "resolved", "versions_behind": 0, "advisories": 0}

    def test_blank_declaration_shows_the_looked_up_version_marked(self):
        html = _render([self._BOM_ROW])
        assert "4.2.18.Final" in html
        assert "via BOM" in html

    def test_declared_version_is_shown_unmarked(self):
        html = _render([dict(self._BOM_ROW, package_version="4.2.18.Final")])
        assert "4.2.18.Final" in html
        assert "via BOM" not in html

    def test_range_floor_does_not_replace_the_declared_range(self):
        """resolved_version is also a range's floor; the range is what the
        file says, so it is what is shown."""
        row = {"package_name": "left-pad", "package_type": "npm",
               "package_version": "^1.3.0", "resolved_version": "1.3.0",
               "resolution": "resolved", "versions_behind": 0, "advisories": 0}
        html = _render([row])
        assert "^1.3.0" in html
        assert ">1.3.0<" not in html

    def test_blank_with_nothing_looked_up_stays_blank(self):
        row = dict(self._BOM_ROW, resolved_version="", resolution="no_version",
                   versions_behind=None, advisories=None)
        html = _render([row])
        assert "via BOM" not in html
        assert "no version" in html
