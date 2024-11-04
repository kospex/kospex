""" Tests for kospex """
from kospex_core import Kospex
import krunner_utils as KrunnerUtils
import kospex_utils as KospexUtils
from kospex_query import KospexQuery, KospexData

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

    # Test with a valid input string, additional colon
    input_string = "./kospex_utils.py:12:    # Split the input string at colons:"
    expected = ["kospex_utils.py", "12", "    # Split the input string at colons:"]
    assert KrunnerUtils.extract_grep_parameters(input_string) == expected

    # Test with a valid input string, many colons
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

def test_days_functions():
    """ Test the days calculation functions """

    d1 = "2023-08-14T17:06:01+10:00"
    d2 = "2021-03-01T01:10:23+00:00"
    assert KospexUtils.days_between_datetimes(d1, d2) == 896.2

    # The same date should return 0.0
    # (i.e. no days between them and 1 decimal places)
    d1 = "2024-05-06T15:50:01+10:00"
    d2 = d1
    assert KospexUtils.days_between_datetimes(d1, d2) == 0.0

# Tests for KospexData

def test_has_complex_sql():
    """ Test validators for more complex SQL expressions """

    kd = KospexData()
    assert kd.is_valid_sql_name("SUM") is True

    assert kd.has_parentheses("SUM") is False

    # Test a simple SQL expression
    simple_sql = "count(author_email)"
    assert kd.has_parentheses(simple_sql) is True
    assert kd.validate_nested_expressions(simple_sql) is True

    #complex_sql = "Count(Distinct(author_email))"
    complex_sql = "count(distinct(author_email))"
    assert kd.has_parentheses(complex_sql) is True
    assert kd.validate_nested_expressions(complex_sql) is True

    invalid_complex = "cou!nt(distinct(author_email))"
    assert kd.validate_nested_expressions(invalid_complex) is False
