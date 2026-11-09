"""The candidate versions a range is judged against, shared by test and builder.

Used by tests/test_npm_range.py and tests/manual/build_npm_range_golden.py, so
node-semver and kospex are asked about exactly the same versions.
"""
import re


def candidate_pool(range_text):
    """Stable versions either side of every number in the range.

    Picks the boundaries a range can get wrong: each version, one patch / minor /
    major either side, and the 0.x / 0.0.x corners where `^` changes meaning.
    """
    pool = {(0, 0, 0), (0, 0, 1), (0, 1, 0), (0, 1, 1), (1, 0, 0), (2, 0, 0), (99, 0, 0)}
    for m in re.finditer(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?", range_text):
        a, b, c = (int(x) if x is not None else 0 for x in m.groups())
        for v in ((a, b, c), (a, b, c + 1), (a, b + 1, 0), (a + 1, 0, 0),
                  (a, b, c - 1), (a, b - 1, 99), (a - 1, 99, 99), (a, b + 1, 1)):
            if min(v) >= 0 and max(v) < 10**9:
                pool.add(v)
    return [".".join(map(str, v)) for v in sorted(pool)]
