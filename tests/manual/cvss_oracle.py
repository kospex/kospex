"""Regenerate tests/data/cvss_v3_golden.json with an independent CVSS library.

Manual, not part of pytest. The Red Hat `cvss` package is the oracle: it is
LGPL-3.0, so it is NOT a kospex dependency and must not become one. Install it in a
throwaway venv, run this once, and commit the JSON -- numbers, not code:

    python -m venv /tmp/oracle && /tmp/oracle/bin/pip install cvss
    /tmp/oracle/bin/python tests/manual/cvss_oracle.py [--db ~/kospex/kospex.db]

The committed file holds every v3.0 and v3.1 base vector (4 AV x 2 AC x 3 PR x 2 UI
x 2 S x 3 C x 3 I x 3 A = 2,592 each), every distinct `cvss3Vector` found in the
database's url_cache, and a list of malformed vectors the oracle refuses.
tests/test_cvss.py checks kospex.scoring.cvss against it without importing `cvss`.
"""
import argparse
import itertools
import json
import sqlite3
from importlib.metadata import version as package_version
from pathlib import Path

from cvss import CVSS3
from cvss.exceptions import CVSS3Error

OUT = Path(__file__).resolve().parent.parent / "data" / "cvss_v3_golden.json"
METRICS = [("AV", "NALP"), ("AC", "LH"), ("PR", "NLH"), ("UI", "NR"),
           ("S", "UC"), ("C", "HLN"), ("I", "HLN"), ("A", "HLN")]

MALFORMED = [
    "", "AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H", "CVSS:3.1",
    "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H",
    "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H/A:H",
    "CVSS:3.1/AV:X/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
    "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H/ZZ:Q",
    "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H/",
    "CVSS:3.2/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
    "CVSS:2.0/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
    "CVSS:3.1//AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
    "CVSS:3.1/AV/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
]


def base_vectors():
    for prefix in ("CVSS:3.0", "CVSS:3.1"):
        for values in itertools.product(*(v for _, v in METRICS)):
            yield prefix + "/" + "/".join(f"{k}:{x}" for (k, _), x in zip(METRICS, values))


def cached_vectors(db_path):
    """Every distinct cvss3Vector in url_cache, for vectors seen in the wild."""
    found = set()
    if not db_path or not Path(db_path).exists():
        return found
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as db:
        rows = db.execute("SELECT content FROM url_cache WHERE content LIKE '%cvss3Vector%'")
        for (content,) in rows:
            try:
                vector = json.loads(content).get("cvss3Vector")
            except (ValueError, AttributeError):
                continue
            if isinstance(vector, str) and vector.startswith("CVSS:3"):
                found.add(vector)
    return found


def oracle(vector):
    """(base_score, rating) from the library, or None if it refuses the vector."""
    try:
        parsed = CVSS3(vector)
    except (CVSS3Error, ValueError, KeyError, IndexError):
        return None
    return [float(parsed.base_score), parsed.severities()[0].upper()]


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=str(Path.home() / "kospex" / "kospex.db"))
    args = parser.parse_args()

    vectors = sorted(set(base_vectors()) | cached_vectors(args.db))
    scored, refused = {}, []
    for vector in vectors:
        result = oracle(vector)
        if result is None:
            refused.append(vector)
        else:
            scored[vector] = result
    for vector in MALFORMED:
        if oracle(vector) is None:
            refused.append(vector)
        else:
            raise SystemExit(f"oracle accepted a vector listed as malformed: {vector!r}")

    document = {"generator": {"package": "cvss", "version": package_version("cvss"),
                              "note": "Independent oracle, LGPL-3.0, not a kospex dependency."},
                "scored": scored, "refused": sorted(set(refused))}
    OUT.write_text(json.dumps(document, indent=0, sort_keys=True) + "\n")
    print(f"{len(scored)} scored, {len(set(refused))} refused -> {OUT}")


if __name__ == "__main__":
    main()
