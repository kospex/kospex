"""Snyk REST API: read-only queries behind `kospex snyk`.

- client.py: SnykClient (token, base URL, pinned REST version, cursor paging, errors)
- queries.py: pure functions over issue/project/org JSON (rows, package filter, vuln summary)
- cli.py: the `snyk` Click group added to `kospex`

Design notes: changes/202611-snyk-api-experiment.md
"""
