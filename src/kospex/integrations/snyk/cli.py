"""`kospex snyk`: read-only Snyk queries. Nothing is written to the kospex DB."""
import json
import os
import sys

import click
from rich.console import Console
from rich.table import Table
from rich.text import Text

import kospex_utils as KospexUtils
import krunner_utils as KrunnerUtils
from kospex.integrations.snyk import queries
from kospex.integrations.snyk.client import (
    API_URL_ENV_VAR, REST_VERSION, TOKEN_ENV_VAR, SnykClient, SnykError)

log = KospexUtils.get_kospex_logger("kospex")
console = Console(markup=False)

SEVERITIES = ("critical", "high", "medium", "low")
STATUS_CHOICES = {"open": ("open",), "resolved": ("resolved",), "all": ("open", "resolved")}
PROJECT_ID_BATCH = 50


# -- shared helpers ------------------------------------------------------------

def _client():
    try:
        return SnykClient()
    except SnykError as e:
        raise click.ClickException(str(e))


def _output_options(f):
    """-out FILENAME [-overwrite] | -stdout, and -raw (same contract as krunner)."""
    f = click.option("-raw", is_flag=True, default=False,
                     help="Write CSV values exactly as returned. By default a value "
                          "starting with = + - @ tab or CR gets a leading ' so a "
                          "spreadsheet cannot run it as a formula.")(f)
    f = click.option("-stdout", "to_stdout", is_flag=True, default=False,
                     help="Write only the CSV, to stdout, instead of the table. "
                          "Progress goes to stderr.")(f)
    f = click.option("-overwrite", is_flag=True, default=False,
                     help="Allow -out to replace an existing file.")(f)
    f = click.option("-out", "out_file", default=None, type=click.Path(dir_okay=False),
                     help="Also write the CSV to this file.")(f)
    return f


def _check_output_options(out_file, overwrite, to_stdout, raw, json_file=None):
    if out_file and to_stdout:
        raise click.UsageError("Give either -out FILENAME or -stdout, not both.")
    if overwrite and not (out_file or json_file):
        raise click.UsageError("-overwrite only applies to -out FILENAME or -json FILENAME.")
    if raw and not (out_file or to_stdout):
        raise click.UsageError("-raw only applies to the CSV: add -out FILENAME or -stdout.")
    if json_file and out_file and os.path.abspath(json_file) == os.path.abspath(out_file):
        raise click.UsageError("-json and -out must be different files.")
    for path in (out_file, json_file):
        if path and os.path.exists(path) and not overwrite:
            raise click.UsageError(f"{path} already exists. Add -overwrite to replace it.")


def _open_private(path, overwrite):
    """Open for writing, owner-only (CWE-732): the raw API data names the client's
    orgs, projects and file paths. fchmod covers a replaced file, whose old mode
    os.open would keep. O_NOFOLLOW refuses a symlink at the path (CWE-59), and
    O_EXCL without -overwrite closes the gap after the exists check (CWE-367)."""
    flags = os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    flags |= os.O_TRUNC if overwrite else os.O_EXCL
    try:
        fd = os.open(path, flags, 0o600)
    except OSError as e:
        raise click.UsageError(f"Cannot write -json {path}: {e.strerror}")
    os.fchmod(fd, 0o600)
    return os.fdopen(fd, "w", encoding="utf-8")


def _emit_csv(rows, fieldnames, out_file, to_stdout, raw, progress):
    """CSV, spreadsheet-safe unless raw (CWE-1236): package names, project names
    and titles come from repositories and advisories that others control."""
    if to_stdout:
        KrunnerUtils.write_dict_to_csv(sys.stdout, rows, fieldnames,
                                       spreadsheet_safe_cells=not raw)
    elif out_file:
        KrunnerUtils.write_dict_to_csv(out_file, rows, fieldnames,
                                       spreadsheet_safe_cells=not raw)
        progress.print(f"Wrote {len(rows)} rows to {out_file}")


def _cell(value):
    """A table cell as Text: values come from Snyk and repos (CWE-150, no markup)."""
    if value is None or value == "":
        return Text("-")
    if isinstance(value, bool):
        return Text("yes" if value else "no")
    return Text(str(value))


def _print_rows(rows, columns, title):
    table = Table(title=Text(title))
    for name in columns:
        table.add_column(name, no_wrap=name in ("package", "vuln_id", "project"))
    for row in rows:
        table.add_row(*[_cell(row.get(c)) for c in columns])
    KospexUtils.print_table(table, console)


def _resolve_orgs(client, wanted):
    """The orgs to query: all the token can see, or those named by id, slug or name."""
    orgs = client.orgs()
    if not wanted:
        return orgs
    picked = []
    for w in wanted:
        match = [o for o in orgs if w in (o.get("id"),
                                           (o.get("attributes") or {}).get("slug"),
                                           (o.get("attributes") or {}).get("name"))]
        if not match:
            raise click.UsageError(f"No Snyk org {w!r} visible to this token. "
                                   "`kospex snyk orgs` lists them.")
        picked.extend(m for m in match if m not in picked)
    return picked


