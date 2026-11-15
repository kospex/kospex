"""Snyk REST fixtures shaped from the OpenAPI spec (version 2025-11-05).

The free Snyk plan returns 403 for projects and issues, so these are built
from the schema, not recorded. Field names match the spec exactly, including
its camelCase `resourcePath` / `sourceLocation` inside representations.
"""


def issue(issue_id, org_id, project_id, packages, problems=("SNYK-JS-EXAMPLE-1",),
          severity="high", status="open", ignored=False, upgradeable=True,
          fixable=True, reachability="no-info", title="Example vulnerability"):
    """packages: [(name, version), ...] — one representation each."""
    return {
        "id": issue_id,
        "type": "issue",
        "attributes": {
            "key": f"key-{issue_id}",
            "title": title,
            "type": "package_vulnerability",
            "effective_severity_level": severity,
            "status": status,
            "ignored": ignored,
            "created_at": "2026-10-01T00:00:00Z",
            "updated_at": "2026-11-01T00:00:00Z",
            "problems": [{"id": p, "source": "SNYK" if p.startswith("SNYK-") else "NVD",
                          "type": "vulnerability"} for p in problems],
            "coordinates": [{
                "is_upgradeable": upgradeable,
                "is_fixable_snyk": fixable,
                "is_fixable_manually": False,
                "is_fixable_upstream": False,
                "is_patchable": False,
                "is_pinnable": False,
                "reachability": reachability,
                "state": status,
                "representations": [
                    {"dependency": {"package_name": n, "package_version": v}}
                    for n, v in packages
                ],
            }],
        },
        "relationships": {
            "organization": {"data": {"id": org_id, "type": "organization"}},
            "scan_item": {"data": {"id": project_id, "type": "project"}},
        },
    }


def org(org_id, slug, name=None, is_personal=False):
    return {"id": org_id, "type": "org",
            "attributes": {"slug": slug, "name": name or slug,
                           "is_personal": is_personal, "group_id": None}}


def project(project_id, org_id, name, counts=None, origin="github", ptype="maven"):
    item = {
        "id": project_id, "type": "project",
        "attributes": {"name": name, "type": ptype, "origin": origin,
                       "status": "active", "target_reference": "main",
                       "target_file": "pom.xml"},
        "relationships": {"organization": {"data": {"id": org_id, "type": "org"}},
                          "target": {"data": {"id": f"t-{project_id}", "type": "target"}}},
    }
    if counts is not None:
        item["meta"] = {"latest_issue_counts": {**counts,
                                                "updated_at": "2026-11-10T00:00:00Z"}}
    return item


JACKSON = "com.fasterxml.jackson.core:jackson-databind"
