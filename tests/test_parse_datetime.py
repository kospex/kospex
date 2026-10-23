"""_parse_datetime: an ISO 8601 fast path in front of dateutil.

days_between_datetimes() parsed both arguments with dateutil.parser.parse, at
~17us a call. developers() calls it once per developer, which was 2.54s of a
3.94s call on a 72,649-developer estate. datetime.fromisoformat does the same
work in C. dateutil stays as the fallback, so the two must agree wherever the
fast path succeeds, and anything the fast path rejects must behave exactly as
before, including raising on the same input.
"""
import pytest
from dateutil import parser

import kospex_utils as KospexUtils

# Shapes stored in, or produced from, the kospex DB
SAMPLES = [
    "2026-10-17T00:30:00Z",              # _select_date_aggregate / repos() output
    "2025-12-30T22:51:28-08:00",         # commits.committer_when
    "2026-10-17T11:26:51+11:00",         # repos.last_sync
    "2026-10-16T11:23:57.123456+00:00",  # microseconds
    "2026-10-16 11:23:57",               # space separator, naive
    "2026-10-16",                        # date only
]

@pytest.mark.parametrize("value", SAMPLES)
def test_matches_dateutil(value):
    fast = KospexUtils._parse_datetime(value)
    slow = parser.parse(value)
    assert fast == slow
    assert (fast.tzinfo is None) == (slow.tzinfo is None)
    assert fast.utcoffset() == slow.utcoffset()


def test_broken_git_offset_behaves_as_before():
    """'+518:00' is in real commit data (4 commits). fromisoformat rejects it.
    dateutil does NOT raise: it returns a datetime whose offset errors only
    when used. So days_between_datetimes' except ValueError catches it and
    returns 0, before and after."""
    bad, good = "2011-09-08T02:38:50+518:00", "2026-10-17T00:30:00Z"
    assert type(KospexUtils._parse_datetime(bad).tzinfo) is type(parser.parse(bad).tzinfo)
    assert KospexUtils.days_between_datetimes(bad, good) == 0


def test_multiline_first_seen_still_raises():
    """35 repos.first_seen values hold several newline-separated dates.
    dateutil raises on them, and so must the fallback. get_repos() splits
    them before calling."""
    value = "2016-08-13T23:48:33-04:00\n2014-09-28T23:39:22+10:00"
    with pytest.raises(parser.ParserError):
        parser.parse(value)
    with pytest.raises(parser.ParserError):
        KospexUtils._parse_datetime(value)


def test_non_iso_falls_back_to_dateutil():
    """A format fromisoformat rejects but dateutil reads is still read."""
    value = "Sat Oct 17 00:30:00 2026 +0000"
    assert KospexUtils._parse_datetime(value) == parser.parse(value)


def test_fast_path_does_not_call_dateutil(monkeypatch):
    """The point of the change: ISO input must not reach dateutil."""
    def fail(*args, **kwargs):
        raise AssertionError("dateutil.parser.parse called for ISO input")
    monkeypatch.setattr(KospexUtils.parser, "parse", fail)
    for value in SAMPLES:
        KospexUtils._parse_datetime(value)


@pytest.mark.parametrize("first,last,min_one,expected", [
    ("2003-09-28T01:20:02Z", "2026-05-06T14:41:31Z", None, 8256.6),
    ("2026-10-17T00:30:00Z", "2026-10-17T10:30:00+11:00", None, 0.0),   # same instant
    ("2026-10-17T00:30:00Z", "2026-10-17T00:30:00Z", True, 1),
    ("2026-01-01", "2026-01-31", None, 30.0),
])
def test_days_between_datetimes_unchanged(first, last, min_one, expected):
    assert KospexUtils.days_between_datetimes(first, last, min_one=min_one) == expected