def _projects_by_id(client, org_id, project_ids):
    """Only the projects the rows refer to, in batches (not every project in the org)."""
    ids = sorted(p for p in project_ids if p)
    found = {}
    for i in range(0, len(ids), PROJECT_ID_BATCH):
        found.update(queries.index_by_id(client.projects(org_id, ids=ids[i:i + PROJECT_ID_BATCH])))
    return found


def _collect_issue_rows(client, orgs, package, severities, project_id, status,
                        exclude_ignored, max_issues, progress, raw_out=None):
    """raw_out, if given, receives every issue read (before the -package filter)
    as one JSON object per line, written as it arrives."""
    orgs_by_id = queries.index_by_id(orgs)
    rows = []
    for org in orgs:
        org_id = org["id"]
        issues = []
        for item in client.issues(org_id, statuses=STATUS_CHOICES[status],
                                  severities=severities or None, project_id=project_id,
                                  ignored=False if exclude_ignored else None,
                                  max_items=max_issues):
            issues.append(item)
            if raw_out is not None:
                raw_out.write(json.dumps(item) + "\n")
        org_rows = queries.issue_rows(issues, orgs_by_id=orgs_by_id, package=package)
        projects = _projects_by_id(client, org_id, {r["project_id"] for r in org_rows})
        for r in org_rows:
            r["project"] = queries.project_label(r["project_id"], projects)
        rows.extend(org_rows)
        capped = " (stopped at -max-issues)" if max_issues and len(issues) >= max_issues else ""
        progress.print(f"{queries.org_label(org_id, orgs_by_id)}: {len(issues)} issues "
                       f"read{capped}, {len(org_rows)} rows")
    return rows


def _issue_filter_options(f):
    f = click.option("-max-issues", type=click.IntRange(min=1), default=None,
                     help="Stop reading issues after N per org (each page is 100).")(f)
    f = click.option("-exclude-ignored", is_flag=True, default=False,
                     help="Leave out issues ignored in Snyk.")(f)
    f = click.option("-status", type=click.Choice(list(STATUS_CHOICES)), default="open",
                     show_default=True, help="Issue status.")(f)
    f = click.option("-project", "project_id", default=None,
                     help="Only this Snyk project id.")(f)
    f = click.option("-severity", "severities", multiple=True,
                     type=click.Choice(SEVERITIES),
                     help="Only these severities (repeatable).")(f)
    f = click.option("-org", "org_names", multiple=True,
                     help="Snyk org id, slug or name (repeatable). Default: every org "
                          "the token can see.")(f)
    return f


def _finish(client, progress):
    progress.print(f"{client.requests} Snyk API request(s).")
    log.info("kospex snyk: %d API requests", client.requests)


# -- commands --------------------------------------------------------------------

@click.group("snyk")
def snyk():
    """Read-only Snyk queries (SCA). Nothing is saved to the kospex DB.

    \b
    Needs SNYK_TOKEN, in the environment or ~/kospex/kospex.env. SNYK_API
    selects a region, e.g. https://api.eu.snyk.io (default https://api.snyk.io).
    Most of the Snyk API needs an Enterprise plan: run `kospex snyk probe` first.

    \b
        kospex snyk probe
        kospex snyk issues -package jackson-databind
        kospex snyk vulns -severity critical -severity high -out vulns.csv
    """


@snyk.command("probe")
@click.option("-org", "org_name", default=None,
              help="Org id, slug or name for the org-scoped calls. Default: the first org.")
def probe(org_name):
    """Which Snyk endpoints this token and plan can use: one small request each."""
    client = _client()
    org_id = None
    if org_name:
        try:
            org_id = _resolve_orgs(client, [org_name])[0]["id"]
        except SnykError as e:
            raise click.ClickException(str(e))
    results = client.probe(org_id)
    table = Table(title=Text(f"Snyk API probe ({client.api_url}, version {REST_VERSION})"))
    for name in ("call", "path", "status", "detail"):
        table.add_column(name)
    for r in results:
        table.add_row(_cell(r.name), _cell(r.path), _cell(r.status), _cell(r.detail))
    KospexUtils.print_table(table, console)
    if any(r.name == "issues" and r.status == 403 for r in results):
        console.print("issues returned 403: `issues` and `vulns` will not work with this "
                      "token. Most of the Snyk API needs an Enterprise plan.")
    _finish(client, console)


@snyk.command("orgs")
def orgs():
    """The Snyk orgs this token can see."""
    client = _client()
    try:
        items = client.orgs()
    except SnykError as e:
        raise click.ClickException(str(e))
    rows = [{"org_id": o.get("id"), **{k: (o.get("attributes") or {}).get(k)
                                       for k in ("slug", "name", "is_personal", "group_id")}}
            for o in items]
    _print_rows(rows, ["org_id", "slug", "name", "is_personal", "group_id"], "Snyk orgs")
    _finish(client, console)


