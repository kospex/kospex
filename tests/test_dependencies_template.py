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


def _marked(html):
    """True if any row carries a `checked at <version>` line. The table
    explanation also says "checked at", but never followed by a version."""
    import re
    return re.search(r">checked at [^<\s]", html) is not None


class TestRangeShowsTheVersionChecked:
    """A range's numbers describe the version kospex looked up, not the text (#379).

    For a range, versions_behind and advisories are for resolved_version: the
    oldest published version the range allows (`^1` -> 1.12.0). The declared
    text stays the main value; a muted `checked at` line says which version the
    numbers are for, with the meaning in a tooltip and in the table explanation
    under the heading, because a tooltip can't be found by scanning or read on
    a touch screen.
    """

    _RANGE = {"package_name": "@playwright/test", "package_type": "npm",
              "package_version": "^1", "resolved_version": "1.12.0",
              "version_kind": "caret", "resolution": "resolved",
              "versions_behind": 41, "advisories": 0}

    def test_range_row_shows_declared_text_and_the_version_checked(self):
        html = _render([self._RANGE])
        assert "^1" in html
        assert "checked at 1.12.0" in html
        assert "oldest published version" in html          # the tooltip

    def test_each_range_kind_is_marked(self):
        # `excluded` is PyPI's `!=1.0`, a range since #388: checked at its first release.
        for kind in ("caret", "tilde", "gte", "bounded", "excluded"):
            html = _render([dict(self._RANGE, version_kind=kind)])
            assert "checked at 1.12.0" in html, kind

    def test_a_pin_is_not_marked(self):
        html = _render([dict(self._RANGE, package_version="1.12.0", version_kind="pinned")])
        assert not _marked(html)

    def test_nothing_checked_means_nothing_marked(self):
        row = dict(self._RANGE, package_version="1000000000000000000",
                   resolved_version="1000000000000000000", version_kind="bounded",
                   resolution="no_satisfying_version", versions_behind=None)
        html = _render([row])
        assert not _marked(html)
        assert "no matching version" in html

    def test_a_pin_deps_dev_respelled_is_marked(self):
        """resolved_version is deps.dev's spelling; when it differs from the
        pin as written, say so, or the numbers look like another version's."""
        for declared, canonical in (("==2026.3.post1", "2026.3.0.post1"),   # PEP 440
                                    ("13.0.1.0", "13.0.1"),                 # NuGet
                                    ("[13.0.1.0]", "13.0.1")):              # NuGet exact
            row = dict(self._RANGE, package_version=declared, resolved_version=canonical,
                       version_kind="pinned")
            html = _render([row])
            assert f"checked at {canonical}" in html, declared

    def test_a_pin_spelled_the_same_is_not_marked(self):
        for declared, resolved in (("==2.0.0", "2.0.0"), ("[13.0.1]", "13.0.1"),
                                   ("v1.10.0", "v1.10.0"), ("4.13", "4.13")):
            row = dict(self._RANGE, package_version=declared, resolved_version=resolved,
                       version_kind="pinned")
            assert not _marked(_render([row])), declared

    def test_a_bare_go_or_nuget_minimum_is_not_marked(self):
        """go.mod `v1.10.0` and NuGet `3.1.1` are minimums (`gte`), but the line
        would only repeat the declared text -- 1,830 such rows on a dev estate."""
        for system, version in (("go", "v1.10.0"), ("nuget", "3.1.1")):
            row = dict(self._RANGE, package_type=system, package_version=version,
                       resolved_version=version, version_kind="gte")
            assert not _marked(_render([row])), system

    def test_bom_row_is_unchanged(self):
        bom = {"package_name": "io.netty:netty-handler", "package_type": "maven",
               "package_version": "", "resolved_version": "4.2.18.Final",
               "version_kind": "none", "resolution": "resolved",
               "versions_behind": 0, "advisories": 0}
        html = _render([bom])
        assert "via BOM" in html
        assert not _marked(html)


class TestTableExplanation:
    """What `checked at` means, under the "Dependency Analysis" heading as a
    collapsed show/hide link, rather than as a footnote under the table."""

    _ROW = TestRangeShowsTheVersionChecked._RANGE

    def test_explanation_sits_between_the_heading_and_the_table(self):
        html = _render([self._ROW])
        heading = html.index("Dependency Analysis")
        explanation = html.index('id="table_explanation"')
        table = html.index('id="myTable"')
        assert heading < explanation < table

    def test_it_is_collapsed_with_a_show_hide_link(self):
        html = _render([self._ROW])
        tag = html[html.index("<details", html.index("Dependency Analysis")):]
        tag = tag[:tag.index(">") + 1]
        assert 'id="table_explanation"' in tag
        assert " open" not in tag                          # collapsed by default
        assert "Show table explanation" in html
        assert "Hide table explanation" in html

    def test_it_explains_checked_at(self):
        html = _render([self._ROW])
        explanation = html[html.index('id="table_explanation"'):html.index('id="myTable"')]
        explanation = " ".join(explanation.split())      # as the browser renders it
        assert "the version deps.dev looked up" in explanation
        assert "oldest published version the range allows" in explanation
        assert "deps.dev's spelling of the same version" in explanation

    def test_the_footnote_under_the_table_is_gone(self):
        html = _render([self._ROW])
        assert 'id="checked_at_note"' not in html
        after_table = " ".join(html[html.index('id="myTable"'):].split())
        assert "the version deps.dev looked up" not in after_table

    def test_shown_even_without_a_marked_row(self):
        """It explains the table, so it does not depend on which rows are in it."""
        pin = dict(self._ROW, package_version="1.12.0", version_kind="pinned")
        assert 'id="table_explanation"' in _render([pin])
