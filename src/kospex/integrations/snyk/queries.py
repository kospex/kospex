"""Pure functions over Snyk REST JSON: flat rows, package matching, vuln summary.

No I/O here, so the shapes can be tested from fixtures built from the OpenAPI
spec, which is all the free plan allows (its issues endpoint returns 403).

Field paths (REST version 2025-11-05):
- issue package: attributes.coordinates[].representations[].dependency.package_name
  / .package_version
- issue vulnerability ids: attributes.problems[].id (SNYK-..., CVE-..., ...)
- issue project: relationships.scan_item.data (type "project"); org:
  relationships.organization.data
- REST has no fixed-in version or introduced-through path (the Export API does).
"""

SEVERITY_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}

ISSUE_FIELDS = (
    "org", "org_id", "project", "project_id",
    "package", "version", "vuln_id", "cves", "title", "severity",
    "status", "ignored", "upgradeable", "fixable", "reachability",
    "issue_id", "created_at", "updated_at",
)

VULN_FIELDS = (
    "package", "vuln_id", "cves", "title", "severity",
    "projects", "orgs", "versions", "upgradeable_projects", "issues",
)

PROJECT_FIELDS = (
    "org", "org_id", "project", "project_id", "type", "origin",
    "target_reference", "target_file", "status",
    "critical", "high", "medium", "low", "counts_updated_at",
)


def _rel_id(item, name, wanted_type=None):
    data = ((item.get("relationships") or {}).get(name) or {}).get("data") or {}
    if wanted_type and data.get("type") != wanted_type:
        return ""
    return data.get("id") or ""


def org_label(org_id, orgs_by_id):
    org = (orgs_by_id or {}).get(org_id) or {}
    attrs = org.get("attributes") or {}
    return attrs.get("slug") or attrs.get("name") or org_id


def project_label(project_id, projects_by_id):
    project = (projects_by_id or {}).get(project_id) or {}
    return (project.get("attributes") or {}).get("name") or project_id


def index_by_id(items):
    return {item["id"]: item for item in items if item.get("id")}


def issue_packages(issue):
    """Distinct (package_name, package_version) pairs, in first-seen order."""
    seen = []
    for coord in (issue.get("attributes") or {}).get("coordinates") or []:
        for rep in coord.get("representations") or []:
            dep = rep.get("dependency") or {}
            pair = (dep.get("package_name") or "", dep.get("package_version") or "")
            if pair[0] and pair not in seen:
                seen.append(pair)
    return seen


def issue_problem_ids(issue):
    """(snyk_ids, cves) from attributes.problems, matched by id prefix.

    Prefixes rather than `source`, because the docs and the spec disagree on
    the source labels and the id is what a reader searches for.
    """
    snyk, cves = [], []
    for problem in (issue.get("attributes") or {}).get("problems") or []:
        pid = problem.get("id") or ""
        if pid.upper().startswith("SNYK-") and pid not in snyk:
            snyk.append(pid)
        elif pid.upper().startswith("CVE-") and pid not in cves:
            cves.append(pid)
    return snyk, cves


def matches_package(name, wanted):
    """True when a Snyk package name is the one asked for.

    Case-insensitive. A wanted name with no ':' also matches a Maven
    groupId:artifactId on its artifactId, so `jackson-databind` finds
    `com.fasterxml.jackson.core:jackson-databind`.
    """
    if not name or not wanted:
        return False
    name, wanted = name.lower(), wanted.lower()
    if name == wanted:
        return True
    return ":" not in wanted and ":" in name and name.rsplit(":", 1)[1] == wanted


