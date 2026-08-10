"""Render tests for supply_chain.html unresolved-dependency handling — issue #109.

PR #106 normalised `versions_behind` so an unresolved dependency carries `null`
instead of the old `"Unknown"` / `""` sentinel. The bubble graph colours and
sizes nodes by that value in JavaScript, where `null <= 2` evaluates to **true** —
so an unresolved dependency rendered **green / "Up to Date"**, the most reassuring
state available, for a package whose version could not be determined at all.

The same coercion affects size: `d3.scaleLinear()(null)` treats null as 0 and
returns the range minimum, so an unresolved node also drew *smallest*, reading
as "least versions behind".

This is the view-layer half of the unresolved-dependency problem. The data-layer
half is #29, which stops unpinned declarations being dropped before they ever
reach the database. Fixing #29 alone increases the number of null-`versions_behind`
rows, so without this the misreporting simply moves from the data layer to the
view layer, where it is harder to notice.

These assertions are deliberately about the *guard existing*, not about rendered
pixels — the logic is client-side D3, so a Python render test can only verify the
template ships the guard. That is still enough to catch a revert.
"""
import kweb2


def _render():
    tmpl = kweb2.templates.get_template("supply_chain.html")
    return tmpl.render({
        "request": None,
        "data": {"nodes": [], "links": []},
        "package": "pypi:requests:2.0.0",
        "ecosystem": "pypi",
    })


def test_template_guards_null_versions_behind_before_comparing():
    """A null check must precede every numeric versions_behind comparison."""
    html = _render()
    assert "isUnresolved" in html, (
        "expected an isUnresolved() helper guarding null versions_behind; "
        "without it `null <= 2` is true and unresolved deps render green"
    )


def test_unresolved_nodes_get_their_own_status_text():
    """Must be a distinct label, not 'Up to Date'.

    Asserts on "Unresolved" specifically. A bare "Unknown" check would pass
    against the pre-fix template, which already uses that word in the date
    formatter (`if (!dateString) return "Unknown"`) for an unrelated purpose.
    """
    html = _render()
    assert "Unresolved" in html, (
        "unresolved dependencies need a distinct status label, not 'Up to Date'"
    )


def test_legend_documents_the_unresolved_state():
    html = _render()
    assert "Unresolved" in html and "legendItems" in html, (
        "the legend must explain the unresolved colour, or users will read "
        "grey as just another shade of 'fine'"
    )


def test_no_bare_versions_behind_comparison_remains():
    """Every `versions_behind <=` comparison must sit behind the guard.

    Catches a partial fix that updates the fill colour but leaves
    getStatusClass() or getStatusText() coercing null.
    """
    html = _render()
    # The three comparison sites (fill, status class, status text) plus the size
    # scale must all be guarded. Count the guard against the comparisons.
    comparisons = html.count("versions_behind <= 2")
    guards = html.count("isUnresolved")
    assert guards >= comparisons, (
        f"found {comparisons} `versions_behind <= 2` comparisons but only "
        f"{guards} isUnresolved guards — at least one path still coerces null"
    )
