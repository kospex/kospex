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
