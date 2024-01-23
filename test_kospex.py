""" Tests for kospex """
from kospex_core import Kospex
import krunner_utils as KrunnerUtils
import kospex_utils as KospexUtils

def test_kospex():
    """ Test object creation works"""
    k = Kospex()
    assert k is not None

# Tests for krunner_utils

def test_extract_grep_parameters():
    """ Test the extract_grep_parameters function """
    # Test with a valid input string
    input_string = "./kospex_utils.py:12:    # Split the input string at colons"
    expected = ["kospex_utils.py", "12", "    # Split the input string at colons"]
    assert KrunnerUtils.extract_grep_parameters(input_string) == expected

    # Test with a valid input string
    input_string = "./kospex_utils.py:12:    # Split the input string at colons:"
    expected = ["kospex_utils.py", "12", "    # Split the input string at colons:"]
    assert KrunnerUtils.extract_grep_parameters(input_string) == expected

    input_string = "one:two:three:four:five"
    expected = ["one", "two", "three:four:five"]
    assert KrunnerUtils.extract_grep_parameters(input_string) == expected


# Tests for kospex_utils

def test_git_rename_event():
    """ Test the parse_git_rename_event function """
    # KospexUtils.parse_git_rename_event()

    # Test with a simple rename
    input_string = "{bin/act => utilities/developer_utils/act}"
    expected = "utilities/developer_utils/act"
    assert KospexUtils.parse_git_rename_event(input_string) == expected

    # Test a plain string is just returned as a string
    input_string = "bin/act/utilities"
    assert input_string == KospexUtils.parse_git_rename_event(input_string)

    # Check a double braces weird filename is just returned as a string
    no_rename_with_braces = "/cookiecutter/app_name/static/css/{{cookiecutter.app_name}}.css"
    assert no_rename_with_braces == KospexUtils.parse_git_rename_event(no_rename_with_braces)

    # Check a replacement with another double braces weird filename
    # pylint: disable=line-too-long
    replace_and_2braces = '{bin/act => utilities/developer_utils}/cookiecutter/static/css/{{cookiecutter.app_name}}.css'
    expected = 'utilities/developer_utils/cookiecutter/static/css/{{cookiecutter.app_name}}.css'
    assert expected == KospexUtils.parse_git_rename_event(replace_and_2braces)
