"""The summary page draws one circle per status with its count inside.

A circle can be as small as 40px, so the label is compacted to at most four
characters and the font is sized from the diameter. Before this, a four-digit
count in 18px bold ran past the edge of a 3% circle.
"""

import pytest

import kospex_web as KospexWeb


@pytest.mark.parametrize(
    "n, expected",
    [
        (0, "0"),
        (7, "7"),
        (999, "999"),
        (1000, "1k"),
        (1984, "2k"),
        (3255, "3.3k"),
        (9949, "9.9k"),
        (9950, "10k"),
        (66362, "66k"),
        (999_499, "999k"),
        (999_500, "1M"),
        (1_260_000, "1.3M"),
        (25_000_000, "25M"),
    ],
)
def test_compact_count(n, expected):
    assert KospexWeb.compact_count(n) == expected


def test_compact_count_never_exceeds_four_chars():
    for n in list(range(0, 20_000, 7)) + list(range(20_000, 50_000_000, 99_991)):
        assert len(KospexWeb.compact_count(n)) <= 4, n


def test_label_font_fits_the_smallest_circle():
    # 40px is the smallest circle the summary draws (0% + 40).
    px = KospexWeb.circle_label_px("3.3k", 40)
    assert 12 <= px < 18
    assert px * 0.62 * 4 <= 40


def test_label_font_caps_at_the_original_size():
    assert KospexWeb.circle_label_px("99", 140) == 18


def test_status_circles_shape():
    stats = {"Active": 1984, "Aging": 2788, "Stale": 3255, "Unmaintained": 66362}
    circles = KospexWeb.status_circles(stats)

    assert [c["name"] for c in circles] == ["Active", "Aging", "Stale", "Dormant"]
    dormant = circles[-1]
    assert dormant["count"] == 66362
    assert dormant["label"] == "66k"
    assert dormant["title"] == "66,362"
    assert dormant["percentage"] == 89
    assert dormant["size"] == 129
    active = circles[0]
    assert active["percentage"] == 3
    assert active["size"] == 43
    for c in circles:
        assert len(c["label"]) * 0.62 * c["font_px"] <= c["size"]


def test_status_circles_missing_status_is_zero_and_smallest():
    circles = KospexWeb.status_circles({"Active": 5})
    stale = circles[2]
    assert stale["count"] == 0
    assert stale["label"] == "0"
    assert stale["percentage"] == 0
    assert stale["size"] == 40


def test_status_circles_empty_db():
    circles = KospexWeb.status_circles({})
    assert all(c["count"] == 0 and c["size"] == 40 for c in circles)