@snyk.command("projects")
@click.option("-org", "org_names", multiple=True,
              help="Snyk org id, slug or name (repeatable). Default: every org.")
@_output_options
def projects(org_names, out_file, overwrite, to_stdout, raw):
    """Snyk projects, with issue counts from each project's latest scan."""
    _check_output_options(out_file, overwrite, to_stdout, raw)
    progress = Console(markup=False, stderr=True) if to_stdout else console
    client = _client()
    try:
        orgs_list = _resolve_orgs(client, org_names)
        orgs_by_id = queries.index_by_id(orgs_list)
        rows = []
        for org in orgs_list:
            rows.extend(queries.project_rows(client.projects(org["id"], issue_counts=True),
                                             orgs_by_id))
    except SnykError as e:
        raise click.ClickException(str(e))
    if not to_stdout:
        _print_rows(rows, ["org", "project", "type", "origin", "critical", "high",
                           "medium", "low"], "Snyk projects (latest scan issue counts)")
    _emit_csv(rows, queries.PROJECT_FIELDS, out_file, to_stdout, raw, progress)
    progress.print(f"{len(rows)} projects in {len(orgs_list)} org(s).")
    _finish(client, progress)


@snyk.command("issues")
@click.option("-package", default=None,
              help="Only this package. Case-insensitive; a name without ':' also "
                   "matches a Maven artifactId (jackson-databind).")
@click.option("-json", "json_file", default=None, type=click.Path(dir_okay=False),
              help="Also save every issue read, exactly as the API returned it, one JSON "
                   "object per line (before -package filtering). For diagnosing missing "
                   "columns. Contains client data: created owner-only, keep it on site.")
@_issue_filter_options
@_output_options
def issues(package, json_file, org_names, severities, project_id, status, exclude_ignored,
           max_issues, out_file, overwrite, to_stdout, raw):
    """Open SCA issues: one row per project, package version and vulnerability.

    Snyk has no server-side package filter, so -package reads every issue in
    each org (100 per request) and filters here.
    """
    _check_output_options(out_file, overwrite, to_stdout, raw, json_file)
    progress = Console(markup=False, stderr=True) if to_stdout else console
    client = _client()
    raw_out = _open_private(json_file, overwrite) if json_file else None
    try:
        orgs_list = _resolve_orgs(client, org_names)
        rows = _collect_issue_rows(client, orgs_list, package, severities, project_id,
                                   status, exclude_ignored, max_issues, progress, raw_out)
    except SnykError as e:
        raise click.ClickException(str(e))
    finally:
        if raw_out is not None:
            raw_out.close()
            # Also on error: what was read before it is what diagnoses it.
            progress.print(f"Raw issues saved to {json_file}")
    rows.sort(key=lambda r: (-queries.SEVERITY_RANK.get(r["severity"], -1),
                             r["package"], r["vuln_id"], r["org"], r["project"]))
    if not to_stdout:
        _print_rows(rows, ["severity", "package", "version", "vuln_id", "project",
                           "upgradeable", "ignored"],
                    f"Snyk issues{' for ' + package if package else ''}")
    _emit_csv(rows, queries.ISSUE_FIELDS, out_file, to_stdout, raw, progress)
    projects_hit = len({(r["org_id"], r["project_id"]) for r in rows})
    progress.print(f"{len(rows)} rows across {projects_hit} project(s).")
    _finish(client, progress)


@snyk.command("vulns")
@click.option("-package", default=None,
              help="Only this package (same matching as `issues -package`).")
@_issue_filter_options
@_output_options
def vulns(package, org_names, severities, project_id, status, exclude_ignored,
          max_issues, out_file, overwrite, to_stdout, raw):
    """Each vulnerability and how many projects it affects, most projects first."""
    _check_output_options(out_file, overwrite, to_stdout, raw)
    progress = Console(markup=False, stderr=True) if to_stdout else console
    client = _client()
    try:
        orgs_list = _resolve_orgs(client, org_names)
        rows = _collect_issue_rows(client, orgs_list, package, severities, project_id,
                                   status, exclude_ignored, max_issues, progress)
    except SnykError as e:
        raise click.ClickException(str(e))
    summary = queries.vuln_summary(rows)
    if not to_stdout:
        _print_rows(summary, ["package", "vuln_id", "severity", "projects",
                              "upgradeable_projects", "versions"],
                    "Snyk vulnerabilities by projects affected")
    _emit_csv(summary, queries.VULN_FIELDS, out_file, to_stdout, raw, progress)
    progress.print(f"{len(summary)} vulnerabilities from {len(rows)} issue rows.")
    _finish(client, progress)


