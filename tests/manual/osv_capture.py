"""Capture real OSV responses as test fixtures (tests/data/osv/*.json). Manual; live requests.

    python tests/manual/osv_capture.py

Only public package names and versions are sent. Each `affected[].versions` list is
cut to its first three entries (a record can list thousands) and `details` to 400
characters: the code under test reads neither. Everything else is as OSV returned it.
Run it again to refresh the fixtures, and read the diff.
"""
import json
import urllib.request
from pathlib import Path

BASE = "https://api.osv.dev/v1"
OUT = Path(__file__).resolve().parent.parent / "data" / "osv"

QUERIES = {
    # a plain GHSA record with a CVSS v3 vector, aliases and a fixed event
    "pypi_requests_2.25.0": ("PyPI", "requests", "2.25.0"),
    # CVSS v4-only records, and records that name several packages
    "maven_log4j_core_2.14.1": ("Maven", "org.apache.logging.log4j:log4j-core", "2.14.1"),
    # a record whose affected[] spans packages in other ecosystems (lodash-rails is RubyGems)
    "npm_lodash_4.17.15": ("npm", "lodash", "4.17.15"),
}


def post(path, body):
    request = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def trim(vuln, version):
    for affected in vuln.get("affected", []):
        if "versions" in affected:
            listed = affected["versions"]
            affected["versions"] = [v for v in listed[:2] if v != version] + \
                ([version] if version in listed else [])
    if len(vuln.get("details", "")) > 400:
        vuln["details"] = vuln["details"][:400]
    return vuln


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for name, (ecosystem, package, version) in QUERIES.items():
        query = {"package": {"name": package, "ecosystem": ecosystem}, "version": version}
        response = post("/query", query)
        response["vulns"] = [trim(v, version) for v in response.get("vulns", [])]
        (OUT / f"{name}.json").write_text(
            json.dumps({"query": query, "response": response}, indent=1, sort_keys=True) + "\n")
        print(f"{name}: {len(response['vulns'])} vulnerabilities")


if __name__ == "__main__":
    main()
