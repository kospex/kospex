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
