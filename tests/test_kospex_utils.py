""" Tests for Kospex Utils """
import kospex_utils as KospexUtils

# Tests

def test_developer_tenure():
    """
    Test the tenure default ordered dict for status checking
    """
    assert "Single day" == KospexUtils.get_status(0)
    assert "< 3 months" == KospexUtils.get_status(15)

    assert KospexUtils.DEFAULT_MAX_STATUS == KospexUtils.get_status(731)
