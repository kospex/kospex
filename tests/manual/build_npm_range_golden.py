"""Rebuild tests/data/npm_range_golden.json from node-semver's own verdicts.

Not part of pytest: it needs node and the npm `semver` package.

    mkdir /tmp/semver && (cd /tmp/semver && npm init -y && npm install semver@7.6.3)
    python tests/manual/build_npm_range_golden.py --semver-dir /tmp/semver/node_modules/semver

By default it re-judges the ranges already in the golden file, so the corpus
stays fixed and only node-semver's answers are refreshed. `--from-db PATH`
samples a new corpus from a kospex DB instead: one or two declared npm
versions per syntactic shape, leaving out anything containing `:` or `/`
(URLs, paths, aliases), which can name private repositories.
"""
import argparse
import collections
import json
import pathlib
import re
import sqlite3
import subprocess
import sys

TESTS = pathlib.Path(__file__).resolve().parent.parent
GOLDEN = TESTS / "data" / "npm_range_golden.json"
sys.path.insert(0, str(TESTS))
from npm_range_pool import candidate_pool  # noqa: E402  the pool both sides use

NODE = r"""
const semver = require(process.argv[1]);
const cases = JSON.parse(require("fs").readFileSync(0, "utf8"));
const out = cases.map(({range, pool}) => {
  if (semver.validRange(range) === null) return {range, satisfying: null};
  return {range, satisfying: pool.map(v => semver.satisfies(v, range) ? "1" : "0").join("")};
});
process.stdout.write(JSON.stringify(out));
"""


def sample_from_db(path, per_shape=2):
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    ranges = sorted(r for (r,) in db.execute(
        "SELECT DISTINCT package_version FROM dependency_data WHERE package_type = 'npm'")
        if r and r.strip() and ":" not in r and "/" not in r)
    picked = collections.defaultdict(list)
    for r in ranges:
        shape = re.sub(r"[1-9]\d*", "N", r)
        if len(picked[shape]) < per_shape:
            picked[shape].append(r)
    return [r for rs in picked.values() for r in rs]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--semver-dir", required=True, help="path to node_modules/semver")
    ap.add_argument("--from-db", help="sample a new corpus from this kospex DB")
    args = ap.parse_args()

    if args.from_db:
        ranges = sample_from_db(args.from_db)
    else:
        ranges = [c["range"] for c in json.loads(GOLDEN.read_text())]

    payload = json.dumps([{"range": r, "pool": candidate_pool(r)} for r in ranges])
    result = subprocess.run(["node", "-e", NODE, args.semver_dir], input=payload,
                            capture_output=True, text=True, check=True)
    cases = json.loads(result.stdout)
    GOLDEN.write_text(json.dumps(cases, indent=0) + "\n")
    print(f"wrote {len(cases)} ranges to {GOLDEN} "
          f"({sum(c['satisfying'] is None for c in cases)} rejected by node-semver)")


if __name__ == "__main__":
    main()
