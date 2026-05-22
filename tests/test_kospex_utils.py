""" Tests for Kospex Utils """
from pathlib import Path
import kospex_utils as KospexUtils

HERE = Path(__file__).parent

# Tests

def test_developer_tenure():
    """
    Test the tenure default ordered dict for status checking
    """
    assert "Single day" == KospexUtils.get_status(0)
    assert "< 3 months" == KospexUtils.get_status(15)

    assert KospexUtils.DEFAULT_MAX_STATUS == KospexUtils.get_status(731)

def test_mailmap():
    """
    Test mailmap parsing
    """

    results = KospexUtils.parse_mailmap(f"{HERE}/mailmap/github.com/adamtornhill/code-maat/.mailmap")
    print(results)

def test_get_status_legend():
    """get_status_legend returns a rich Table describing the 4 statuses."""
    from rich.table import Table

    legend = KospexUtils.get_status_legend()
    assert isinstance(legend, Table)

    from rich.console import Console
    console = Console(width=120)
    with console.capture() as capture:
        console.print(legend)
    text = capture.get()
    for status in ["Active", "Aging", "Stale", "Unmaintained"]:
        assert status in text
    # thresholds appear in the descriptions
    assert "90" in text
    assert "180" in text
    assert "365" in text

def test_get_status_table_rich():
    """get_status_table returns a rich Table with counts and percentages."""
    from rich.table import Table
    from rich.console import Console

    status = {"Active": 3, "Aging": 1, "Stale": 0, "Unmaintained": 1}
    table = KospexUtils.get_status_table(status)
    assert isinstance(table, Table)

    console = Console(width=120)
    with console.capture() as capture:
        console.print(table)
    text = capture.get()
    for header in ["Active", "Aging", "Stale", "Unmaintained", "Total"]:
        assert header in text
    assert "5" in text       # total count
    assert "%" in text       # percentage row present