def issue_rows(issues, projects_by_id=None, orgs_by_id=None, package=None):
    """One flat row per (issue, package version). Filtered to `package` if given.

    A Snyk issue is per project, so the same vulnerability in ten projects is
    ten issues, and so ten or more rows.
    """
    rows = []
    for issue in issues:
        attrs = issue.get("attributes") or {}
        coords = attrs.get("coordinates") or []
        snyk_ids, cves = issue_problem_ids(issue)
        vuln_id = (snyk_ids or cves or [attrs.get("key") or issue.get("id") or ""])[0]
        org_id = _rel_id(issue, "organization")
        project_id = _rel_id(issue, "scan_item", "project")
        reach = next((c.get("reachability") for c in coords if c.get("reachability")), "")
        base = {
            "org": org_label(org_id, orgs_by_id),
            "org_id": org_id,
            "project": project_label(project_id, projects_by_id),
            "project_id": project_id,
            "vuln_id": vuln_id,
            "cves": ";".join(cves),
            "title": attrs.get("title") or "",
            "severity": attrs.get("effective_severity_level") or "",
            "status": attrs.get("status") or "",
            "ignored": bool(attrs.get("ignored")),
            "upgradeable": any(c.get("is_upgradeable") for c in coords),
            "fixable": any(c.get(k) for c in coords for k in
                           ("is_fixable_snyk", "is_fixable_manually", "is_fixable_upstream")),
            "reachability": reach or "",
            "issue_id": issue.get("id") or "",
            "created_at": attrs.get("created_at") or "",
            "updated_at": attrs.get("updated_at") or "",
        }
        packages = issue_packages(issue) or [("", "")]
        for name, version in packages:
            if package and not matches_package(name, package):
                continue
            rows.append({**base, "package": name, "version": version})
    return rows


def vuln_summary(rows):
    """Group issue rows by (package, vuln_id): how many projects each one hits.

    Sorted by projects affected, then severity, so the top of the list is
    where one upgrade removes the most findings.
    """
    groups = {}
    for row in rows:
        key = (row["package"], row["vuln_id"])
        g = groups.setdefault(key, {
            "package": row["package"], "vuln_id": row["vuln_id"], "cves": set(),
            "title": row["title"], "severity": row["severity"],
            "projects": set(), "orgs": set(), "versions": set(),
            "upgradeable_projects": set(), "issues": set(),
        })
        if row["cves"]:
            g["cves"].update(row["cves"].split(";"))
        if SEVERITY_RANK.get(row["severity"], -1) > SEVERITY_RANK.get(g["severity"], -1):
            g["severity"] = row["severity"]
        project = (row["org_id"], row["project_id"])
        g["projects"].add(project)
        g["orgs"].add(row["org_id"])
        if row["version"]:
            g["versions"].add(row["version"])
        if row["upgradeable"]:
            g["upgradeable_projects"].add(project)
        g["issues"].add(row["issue_id"])

    summary = [{
        "package": g["package"],
        "vuln_id": g["vuln_id"],
        "cves": ";".join(sorted(g["cves"])),
        "title": g["title"],
        "severity": g["severity"],
        "projects": len(g["projects"]),
        "orgs": len(g["orgs"]),
        "versions": ";".join(sorted(g["versions"])),
        "upgradeable_projects": len(g["upgradeable_projects"]),
        "issues": len(g["issues"]),
    } for g in groups.values()]
    summary.sort(key=lambda s: (-s["projects"], -SEVERITY_RANK.get(s["severity"], -1),
                                s["package"], s["vuln_id"]))
    return summary


def project_rows(projects, orgs_by_id=None):
    """One row per project, with the latest issue counts when they were requested."""
    rows = []
    for project in projects:
        attrs = project.get("attributes") or {}
        counts = (project.get("meta") or {}).get("latest_issue_counts") or {}
        org_id = _rel_id(project, "organization")
        rows.append({
            "org": org_label(org_id, orgs_by_id),
            "org_id": org_id,
            "project": attrs.get("name") or "",
            "project_id": project.get("id") or "",
            "type": attrs.get("type") or "",
            "origin": attrs.get("origin") or "",
            "target_reference": attrs.get("target_reference") or "",
            "target_file": attrs.get("target_file") or "",
            "status": attrs.get("status") or "",
            "critical": counts.get("critical", ""),
            "high": counts.get("high", ""),
            "medium": counts.get("medium", ""),
            "low": counts.get("low", ""),
            "counts_updated_at": counts.get("updated_at") or "",
        })
    return rows
