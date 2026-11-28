#!/usr/bin/env python3
"""FastAPI Kospex web server."""

import csv
import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from io import StringIO
from pathlib import Path
from typing import Optional
from urllib.parse import quote, urlencode

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import panopticas as Panopticas
from requests.models import LocationParseError
from starlette.exceptions import HTTPException as StarletteHTTPException

import kospex_schema as KospexSchema
import kospex_stats as KospexStats
import kospex_utils as KospexUtils
import kospex_web as KospexWeb
from kospex import developers_view
from api_routes import router as api_router
from kospex.db.migrator import warn_if_behind
from kospex.osi_queue import get_outcome as get_osi_outcome
import kospex.orphans as KospexOrphans
from kospex_core import Kospex
from kospex_dependencies import KospexDependencies
from kospex_git import KospexGit
from kospex_query import (
    KospexQuery, retired_tag_replacement, retired_tags, url_cache_seconds)
from kospex_request_cache import RequestCache
from kospex_utils import KospexTimer
from kweb_graph_service import GraphService
from kweb_help_service import HelpService

# Initialize Kospex environment
KospexUtils.init(create_directories=True, setup_logging=True, verbose=False)

# Set up logging using centralized system
logger = KospexUtils.get_kospex_logger("kweb2")

# Used to cache DB results in memory
request_cache = RequestCache()
# structure
# key: { data, cache_time}


def cached_query(name, compute, **scope):
    """Return compute() from request_cache, keyed by name and scope.

    Recomputed after a newer repo sync, or once the entry exceeds max_age.
    cache_time is taken before compute() runs, so a sync that lands mid-query
    still invalidates the result.
    """
    key = RequestCache.key(name, **scope)
    data = request_cache.get(key, KospexQuery().get_last_sync_datetime())
    if data is None:
        started = datetime.now().astimezone()
        data = compute()
        request_cache.set(key, data, cache_time=started)
    return data


SUMMARY_STATS_SCOPES = {"org_key", "repo_id", "server"}


def summary_rows(params):
    """(developers, repos) rows for the summary page's status counts.

    Read from developer_stats rather than scanning commits twice. params is a
    repo_id, org_key or server scope (or empty, for the whole estate).
    """
    devs = cached_query(
        "developer_statuses", lambda: KospexQuery().developer_statuses(**params), **params
    )
    repos = cached_query(
        "repo_statuses", lambda: KospexQuery().repo_statuses(**params), **params
    )
    return devs, repos

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Build (and migrate) the kospex schema once, before serving any request.

    kweb reaches the DB through KospexQuery, which opens the path directly and
    so CREATES an empty file when it is missing — on a clean install that meant
    every request failed on 'no such table: kospex_config'. Doing it here also
    lets kweb participate in the fresh-DB migration bootstrap.

    Once per server boot, not per request: the KospexQuery call sites would
    otherwise re-run ~19 CREATE TABLE IF NOT EXISTS statements each time.
    """
    try:
        db = KospexSchema.connect_or_create_kospex_db()
        pending = warn_if_behind(db)
        if pending:
            logger.warning(
                "kospex DB is %s migration(s) behind - run `kospex upgrade-db -apply`",
                pending,
            )
    except Exception as exc:
        # Never prevent the server booting; the failure is visible in the log
        # and every request will report it anyway.
        logger.error("Could not initialise the kospex database at startup: %s", exc)
    yield


# Initialize FastAPI app
app = FastAPI(
    title="Kospex Web",
    description="Kospex Code and Developer analytics platform",
    version=Kospex.VERSION,
    lifespan=lifespan,
)

DEFAULT_ALLOWED_HOSTS = ["127.0.0.1", "localhost"]


def allowed_hosts():
    """Host header values kweb will answer to.

    Rejecting any other Host stops DNS rebinding (CWE-346): a rebound page
    reaches 127.0.0.1 as same-origin, so CORS never applies, but its requests
    still carry the attacker's hostname. KOSPEX_WEB_ALLOWED_HOSTS (comma
    separated, also read from kospex.env) adds names or IPs, e.g. to reach kweb
    in a container from another machine. Ports are not part of the match.

    A pattern Starlette would refuse is dropped with a warning rather than
    stopping the server booting.
    """
    hosts = list(DEFAULT_ALLOWED_HOSTS)
    raw = os.environ.get("KOSPEX_WEB_ALLOWED_HOSTS", "")
    for host in (h.strip().lower() for h in raw.split(",")):
        if not host or host in hosts:
            continue
        if "*" in host[1:] or (host.startswith("*") and host != "*" and not host.startswith("*.")):
            logger.warning("KOSPEX_WEB_ALLOWED_HOSTS: ignoring %r, wildcards must be like '*.example.com'", host)
            continue
        if host == "*":
            logger.warning("KOSPEX_WEB_ALLOWED_HOSTS contains '*': Host header checking is disabled")
        hosts.append(host)
    return hosts


# No CORS middleware: kweb's pages only call kweb, which is same-origin, and
# without Access-Control-Allow-Origin browsers keep other sites from reading
# its responses. Add it back only with explicit origins, never "*".
app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts())

# Mount static files
static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# Initialize Jinja2 templates
templates_dir = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(templates_dir))

# Initialize services
help_service = HelpService()
graph_service = GraphService()

# Include API routes
app.include_router(api_router)


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(Path(__file__).parent / "static/favicon.ico")


def download_csv_fastapi(dict_data, filename=None, fieldnames=None):
    """FastAPI-compatible CSV download function

    With fieldnames, no rows gives a file of headings; without, it is a 400.
    """
    if not dict_data and not fieldnames:
        raise HTTPException(status_code=400, detail="No data to download")

    # Create CSV content
    with StringIO() as output:
        # Use the keys of the first dictionary for the header
        if dict_data:
            fieldnames = dict_data[0].keys()

        # Create a CSV writer object
        writer = csv.DictWriter(output, fieldnames=fieldnames)

        # Write the header
        writer.writeheader()

        # Write the rows
        for row_dict in dict_data:
            writer.writerow(row_dict)

        # Get the CSV string
        csv_string = output.getvalue()

    # Set the output file name
    if not filename:
        filename = "download.csv"

    # Create FastAPI Response with CSV content
    return Response(
        content=csv_string,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# Scope keys from KospexWeb.get_id_params, with the word the user sees.
_SCOPE_KINDS = {
    "repo_id": "repository",
    "org_key": "organisation",
    "server": "server",
    "author_email": "developer",
}


def _require_known_scope(params, id):
    """
    Raise a 404 if id names a repository, organisation or server that has no
    row in the repos table. Without this a page rendered empty with HTTP 200,
    which looks the same as a real scope with no data; an unknown id is most
    likely a typo or a stale link. (#206)

    params: KospexWeb.get_id_params(id), or the route's own {key: id}.
    No id (the all-repositories page) and developer ids are not checked.
    """
    if not id or KospexQuery().scope_exists(params):
        return
    kind = next(name for key, name in _SCOPE_KINDS.items() if params.get(key))
    raise HTTPException(
        status_code=404,
        detail=f"No {kind} with ID '{id}' in the kospex database. Check it for a typo.",
    )


# Custom 404 handler
@app.exception_handler(404)
async def custom_404_handler(request: Request, exc: HTTPException):
    """Custom 404 error handler that serves the 404.html template.

    Passes the route's own `detail` through to the template's error callout,
    which nothing filled before: /tech/Fortran, /tag/NotATag and a plain
    mistyped path all rendered the identical "Oops! Page not found.", so a
    reader could not tell a name this estate does not have from a bad URL.

    A detail that only restates the heading is dropped: starlette's default
    for an unmatched route is the bare string "Not Found", and the catch-all
    route's is "Page not found", both of which the page already says in 3xl
    bold above. Repeating it in an orange warning box reads as a second,
    different problem. Every other route's detail is information the heading
    does not give -- "Repository not in the kospex database: ..." -- and has
    been discarded until now.
    """
    generic = ("not found", "page not found")
    detail = getattr(exc, "detail", None)
    error = detail if detail and detail.strip().lower() not in generic else None

    try:
        return templates.TemplateResponse(
            request, "404.html", {"error": error}, status_code=404)
    except Exception as e:
        # Fallback to basic 404 if template fails
        logger.error(f"Error rendering 404 template: {e}")
        return HTMLResponse(content="<h1>404 - Page Not Found</h1>", status_code=404)


# Custom exception handler for general HTTP exceptions
@app.exception_handler(StarletteHTTPException)
async def custom_http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Handle HTTP exceptions with custom error pages"""
    if exc.status_code == 404:
        return await custom_404_handler(request, exc)

    # For other status codes, use default handler
    return await http_exception_handler(request, exc)


@app.get("/clear-cache")
def clear_cache(request: Request):
    """
    Clear the cache the in-memory cache
    """
    request_cache.clear()
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
@app.get("/summary/", response_class=HTMLResponse)
@app.get("/summary/{id}", response_class=HTMLResponse)
def summary(request: Request, id: Optional[str] = None):
    """Serve up the summary home page"""
    try:
        logger.info(f"Summary page requested with id: {id}")

        params = KospexWeb.get_id_params(id)
        if not set(params) <= SUMMARY_STATS_SCOPES:
            # get_id_params also decodes a base64 author_email id. A summary is
            # per repo, org or server; a developer's page is /developers/.
            raise HTTPException(
                status_code=404,
                detail="Summary pages are per repo, org or server. See /developers/ for a developer.",
            )
        _require_known_scope(params, id)

        with KospexTimer("Load developers") as dev_load:
            devs, repos = summary_rows(params)

        logger.info(dev_load)

        dev_stats = KospexUtils.count_key_occurrences(devs, "status")
        repo_stats = KospexUtils.count_key_occurrences(repos, "status")

        # Each developer circle opens that status's list, in the same scope
        dev_circles = KospexWeb.status_circles(dev_stats)
        for circle in dev_circles:
            circle["url"] = developers_view.developers_url(
                params, status=circle["key"].lower())

        return templates.TemplateResponse(
            request,
            "summary.html",
            {
                "dev_total": sum(dev_stats.values()),
                "dev_url": developers_view.developers_url(params),
                "dev_circles": dev_circles,
                "repo_total": sum(repo_stats.values()),
                "repo_circles": KospexWeb.status_circles(repo_stats),
                "id": params,
                # Breadcrumb, for a server, org or repo scope only
                **(KospexWeb.scope_context(params, "Summary") if params else {}),
            },
        )
    except HTTPException:
        # Re-raise the route's own 4xx. The handler below converts any
        # Exception to a 500, and HTTPException is an Exception, so a
        # deliberate 404/400 was reported as a server error. (#210)
        raise
    except Exception as e:
        logger.error(f"Error in summary endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/summary2/", response_class=HTMLResponse)
@app.get("/summary2/{id}", response_class=HTMLResponse)
def summary2(request: Request, id: Optional[str] = None):
    """Serve up the summary home page with horizontal stacked bar visualization"""
    try:
        logger.info(f"Summary2 page requested with id: {id}")

        params = KospexWeb.get_id_params(id)
        _require_known_scope(params, id)

        with KospexTimer("Load developers") as dev_load:
            devs = cached_query(
                "developers", lambda: KospexQuery().developers(**params), **params
            )

        logger.info(dev_load)

        dev_stats = KospexUtils.count_key_occurrences(devs, "status")
        dev_percentages = KospexUtils.convert_to_percentage(dev_stats)

        total = sum(dev_stats.values())
        dev_stats["total"] = total

        result = {}
        for name, percentage in dev_percentages.items():
            if percentage:
                dev_stats[f"{name}_percentage"] = round(percentage)
            result[name] = round(100 * (percentage / 100)) + 40

        repos = cached_query("repos", lambda: KospexQuery().repos(**params), **params)
        repo_stats = KospexUtils.count_key_occurrences(repos, "status")

        repo_sizes = {}
        repo_percentages = KospexUtils.convert_to_percentage(repo_stats)
        # Do the total after percentages are calculated
        total = sum(repo_stats.values())
        repo_stats["total"] = total

        for name, percentage in repo_percentages.items():
            if percentage:
                repo_stats[f"{name}_percentage"] = round(percentage)
            repo_sizes[name] = round(100 * (percentage / 100)) + 40

        return templates.TemplateResponse(
            request,
            "summary2.html",
            {
                "developers": dev_stats,
                "data_size": result,
                "repos": repo_stats,
                "repo_sizes": repo_sizes,
                "id": params,
            },
        )
    except HTTPException:
        # Re-raise the route's own 4xx. The handler below converts any
        # Exception to a 500, and HTTPException is an Exception, so a
        # deliberate 404/400 was reported as a server error. (#210)
        raise
    except Exception as e:
        logger.error(f"Error in summary2 endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/help/", response_class=HTMLResponse)
@app.get("/help/{id}", response_class=HTMLResponse)
def help_page(request: Request, id: Optional[str] = None):
    """Serve up the help pages"""
    try:
        logger.info(f"Help page requested with id: {id}")

        if id is None:
            return templates.TemplateResponse(request, "help/index.html")
        else:
            # Try to serve the specific help page
            try:
                return templates.TemplateResponse(request, f"help/{id}.html")
            except Exception:
                # If specific page doesn't exist, fall back to index
                logger.warning(f"Help page help/{id}.html not found, falling back to index")
                return templates.TemplateResponse(request, "help/index.html")
    except HTTPException:
        # Re-raise the route's own 4xx. The handler below converts any
        # Exception to a 500, and HTTPException is an Exception, so a
        # deliberate 404/400 was reported as a server error. (#210)
        raise
    except Exception as e:
        logger.error(f"Error in help endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/developers/active/{repo_id}", response_class=HTMLResponse)
def active_developers(request: Request, repo_id: str):
    """A repo's authors with a commit in the last 90 days."""
    try:
        logger.info(f"Active developers page requested for repo: {repo_id}")
        _require_known_scope({"repo_id": repo_id}, repo_id)

        context = _developers_context(
            KospexQuery(), {"repo_id": repo_id}, "authors", "90",
            _list_params(request.query_params), None)
        return templates.TemplateResponse(request, "developers.html", context)
    except HTTPException:
        # Re-raise the route's own 4xx. The handler below converts any
        # Exception to a 500, and HTTPException is an Exception, so a
        # deliberate 404/400 was reported as a server error. (#210)
        raise
    except Exception as e:
        logger.error(f"Error in active_developers endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


# Health check endpoint
@app.get("/health")
def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "service": "kospex-web"}


@app.get("/generate-repo-id/")
def generate_repo_id(url: str):
    """Generate a repo_id from a git URL"""
    try:
        logger.info(f"Generate repo_id requested for URL: {url}")

        parts = KospexGit.parse_git_remote(url)
        if not parts:
            return JSONResponse(
                status_code=400,
                content={"url": url, "error": "not a recognisable git remote URL"},
            )

        repo_id = KospexGit.generate_repo_id(
            parts["remote"], parts["org"], parts["repo"])

        return JSONResponse(content={
            "url": url,
            "repo_id": repo_id,
            "git_server": parts["remote"],
            "org": parts["org"],
            "repo": parts["repo"],
        })

    except HTTPException:
        # Re-raise the route's own 4xx. The handler below converts any
        # Exception to a 500, and HTTPException is an Exception, so a
        # deliberate 404/400 was reported as a server error. (#210)
        raise
    except Exception as e:
        logger.error(f"Error in generate_repo_id endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/servers/", response_class=HTMLResponse)
def servers(request: Request):
    """Display Git server information."""
    try:
        logger.info("Servers page requested")

        kquery = KospexQuery()
        data = kquery.server_summary()

        return templates.TemplateResponse(request, "servers.html", {"data": data})
    except HTTPException:
        # Re-raise the route's own 4xx. The handler below converts any
        # Exception to a 500, and HTTPException is an Exception, so a
        # deliberate 404/400 was reported as a server error. (#210)
        raise
    except Exception as e:
        logger.error(f"Error in servers endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/metadata/", response_class=HTMLResponse)
def metadata(request: Request):
    """Metadata about the kospex DB and repos."""
    try:
        logger.info("Metadata page requested")

        with KospexTimer("Summary load") as summary_load:
            data = cached_query("metadata", lambda: KospexQuery().summary())

        logger.info(summary_load)

        # data = KospexQuery().summary()

        return templates.TemplateResponse(request, "metadata.html", {**data})
    except HTTPException:
        # Re-raise the route's own 4xx. The handler below converts any
        # Exception to a 500, and HTTPException is an Exception, so a
        # deliberate 404/400 was reported as a server error. (#210)
        raise
    except Exception as e:
        logger.error(f"Error in metadata endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/metadata/repos/", response_class=HTMLResponse)
@app.get("/metadata/repos/{id}", response_class=HTMLResponse)
def metadata_repos(request: Request, id: Optional[str] = None):
    """
    Display repository metadata information based on git commits and sync.
    """
    try:
        logger.info(f"Metadata repos page requested with id: {id}")
        _require_known_scope(KospexWeb.get_id_params(id), id)

        kquery = KospexQuery()
        repos = kquery.get_repos()

        # import pprint as pp
        # pp.pprint(repos)

        # If id is provided, filter to specific repo
        if id:
            repos = [repo for repo in repos if repo.get("_repo_id") == id]

        return templates.TemplateResponse(
            request, "metadata_repos.html", {"repos": repos, "repo_id": id}
        )
    except HTTPException:
        # Re-raise the route's own 4xx. The handler below converts any
        # Exception to a 500, and HTTPException is an Exception, so a
        # deliberate 404/400 was reported as a server error. (#210)
        raise
    except Exception as e:
        logger.error(f"Error in metadata repos endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/orphans/", response_class=HTMLResponse)
@app.get("/orphans/{id}", response_class=HTMLResponse)
def orphans(request: Request, id: Optional[str] = None, active_in: str = "estate"):
    """Orphaned repos for a scope; the same rows as `kospex orphans`.

    active_in (estate, server or org) is where a person counts as still here.
    """
    try:
        logger.info(f"Orphans page requested with id: {id}")

        if active_in not in KospexOrphans.ACTIVE_IN:
            raise HTTPException(
                status_code=400,
                detail=f"active_in must be one of {', '.join(KospexOrphans.ACTIVE_IN)}.")
        params = KospexWeb.get_id_params(id)
        _require_known_scope(params, id)
        data = KospexQuery().get_orphans(id=params, active_in=active_in)

        return templates.TemplateResponse(request, "orphans.html", {
            "data": data,
            "summary": KospexOrphans.summary(data),
            "stale_count": sum(1 for row in data if row["refresh_stale"] is True),
            "stale_days": 30,
            "sync_command": KospexOrphans.sync_command(
                30, repo_id=params.get("repo_id"), org_key=params.get("org_key"),
                server=params.get("server")),
            "active_in": active_in,
            "scope_id": id or "",
        })
    except HTTPException:
        # Re-raise the route's own 4xx. The handler below converts any
        # Exception to a 500, and HTTPException is an Exception, so a
        # deliberate 404/400 was reported as a server error. (#210)
        raise
    except Exception as e:
        logger.error(f"Error in orphans endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/bubble/{id}", response_class=HTMLResponse)
@app.get("/treemap/{id}", response_class=HTMLResponse)
def bubble_treemap(request: Request, id: str):
    """
    Display a bubble or treemap chart of developers in a repo
    or the repos for an org_key
    or the repos for a given user

    Show the developers for a repo_id
    /bubble/<repo_id> or /treemap/<repo_id>

    Show the developers for an org_key
    /bubble/<org_key> or /treemap/<org_key>

    Show the developers for a git_server
    /bubble/<git_server> or /treemap/<git_server>

    Show repos for a developer with a base64 encoded email
    /bubble/EMAIL_B64 or /treemap/EMAIL_B64

    Show repo view of an org_key
    /bubble/repo/<org_key> or /treemap/repo/<org_key>
    """
    try:
        # Determine template type from the path
        template = "bubble" if "/bubble/" in str(request.url) else "treemap"
        logger.info(f"{template.title()} chart requested for id: {id}")
        _require_known_scope(KospexWeb.get_id_params(id), id)

        link_url = ""

        if KospexUtils.parse_repo_id(id):
            link_url = f"repo/{id}"
        elif KospexUtils.is_base64(id):
            link_url = f"dev/{id}"
        else:
            link_url = f"{id}"

        html_template = f"{template}.html"

        return templates.TemplateResponse(
            request, html_template,
            {"link_url": link_url, "template": template, "id": id},
        )
    except HTTPException:
        # Re-raise the route's own 4xx. The handler below converts any
        # Exception to a 500, and HTTPException is an Exception, so a
        # deliberate 404/400 was reported as a server error. (#210)
        raise
    except Exception as e:
        logger.error(f"Error in bubble/treemap endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/osi/", response_class=HTMLResponse)
@app.get("/osi/{id}", response_class=HTMLResponse)
def osi(request: Request, id: Optional[str] = None):
    """Functions around an Open Source Inventory"""
    try:
        logger.info(f"OSI page requested with id: {id}")

        params = KospexWeb.get_id_params(id)
        _require_known_scope(params, id)
        try:
            deps = KospexQuery().get_dependency_files(request_id=params)
        except ValueError as exc:
            # A scope this query cannot honour -- e.g. an author email.
            # Refusing beats widening to every row. (#158)
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        for file in deps:
            file["days_ago"] = KospexUtils.days_ago(file.get("committer_when"))
            file["status"] = KospexUtils.development_status(file.get("days_ago"))

        extracted_keys = KospexQuery().extracted_dependency_file_keys(request_id=params)
        deps, commentary = KospexWeb.osi_extraction_view(deps, extracted_keys)
        # Shown above the collapsed coverage details, so a gap is visible
        # without expanding them. Same warning as /dependencies/.
        unsupported = KospexWeb.unsupported_dependency_files(deps)

        file_number = len(deps)
        status = KospexUtils.repo_stats(deps, "committer_when")
        filenames = KospexUtils.filenames_by_repo_id(deps)

        return templates.TemplateResponse(
            request, "osi.html",
            {
                "data": deps,
                "file_number": file_number,
                "dep_files": filenames,
                "status": status,
                "commentary": commentary,
                "unsupported": unsupported,
                **KospexWeb.osi_fix_commands(id, params.get("repo_id")),
                **KospexWeb.scope_context(params, "Open Source Inventory"),
                "status_days": {"active": KospexUtils.DEV_STATUS_ACTIVE_DAYS,
                                "aging": KospexUtils.DEV_STATUS_AGING_DAYS,
                                "stale": KospexUtils.DEV_STATUS_STALE_DAYS},
            },
        )
    except HTTPException:
        # A deliberate 4xx must not be swallowed by the handler below and
        # re-raised as a 500 -- the request was bad, not the server.
        raise
    except Exception as e:
        logger.error(f"Error in osi endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/collab/{repo_id}", response_class=HTMLResponse)
def collab(request: Request, repo_id: str):
    """Display repository collaboration information"""
    try:
        logger.info(f"Collaboration page requested for repo: {repo_id}")

        # An unvalidated id was passed straight to the query, which returned no
        # rows and rendered an empty table -- indistinguishable from "this repo
        # has no collaborators". Both failures now say what is wrong. (#43)
        if not KospexUtils.parse_repo_id(repo_id):
            raise HTTPException(
                status_code=404,
                detail=f"Not a repo_id: {repo_id} (expected server~owner~repo)")

        kquery = KospexQuery()

        if not kquery.get_repo_by_id(repo_id):
            raise HTTPException(
                status_code=404,
                detail=f"Repository not in the kospex database: {repo_id}")

        collabs = kquery.get_collabs(repo_id=repo_id)
        _label_bots(collabs, ("author", "committer"))

        return templates.TemplateResponse(
            request, "collab.html",
            {"repo_id": repo_id, "collabs": collabs,
             # Breadcrumb: server / org / repo / Collaboration.
             **KospexWeb.scope_context({"repo_id": repo_id}, "Collaboration")},
        )
    except HTTPException:
        # A deliberate 4xx must not be swallowed by the handler below and
        # re-raised as a 500 -- the request was bad, not the server.
        raise
    except Exception as e:
        logger.error(f"Error in collab endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/collab/graph/{repo_id}", response_class=HTMLResponse)
def collab_graph(request: Request, repo_id: str):
    """Display network graph visualization of repository collaboration"""
    try:
        logger.info(f"Collaboration graph page requested for repo: {repo_id}")
        _require_known_scope({"repo_id": repo_id}, repo_id)

        # Breadcrumb: server / org / repo / Collaboration / Network graph.
        crumbs = {
            **KospexWeb.scope_context({"repo_id": repo_id}, "Collaboration"),
            "sub_page": "Network graph",
            "page_url": f"/collab/{repo_id}",
        }

        return templates.TemplateResponse(
            request, "collab_graph.html", {"repo_id": repo_id, **crumbs}
        )
    except HTTPException:
        # Re-raise the route's own 4xx. The handler below converts any
        # Exception to a 500, and HTTPException is an Exception, so a
        # deliberate 404/400 was reported as a server error. (#210)
        raise
    except Exception as e:
        logger.error(f"Error in collab_graph endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/api/collab/graph/{repo_id}", response_class=JSONResponse)
def collab_graph_data(request: Request, repo_id: str):
    """Return JSON data for collaboration network graph"""
    try:
        logger.info(f"Collaboration graph data requested for repo: {repo_id}")

        kquery = KospexQuery()
        collabs = kquery.get_collabs(repo_id=repo_id)

        return JSONResponse(content=collabs)
    except HTTPException:
        # Re-raise the route's own 4xx. The handler below converts any
        # Exception to a 500, and HTTPException is an Exception, so a
        # deliberate 404/400 was reported as a server error. (#210)
        raise
    except Exception as e:
        logger.error(f"Error in collab_graph_data endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/file-collab/{repo_id}/", response_class=HTMLResponse)
def file_collaboration(request: Request, repo_id: str):
    """Display file collaboration information"""
    try:
        logger.info(f"File collaboration page requested for repo: {repo_id}")
        _require_known_scope({"repo_id": repo_id}, repo_id)

        file_path = request.query_params.get("file_path")

        if not file_path:
            raise HTTPException(status_code=400, detail="file_path parameter is required")

        logger.info(f"File collaboration requested for: {file_path}")

        kquery = KospexQuery()
        collaborators = kquery.get_file_collaborators(repo_id=repo_id, file_path=file_path)

        return templates.TemplateResponse(
            request, "file_collaboration.html",
            {
                "collaborators": collaborators,
                "repo_id": repo_id,
                "file_path": file_path,
            },
        )
    except HTTPException:
        # Re-raise the route's own 4xx. The handler below converts any
        # Exception to a 500, and HTTPException is an Exception, so a
        # deliberate 404/400 was reported as a server error. (#210)
        raise
    except Exception as e:
        logger.error(f"Error in file_collaboration endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/orgs/", response_class=HTMLResponse)
@app.get("/orgs/{server}", response_class=HTMLResponse)
def orgs(request: Request, server: Optional[str] = None):
    """Display organization information"""
    try:
        logger.info(f"Organizations page requested with server: {server}")
        _require_known_scope({"server": server}, server)

        org = request.query_params.get("org")
        params = KospexWeb.get_id_params(server)

        kospex = KospexQuery()
        git_orgs = kospex.orgs()
        active_devs = kospex.active_devs(org=True)

        for row in git_orgs:
            row["active_devs"] = active_devs.get(row["org_key"], 0)

        return templates.TemplateResponse(request, "orgs.html", {"data": git_orgs})
    except HTTPException:
        # Re-raise the route's own 4xx. The handler below converts any
        # Exception to a 500, and HTTPException is an Exception, so a
        # deliberate 404/400 was reported as a server error. (#210)
        raise
    except Exception as e:
        logger.error(f"Error in orgs endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/syncs/", response_class=HTMLResponse)
def syncs(request: Request):
    """Stale repos and org sync runs, with the kgit commands to refresh them.

    Read-only, and not linked from the menus yet. See changes/202611-kweb-syncs.md.
    """
    from datetime import timezone
    from kospex import syncs_view as SyncsView
    try:
        query = request.query_params
        try:
            params = SyncsView.parse_params(query.get("tab"), query.get("org"),
                                            query.get("server"), query.get("days"))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        kospex = KospexQuery()
        now = datetime.now(timezone.utc)
        repos = kospex.get_repos()
        page = {"title": "Syncs", **params, "days_choices": SyncsView.DAYS_CHOICES,
                "picker": SyncsView.picker(repos, params["days"], now)}
        if params["tab"] == "repos":
            page["repos"] = SyncsView.repo_view(repos, org=params["org"],
                                                server=params["server"],
                                                days=params["days"], now=now)
        else:
            page["orgs"] = SyncsView.org_runs(kospex.kospex_db, org=params["org"],
                                              server=params["server"], now=now)
        return templates.TemplateResponse(request, "syncs.html", {"page": page})
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in syncs endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/syncs/run/{run_id}", response_class=HTMLResponse)
def syncs_run(request: Request, run_id: str):
    """One org sync run, repo by repo, and the org's repos the listing doesn't have.

    Read-only, and not linked from the menus yet. See changes/202611-kweb-sync-run.md.
    """
    from kospex import syncs_view as SyncsView
    try:
        try:
            page = SyncsView.run_view(KospexQuery().kospex_db, run_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="No org sync run with that id.")
        page["title"] = "Org sync run"
        context = {"page": page}
        # The server / org breadcrumb, as on the org's other pages. Only when the
        # org has repos in kospex: before any are synced, its crumbs would 404.
        if page.get("available"):
            org_key = page["run"]["org_key"]
            parsed = KospexUtils.parse_org_key(org_key)
            if parsed:
                # "github.com / acme" reads better than the org key. parse_org_key
                # decodes a nested group's "~~" back to "/".
                context["org_title"] = f"{parsed['git_server']} / {parsed['org']}"
                if KospexQuery().scope_exists({"org_key": org_key}):
                    context["crumb"] = parsed
        return templates.TemplateResponse(request, "syncs_run.html", context)
    except HTTPException:
        # The 404 above, not a 500 from the handler below. (#210)
        raise
    except Exception as e:
        logger.error(f"Error in syncs run endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


def _repos_page_figures(kospex, org_key, server):
    """(repo rows, {repo_id: active developers}, developers) for /repos/.

    Read from developer_stats, or from commits while any repo's stats are
    missing or out of date (see developer_stats_coverage()). Both methods of
    each pair take the same arguments and give rows the page reads alike.
    """
    scope = {"org_key": org_key, "server": server}
    if any(kospex.developer_stats_coverage().values()):
        repos_name, devs_name, active_name = "repos", "developers", "active_devs"
    else:
        repos_name, devs_name, active_name = (
            "repo_summaries", "developer_statuses", "active_devs_from_stats")

    rows = cached_query(
        repos_name, lambda: getattr(kospex, repos_name)(**scope), **scope)
    active_devs = cached_query(active_name, getattr(kospex, active_name))
    developers = cached_query(
        devs_name, lambda: getattr(kospex, devs_name)(**scope), **scope)
    return rows, active_devs, developers


@app.get("/repos/", response_class=HTMLResponse)
@app.get("/repos/{id}", response_class=HTMLResponse)
def repos(request: Request, id: Optional[str] = None):
    """Display repository information"""
    try:
        logger.info(f"Repositories page requested with id: {id}")

        params = KospexWeb.get_id_params(id)
        _require_known_scope(params, id)
        repo_id = request.query_params.get("repo_id") or params.get("repo_id")
        org_key = request.query_params.get("org_key") or params.get("org_key")
        server = request.query_params.get("server") or params.get("server")

        kospex = KospexQuery()

        page = {}
        # TODO - validate params
        techs = None
        # Maintenance ranges
        ranges = None

        page["repo_id"] = repo_id

        data = []

        all_repos = kospex.get_repos()
        repo_lookup = {d["_repo_id"]: d for d in all_repos}

        if org_key:
            parsed = KospexUtils.parse_org_key(org_key)
            if parsed:
                page["git_server"] = parsed["git_server"]
                page["git_owner"] = parsed["org"]
                techs = kospex.tech_landscape(org_key=org_key)
                ranges = kospex.commit_ranges2(org_key=org_key)
        elif server:
            page["git_server"] = server

        rows, active_devs, developers = _repos_page_figures(kospex, org_key, server)
        # Copy the cached rows: the loops below add keys to them.
        data = [dict(row) for row in rows]
        for row in data:
            row["active_devs"] = active_devs.get(row["_repo_id"], 0)

        developer_status = KospexUtils.repo_stats(developers, "last_commit")

        for repo in data:
            if r := repo_lookup.get(repo.get("_repo_id")):
                repo["years_active"] = r.get("years_active")

        return templates.TemplateResponse(
            request, "repos.html",
            {
                "data": data,
                "page": page,
                "ranges": ranges,
                "techs": techs,
                "developer_status": developer_status,
            },
        )
    except HTTPException:
        # Re-raise the route's own 4xx. The handler below converts any
        # Exception to a 500, and HTTPException is an Exception, so a
        # deliberate 404/400 was reported as a server error. (#210)
        raise
    except Exception as e:
        logger.error(f"Error in repos endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/repo/{repo_id}", response_class=HTMLResponse)
def repo(request: Request, repo_id: str):
    """Display individual repository information"""
    try:
        logger.info(f"Repository view requested for repo: {repo_id}")

        parsed = KospexUtils.parse_repo_id(repo_id)
        if not parsed:
            raise HTTPException(status_code=404, detail="Repository not found")
        _require_known_scope({"repo_id": repo_id}, repo_id)

        kospex = KospexQuery()
        commit_ranges = kospex.commit_ranges(repo_id)
        email_domains = kospex.email_domains(repo_id=repo_id)
        summary = kospex.author_summary(repo_id)
        techs = kospex.tech_landscape(repo_id=repo_id)

        developers = kospex.developers(repo_id=repo_id)
        developer_status = KospexUtils.repo_stats(developers, "last_commit")

        # TODO - make generic function for radar graph (in developer view too)
        labels = []
        datapoints = []
        count = 0
        for tech in techs:
            labels.append(tech["Language"])
            datapoints.append(tech["count"])
            count += 1
            if count > 10:
                break

        return templates.TemplateResponse(
            request, "repo_view.html",
            {
                "repo_id": repo_id,
                "git_server": parsed["git_server"],
                "org": parsed["org"],
                "org_key": parsed["org_key"],
                "repo": parsed["repo"],
                "entity_type": "Repository",
                "ranges": commit_ranges,
                "email_domains": email_domains,
                "landscape": techs,
                "developer_status": developer_status,
                "labels": labels,
                "datapoints": datapoints,
                "summary": summary,
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in repo endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/org/{org_key}", response_class=HTMLResponse)
def org_view(request: Request, org_key: str):
    """Display individual organisation information (mirrors the repo view)."""
    try:
        logger.info(f"Organisation view requested for org_key: {org_key}")

        parsed = KospexUtils.parse_org_key(org_key)
        if not parsed:
            raise HTTPException(status_code=404, detail="Organisation not found")
        _require_known_scope({"org_key": org_key}, org_key)

        kospex = KospexQuery()
        commit_ranges = kospex.commit_ranges(org_key=org_key)
        techs = kospex.tech_landscape(org_key=org_key)

        developers = kospex.developers(org_key=org_key)
        developer_status = KospexUtils.repo_stats(developers, "last_commit")

        org_repos = kospex.repos(org_key=org_key)
        active_devs = kospex.active_devs()
        for row in org_repos:
            row["active_devs"] = active_devs.get(row["_repo_id"], 0)

        # TODO - make generic function for radar graph (in repo/developer views too)
        labels = []
        datapoints = []
        count = 0
        for tech in techs:
            labels.append(tech["Language"])
            datapoints.append(tech["count"])
            count += 1
            if count > 10:
                break

        return templates.TemplateResponse(
            request, "org_view.html",
            {
                "org_key": org_key,
                "git_server": parsed["git_server"],
                "org": parsed["org"],
                "entity_type": "Organisation",
                "ranges": commit_ranges,
                "landscape": techs,
                "developer_status": developer_status,
                "labels": labels,
                "datapoints": datapoints,
                "repos": org_repos,
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in org_view endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/key-person/{repo_id}", response_class=HTMLResponse)
def key_person(request: Request, repo_id: str):
    """Display key person analysis for a repository"""
    try:
        logger.info(f"Key person view requested for repo: {repo_id}")
        _require_known_scope({"repo_id": repo_id}, repo_id)

        kospex = KospexQuery()

        # Get commits summary data (same as repo view)
        commit_ranges = kospex.commit_ranges(repo_id)

        # Get developer status (same as repo view)
        developers = kospex.developers(repo_id=repo_id)
        developer_status = KospexUtils.repo_stats(developers, "last_commit")

        # Get key person data
        key_people = kospex.key_person(repo_id=repo_id, top=5)

        return templates.TemplateResponse(
            request, "key_person.html",
            {
                "repo_id": repo_id,
                "ranges": commit_ranges,
                "developer_status": developer_status,
                "key_people": key_people,
            },
        )
    except HTTPException:
        # Re-raise the route's own 4xx. The handler below converts any
        # Exception to a 500, and HTTPException is an Exception, so a
        # deliberate 404/400 was reported as a server error. (#210)
        raise
    except Exception as e:
        logger.error(f"Error in key_person endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/landscape/", response_class=HTMLResponse)
@app.get("/landscape/{id}", response_class=HTMLResponse)
def landscape(request: Request, id: Optional[str] = None):
    """Serve up the technology landscape metadata"""
    try:
        logger.info(f"Technology landscape page requested with id: {id}")

        kospex = KospexQuery()

        params = KospexWeb.get_id_params(id)
        _require_known_scope(params, id)
        repo_id = request.query_params.get("repo_id") or params.get("repo_id")
        org_key = request.query_params.get("org_key") or params.get("org_key")
        data = kospex.tech_landscape(org_key=org_key, repo_id=repo_id)

        download = request.query_params.get("download")

        if download:
            # Download tech landscape data as CSV
            return download_csv_fastapi(data, "tech_landscape.csv")
        else:
            # Repo scope: breadcrumb (server / org / repo / page), and the
            # per-technology links carry repo_id so /tech/ lists only this
            # repo's files.
            crumbs = (KospexWeb.scope_context(
                {"repo_id": repo_id} if repo_id else {"org_key": org_key},
                "Technology Landscape") if (repo_id or org_key) else {})
            repo_qs = (f"?repo_id={quote(repo_id, safe='')}" if repo_id else
                       f"?org_key={quote(org_key, safe='')}" if org_key else "")
            # Download keeps whichever scope the page was opened with: a path
            # id, ?repo_id= or ?org_key= (an empty id rendered "/landscape/None").
            if id:
                download_url = f"/landscape/{quote(id, safe='~')}?download=true"
            elif repo_id:
                download_url = f"/landscape/?repo_id={quote(repo_id, safe='')}&download=true"
            elif org_key:
                download_url = f"/landscape/?org_key={quote(org_key, safe='')}&download=true"
            else:
                download_url = "/landscape/?download=true"
            return templates.TemplateResponse(
                request, "landscape.html",
                {"data": data, "org_key": org_key, "id": id,
                 "repo_qs": repo_qs, "download_url": download_url, **crumbs},
            )
    except HTTPException:
        # Re-raise the route's own 4xx. The handler below converts any
        # Exception to a 500, and HTTPException is an Exception, so a
        # deliberate 404/400 was reported as a server error. (#210)
        raise
    except Exception as e:
        logger.error(f"Error in landscape endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


def _label_bots(rows, prefixes):
    """Add `<prefix>_is_bot` / `<prefix>_bot_type` to each row for each
    `<prefix>_email` column, using the same detector as `krunner key-person`.
    """
    from kospex_email import EmailAnalyzer

    analyzer = EmailAnalyzer()
    for row in rows:
        for prefix in prefixes:
            info = analyzer.analyze(row[f"{prefix}_email"])
            row[f"{prefix}_is_bot"] = bool(info.is_bot)
            row[f"{prefix}_bot_type"] = info.bot_type or ""
    return rows


def _label_people(rows, column):
    """Add "email" and the bot label to developer rows.

    Uses the same detector as `krunner key-person` / `orphans`
    (EmailAnalyzer), so the label agrees with them. It covers the common
    bots -- GitHub's web-flow identity, Dependabot, several build accounts --
    and misses others (Gerrit, TeamCity, copybara), which show unlabelled.
    """
    from kospex_email import EmailAnalyzer

    analyzer = EmailAnalyzer()
    for row in rows:
        row["email"] = row[column]
        info = analyzer.analyze(row[column])
        row["is_bot"] = bool(info.is_bot)
        row["bot_type"] = info.bot_type or ""
    return rows


# The developers CSV, after its email column
DEVELOPER_CSV_COLUMNS = ("commits", "repos", "first_commit", "last_commit",
                         "last_seen", "is_bot", "bot_type")


def _list_params(query_params):
    """parse_list_params(), with a bad value as a 400."""
    try:
        return developers_view.parse_list_params(query_params)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def _developers_scope(kq, id, query_params):
    """{} or one of repo_id, org_key, server for /developers/. Unknown is a 404.

    A path id wins over ?repo_id=, then ?org_key=, then ?server=. Org and repo
    ids contain "~"; any other id is a server name.
    """
    if id:
        if "~" not in id:
            scope = {"server": id}
            # A server name with no dot can also pass is_base64(), so the
            # server is looked up before the id is read as a developer.
            if (not kq.scope_exists(scope) and KospexUtils.is_base64(id)
                    and "@" in (KospexUtils.decode_base64(id) or "")):
                raise HTTPException(
                    status_code=404,
                    detail="Developer lists are per server, org or repo. For one "
                           "developer use /developers/?author_email=",
                )
        elif KospexUtils.parse_org_key(id):
            scope = {"org_key": id}
        else:
            scope = {"repo_id": id}
    else:
        scope = next(
            ({key: query_params[key]} for key in ("repo_id", "org_key", "server")
             if query_params.get(key)), {})

    if scope:
        _require_known_scope(scope, next(iter(scope.values())))
    return scope


def _developer_technology(author_email):
    """An author's languages, with the radar chart's labels and datapoints."""
    techs = KospexQuery().author_languages(author_email)
    # The chart is small: eight named languages. The table keeps them all.
    top = [t for t in techs if t["language"] != "Unknown"][:8]
    return techs, [t["language"] for t in top], [t["commits"] for t in top]


@app.get("/developers/", response_class=HTMLResponse)
@app.get("/developers/{id}", response_class=HTMLResponse)
def developers(request: Request, id: Optional[str] = None):
    """Developer info page"""
    try:
        logger.info("Developers page requested with id: %s", id)

        author_email = request.query_params.get("author_email")
        if author_email:
            logger.info(f"Developer view requested for: {author_email}")
            # Github uses +, which get interpreted as a " " in the URL.
            author_email = author_email.replace(" ", "+")
            repo_list = KospexQuery().repos_by_author(author_email)
            techs, labels, datapoints = _developer_technology(author_email)

            developer_name, github_handle = KospexUtils.developer_identity(
                author_email)

            return templates.TemplateResponse(
                request, "developer_view.html",
                {
                    "repos": repo_list,
                    "tech": techs,
                    "author_email": author_email,
                    "labels": labels,
                    "developer_name": developer_name,
                    "github_handle": github_handle,
                    "datapoints": datapoints,
                },
            )

        download = request.query_params.get("download")
        view = request.query_params.get("view", "authors")
        if view not in ("authors", "committers"):
            raise HTTPException(
                status_code=400,
                detail="view must be 'authors' or 'committers'.")
        days = request.query_params.get("days")
        if days is not None:
            # A window is a positive whole number of days. Anything else used
            # to reach int() in the query and come back as a 500.
            if not days.isascii() or not days.isdigit() or int(days) < 1:
                raise HTTPException(
                    status_code=400,
                    detail="days must be a whole number of days, 1 or more.")
            days = str(int(days))
        params = _list_params(request.query_params)

        kq = KospexQuery()
        scope = _developers_scope(kq, id, request.query_params)
        context = _developers_context(kq, scope, view, days, params, download)
        if download:
            column = "committer_email" if view == "committers" else "author_email"
            return download_csv_fastapi(
                context, f"{view if view == 'committers' else 'developers'}.csv",
                fieldnames=[column, *DEVELOPER_CSV_COLUMNS])
        return templates.TemplateResponse(request, "developers.html", context)
    except HTTPException:
        # Re-raise the route's own 4xx. The handler below converts any
        # Exception to a 500, and HTTPException is an Exception, so a
        # deliberate 404/400 was reported as a server error. (#210)
        raise
    except Exception as e:
        logger.error(f"Error in developers endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


def _developers_context(kq, scope, view, days, params, download):
    """The developers.html context, or the CSV rows when downloading."""
    column = "committer_email" if view == "committers" else "author_email"
    # Status and search apply to the all-time authors view only.
    filtered = view == "authors" and not days
    search = params["q"] if filtered else None
    status = (params["status"] or "all") if filtered else None

    def listing(**kwargs):
        return kq.developer_list(
            scope, column=column, days=days, search=search,
            sort=params["sort"], direction=params["dir"], **kwargs)

    if download:
        # Without ?status= a download is everyone in the scope
        chosen = params["status"] if filtered and params["status"] != "all" else None
        rows, _ = listing(status=chosen)
        for row in rows:
            row.pop("status", None)
        _label_people(rows, column)
        for row in rows:
            row.pop("email")  # a template helper, not a CSV column
        return rows

    counts = cached_query(
        "developer_status_counts",
        lambda: kq.developer_status_counts(scope), **scope)
    # Too many active developers to list, and nothing asked for: an overview
    overview = (filtered and not params["status"] and not search
                and counts["active"] > developers_view.LIST_THRESHOLD)
    if overview:
        status = "active"  # what the overview and its download are of

    chosen = status if status != "all" else None
    busiest, busiest_label = [], None
    if overview:
        rows, total = [], counts["active"]
        pager = developers_view.page_window(0, 1)
        if not scope.get("repo_id"):
            by = "repo" if scope.get("org_key") else "org"
            busiest_label = f"{by}s"
            found = cached_query(
                "busiest_scopes",
                lambda: kq.busiest_scopes(
                    scope, by=by, limit=developers_view.BUSIEST_LIMIT),
                by=by, **scope)
            key = "repo_id" if by == "repo" else "org_key"
            busiest = []
            for row in found:
                name, target = row["name"], {key: row["id"]}
                if by == "org" and not name:
                    # Repos with no org (go.googlesource.com): the id is "server~"
                    server = row["id"].removesuffix("~")
                    name, target = f"{server} (no org)", {"server": server}
                busiest.append({**row, "name": name, "url": developers_view.developers_url(
                    target, status="active")})
    else:
        rows, total = listing(status=chosen, limit=developers_view.PAGE_SIZE,
                              offset=(params["page"] - 1) * developers_view.PAGE_SIZE)
        if total <= developers_view.LIST_THRESHOLD:
            rows, total = listing(status=chosen)
        pager = developers_view.page_window(total, params["page"])
        _label_people(rows, column)

    # The cards: authors and committers, within the window when there is one
    if not days:
        data = {"authors": counts["all"],
                "committers": cached_query(
                    "committer_count", lambda: kq.committer_count(scope), **scope)}
    elif view == "committers":
        data = {"authors": kq.developer_list(scope, days=days, limit=0)[1],
                "committers": total}
    else:
        data = {"authors": total,
                "committers": kq.committer_count(scope, days=days)}

    committers = "committers" if view == "committers" else None

    def link(**changes):
        """This list's URL with some parameters changed."""
        state = {"view": committers, "days": days,
                 "status": params["status"] if filtered else None, "q": search,
                 "sort": params["sort"], "dir": params["dir"], "page": params["page"]}
        state.update(changes)
        default_sort = (state["sort"] == "last_seen" and state["dir"] == "asc")
        if default_sort:
            state["sort"] = state["dir"] = None
        if state["page"] == 1:
            state["page"] = None
        return developers_view.developers_url(scope, **state)

    sort_links = {}
    for key, default in developers_view.SORT_DEFAULT_DIRECTION.items():
        direction = default
        if key == params["sort"]:
            direction = "desc" if params["dir"] == "asc" else "asc"
        sort_links[key] = link(sort=key, dir=direction, page=1)

    # Half-size versions of the summary's circles, hidden where all-time
    # status counts are not what is listed: a search, a window, committers
    circles = []
    if filtered and not search:
        circles = KospexWeb.status_circles(
            {key.capitalize(): counts[key] for key in developers_view.STATUSES[:-1]},
            min_px=48, px_per_percent=0.48)
        for circle in circles:
            key = circle["key"].lower()
            circle.update(
                # Marked only when asked for, not for the default active view
                status=key, current=key == params["status"],
                url=developers_view.developers_url(scope, status=key))

    crumbs = {}
    if scope:
        crumbs = KospexWeb.scope_context(scope, "Developers")
        if view == "committers":
            # server / org / repo / Developers / Committers
            crumbs["sub_page"] = "Committers"
            crumbs["page_url"] = developers_view.developers_url(scope)

    repo_id = scope.get("repo_id")
    return {
        "people": rows, "data": data, "view": view, "days": days,
        "mode": "overview" if overview else "list",
        "busiest": busiest, "busiest_label": busiest_label,
        "active_url": developers_view.developers_url(scope, status="active"),
        "all_url": developers_view.developers_url(scope, status="all"),
        "status": status, "q": search, "filtered": filtered,
        "circles": circles, "sort_links": sort_links,
        # What the page calls each status, as on the summary page
        "status_names": {key.lower(): name for key, name, _ in KospexWeb.SUMMARY_STATUSES},
        "status_colours": {key.lower(): colour for key, _, colour in KospexWeb.SUMMARY_STATUSES},
        "sort": params["sort"], "direction": params["dir"],
        "pager": pager, "total": total,
        "prev_url": link(page=pager["page"] - 1) if pager["page"] > 1 else None,
        "next_url": link(page=pager["page"] + 1) if pager["page"] < pager["pages"] else None,
        "first_url": link(page=1),
        "search_action": developers_view.developers_url(scope) if filtered else None,
        # A search stays inside the scope, so the box names it and offers wider
        "search_where": crumbs.get("scope_name") or "all repositories",
        "search_all_url": developers_view.developers_url({}, q=search) if scope else None,
        # Row links reach the commits page, which takes a repo_id.
        "row_scope": f"&repo_id={quote(repo_id, safe='')}" if repo_id else "",
        "authors_url": developers_view.developers_url(scope, days=days),
        "committers_url": developers_view.developers_url(scope, view="committers", days=days),
        # "Show all time" drops the window, and stays on this view.
        "more_url": developers_view.developers_url(scope, view=committers),
        "download_url": developers_view.developers_url(
            scope, view=committers, days=days, status=chosen, q=search,
            download="true"),
        # The collaboration pages are per repository only.
        "collab_url": f"/collab/{repo_id}" if repo_id else None,
        **crumbs,
    }


@app.get("/graph/", response_class=HTMLResponse)
@app.get("/graph/{org_key}", response_class=HTMLResponse)
def graph(request: Request, org_key: Optional[str] = None):
    """Force directed graphs for data in the Kospex DB."""
    try:
        logger.info(f"Graph page requested with org_key: {org_key}")
        _require_known_scope({"org_key": org_key}, org_key)
        focus = None

        author_email = request.query_params.get("author_email")
        if author_email:
            # This is a weird old skool http thing
            # where spaces were represented by + signs
            author_email = author_email.replace(" ", "+")
        repo_id = request.query_params.get("repo_id")

        if repo_id:
            org_key = f"?repo_id={repo_id}"
            focus = "files"
        elif author_email:
            org_key = f"?author_email={author_email}"

        return templates.TemplateResponse(
            request, "graph.html", {"org_key": org_key, "focus": focus}
        )
    except HTTPException:
        # Re-raise the route's own 4xx. The handler below converts any
        # Exception to a 500, and HTTPException is an Exception, so a
        # deliberate 404/400 was reported as a server error. (#210)
        raise
    except Exception as e:
        logger.error(f"Error in graph endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/org-graph/", response_class=JSONResponse)
@app.get("/org-graph/{org_key}", response_class=JSONResponse)
@app.get("/org-graph/{focus}/{org_key}", response_class=JSONResponse)
def org_graph(request: Request, org_key: Optional[str] = None, focus: Optional[str] = None):
    """Return JSON data for the force directed graph."""
    try:
        logger.info(f"Org graph data requested - focus: {focus}, org_key: {org_key}")

        return graph_service.get_graph_data(focus, org_key, dict(request.query_params))
    except HTTPException:
        # Re-raise the route's own 4xx. The handler below converts any
        # Exception to a 500, and HTTPException is an Exception, so a
        # deliberate 404/400 was reported as a server error. (#210)
        raise
    except Exception as e:
        logger.error(f"Error in org_graph endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/tenure/", response_class=HTMLResponse)
@app.get("/tenure/{id}", response_class=HTMLResponse)
def tenure(request: Request, id: Optional[str] = None):
    """
    View developer tenure for all, a server, org or a repo
    """
    try:
        logger.info(f"Tenure page requested with id: {id}")

        # TODO - CHECK THIS FOR SECURITY
        params = KospexWeb.get_id_params(id)

        # get_id_params also decodes a base64 author_email id, which
        # developers() does not take: this was a 500. Tenure is per repo, org
        # or server, as the summary page is.
        if params.get("author_email"):
            raise HTTPException(
                status_code=404,
                detail="Tenure pages are per repo, org or server. See /developers/ for a developer.",
            )
        _require_known_scope(params, id)

        developers = KospexQuery().developers(**params)
        active_devs = []
        dev_leavers = []

        for entry in developers:
            entry["tenure_status"] = KospexUtils.get_status(entry["tenure"])

        for dev in developers:
            if "Active" == dev.get("status"):
                active_devs.append(dev)
            else:
                if KospexUtils.days_ago(dev["last_commit"]) < 365:
                    dev_leavers.append(dev)

        # Calculate developers who've left and the distribugion
        for entry in dev_leavers:
            entry["tenure_status"] = KospexUtils.get_status(entry["tenure"])

        leavers = KospexUtils.get_status_distribution(dev_leavers)

        data = {}

        data["leavers"] = len(dev_leavers)

        data["developers"] = len(developers)
        data["active_devs"] = len(active_devs)

        days_values = [entry["tenure"] for entry in developers]
        active_days_values = [entry["tenure"] for entry in active_devs]

        commit_stats = KospexQuery().get_activity_stats(params)
        data["days_active"] = commit_stats.get("days_active")
        data["years_active"] = commit_stats.get("years_active")
        data["repos"] = commit_stats.get("repos")
        data["commits"] = commit_stats.get("commits")

        no_commits_message = None
        if not data["commits"]:
            kind = next((name for key, name in _SCOPE_KINDS.items() if params.get(key)), None)
            no_commits_message = (
                f"No commits synced for this {kind}." if kind else "No commits synced yet."
            )

        data |= KospexStats.tenure_stats(days_values)

        # Holds the active stats
        active_data = KospexStats.tenure_stats(active_days_values)

        distribution = KospexUtils.get_status_distribution(developers)
        active_d = KospexUtils.get_status_distribution(active_devs)

        return templates.TemplateResponse(
            request, "tenure.html",
            {
                "data": data,
                "active_data": active_data,
                "distribution": distribution,
                "developers": developers,
                "active_distribution": active_d,
                "leavers": leavers,
                "no_commits_message": no_commits_message,
                **KospexWeb.scope_context(params, "Tenure"),
            },
        )
    except HTTPException:
        # Re-raise the route's own 4xx. The handler below converts any
        # Exception to a 500, and HTTPException is an Exception, so a
        # deliberate 404/400 was reported as a server error. (#210)
        raise
    except Exception as e:
        logger.error(f"Error in tenure endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/meta/author-domains", response_class=HTMLResponse)
def author_domains(request: Request):
    """Display author email domain analysis"""
    try:
        logger.info("Author domains page requested")
        days = request.query_params.get("days")
        if days:
            if days.isdigit():
                days = int(days)
            else:
                logger.error(f"Invalid days parameter: {days}")
                days = None
        else:
            days = None

        kospex = KospexQuery()
        email_domains = kospex.email_domains(days=days)

        return templates.TemplateResponse(
            request, "meta-author-domains.html",
            {"days": days, "email_domains": email_domains},
        )
    except HTTPException:
        # Re-raise the route's own 4xx. The handler below converts any
        # Exception to a 500, and HTTPException is an Exception, so a
        # deliberate 404/400 was reported as a server error. (#210)
        raise
    except Exception as e:
        logger.error(f"Error in author_domains endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/tech-change/", response_class=HTMLResponse)
def tech_change(request: Request):
    """Technology change radar visualization"""
    try:
        logger.info("Tech change radar page requested")

        labels = ["Java", "Go", "JavaScript", "Python", "Kotlin"]

        return templates.TemplateResponse(
            request, "tech-change.html", {"labels": labels}
        )
    except HTTPException:
        # Re-raise the route's own 4xx. The handler below converts any
        # Exception to a 500, and HTTPException is an Exception, so a
        # deliberate 404/400 was reported as a server error. (#210)
        raise
    except Exception as e:
        logger.error(f"Error in tech_change endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


def _absent_requested(request: Request) -> bool:
    """Whether the caller asked for the negative view: `?absent=true`.

    A bare `?absent` is what a person types, so its empty value counts as yes;
    an explicit false/0/no counts as no. Shared by /tag/ and /tech/ so the two
    cannot drift on which spellings they accept.
    """
    raw = request.query_params.get("absent")

    return raw is not None and raw.lower() not in ("false", "0", "no")


@app.get("/tech/{tech}", response_class=HTMLResponse)
def repo_with_tech(request: Request, tech: str):
    """Show repositories with the given technology"""
    try:
        logger.info(f"Tech filtering page requested for technology: {tech}")

        repo_id = request.query_params.get("repo_id")
        # Org scope only narrows the repository listing; a repo_id wins.
        org_key = None if repo_id else request.query_params.get("org_key")
        if org_key:
            _require_known_scope({"org_key": org_key}, org_key)
        absent = _absent_requested(request)
        kospex = KospexQuery()
        template = "repos-tech.html"
        absent_count = None
        universe = None

        try:
            if repo_id:
                # Files in one repository that lack the technology is not a
                # question with an answer, so the drill-down wins.
                if not kospex.has_tech(tech):
                    raise ValueError(f"no current file has '{tech}'")
                repos_with_tech = kospex.repo_files(tech, repo_id=repo_id)
                template = "repo_files.html"
            elif absent:
                repos_with_tech = kospex.repos_without_tech(tech, org_key=org_key)
                universe = kospex.count_repos_with_file_metadata(org_key=org_key)
            else:
                repos_with_tech = kospex.repos_with_tech(tech, org_key=org_key)
                if not repos_with_tech:
                    # No current file anywhere has it, so this is a bad URL,
                    # not an estate with none of it. A 200 with an empty table
                    # reads as "no repositories have Fortran" -- the confusion
                    # #293 exists to remove, and the same call /tag/ makes.
                    raise ValueError(f"no current file has '{tech}'")
                # The other side of the estate, as on /tag/: how many
                # repositories we have looked at and found no file of this
                # technology in.
                universe = kospex.count_repos_with_file_metadata(org_key=org_key)
                absent_count = max(universe - len(repos_with_tech), 0)
        except ValueError as error:
            # Both words, because /tech/ serves scc's `Language` and what the
            # Landscape pages call a technology -- a reader cannot be expected
            # to know which one they were asking for.
            logger.info(f"Unknown technology requested: {tech} ({error})")
            # Names what was asked for: the URL may have come from a paste or
            # a long link whose end the reader cannot see, and the browser
            # shows the percent-encoded form. Jinja autoescapes it into the
            # template, which the tests assert rather than assume.
            raise HTTPException(
                status_code=404,
                detail=f"{tech} is an unknown Technology or Language.")

        # Breadcrumb for the per-repository drill-down: server / org / repo / page.
        crumbs = (KospexWeb.scope_context({"repo_id": repo_id}, f"Technology: {tech}")
                  if repo_id else
                  KospexWeb.scope_context({"org_key": org_key}, f"Technology: {tech}")
                  if org_key else {"page": {}})
        absent_url = f"/tech/{quote(tech, safe='')}?absent=true"
        if org_key:
            absent_url += f"&org_key={quote(org_key, safe='')}"

        return templates.TemplateResponse(
            request, template,
            {"data": repos_with_tech, "tech": tech, "tech_kind": "Technology",
             "tech_url": f"/tech/{quote(tech, safe='')}",
             # Only the repo listing counts repositories; the file listing for
             # a single repo is a different template.
             "repo_count": None if repo_id else len(repos_with_tech),
             "absent_view": absent and not repo_id,
             "absent_count": absent_count,
             "absent_url": absent_url,
             "universe_count": universe,
             **crumbs},
        )
    except HTTPException:
        # Re-raise the route's own 4xx. The handler below converts any
        # Exception to a 500, and HTTPException is an Exception, so a
        # deliberate 404/400 was reported as a server error. (#210)
        raise
    except Exception as e:
        logger.error(f"Error in repo_with_tech endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/tags/", response_class=HTMLResponse)
def tags_index(request: Request):
    """List the tag vocabulary, as an entry point to /tag/{tag}.

    The vocabulary is panopticas.get_tags() -- what panopticas *can* assign,
    derived from its detection rules. That is deliberately not "the tags in
    this database": the two differ, and this is the set where_tag() validates
    against, so every tag listed here resolves rather than 404ing. Some will
    have no repositories.

    No counts. Counting repositories per tag is not a single-table lookup:
    tech_type holds several tags per row, and grouping by the stored value
    double-counts repositories.

    Retired tags are listed separately, beside their replacement (#317). Files
    recorded before a rename still carry the old tag, and where_tag() accepts
    it, so it has a page worth reaching.
    """
    try:
        logger.info("Tag index page requested")

        # Case-insensitive, or every lowercase tag would file after `yarn` and
        # the page would run the alphabet twice.
        tags = sorted(Panopticas.get_tags(), key=str.lower)
        retired = sorted(retired_tags().items(), key=lambda kv: kv[0].lower())

        return templates.TemplateResponse(
            request, "tags.html",
            {"tags": tags, "retired": retired, "page": {}},
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in tags_index endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/tag/{tag}", response_class=HTMLResponse)
def repo_with_tag(request: Request, tag: str):
    """Show repositories carrying a panopticas tag, or its files in one repo.

    Deliberately a separate route from /tech/{tech} rather than a fallback
    inside it. 9 names -- `Go`, `Python`, `JavaScript`, `C#` and others -- are
    both a Language and a tag, and they count very different things: `Go` is
    24,236 files as a Language and 345 as a tag. A route that guessed between
    them, in either order, would not be trustworthy; two routes keep both
    meanings addressable.
    """
    try:
        logger.info(f"Tag filtering page requested for tag: {tag}")

        repo_id = request.query_params.get("repo_id")
        # Org scope only narrows the repository listing; a repo_id wins.
        org_key = None if repo_id else request.query_params.get("org_key")
        if org_key:
            _require_known_scope({"org_key": org_key}, org_key)
        absent = _absent_requested(request)
        kospex = KospexQuery()
        template = "repos-tech.html"
        absent_count = None
        universe = None

        try:
            if repo_id:
                # Files in one repository that lack the tag is not a question
                # with an answer, so the drill-down wins over `absent`.
                data = kospex.repo_files_by_tag(tag, repo_id=repo_id)
                template = "repo_files.html"
            elif absent:
                data = kospex.repos_without_tag(tag, org_key=org_key)
                universe = kospex.count_repos_with_file_metadata(org_key=org_key)
            else:
                data = kospex.repos_with_tag(tag, org_key=org_key)
                # The other side of the estate: how many repositories we have
                # looked at and found no file with this tag. This is the
                # adoption question -- "everyone should have a Dependabot
                # file" is only readable next to how many don't.
                universe = kospex.count_repos_with_file_metadata(org_key=org_key)
                absent_count = max(universe - len(data), 0)
        except ValueError as error:
            # where_tag() rejects a tag panopticas does not define. That is a
            # bad URL, so say so: a 200 with an empty table reads as "no
            # repositories have this tag", which is the confusion this route
            # exists to remove.
            logger.info(f"Unknown tag requested: {tag} ({error})")
            raise HTTPException(
                status_code=404, detail=f"{tag} is an unknown Tag.")

        retired, replacement = retired_tag_replacement(tag)
        # Breadcrumb for the per-repository drill-down: server / org / repo / page.
        crumbs = (KospexWeb.scope_context({"repo_id": repo_id}, f"Tag: {tag}")
                  if repo_id else
                  KospexWeb.scope_context({"org_key": org_key}, f"Tag: {tag}")
                  if org_key else {"page": {}})
        absent_url = f"/tag/{quote(tag, safe='')}?absent=true"
        if org_key:
            absent_url += f"&org_key={quote(org_key, safe='')}"

        return templates.TemplateResponse(
            request, template,
            {"data": data, "tech": tag, "tech_kind": "Tag",
             "tag_retired": retired, "tag_replacement": replacement,
             "tech_url": f"/tag/{quote(tag, safe='')}",
             "repo_count": None if repo_id else len(data),
             "absent_view": absent and not repo_id,
             "absent_count": absent_count,
             "absent_url": absent_url,
             "universe_count": universe,
             **crumbs},
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in repo_with_tag endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/developer/", response_class=HTMLResponse)
@app.get("/developer/{id}", response_class=HTMLResponse)
def developer_view(request: Request, id: Optional[str] = None):
    """
    View individual developer details
    """
    try:
        logger.info(f"Developer view requested with id: {id}")

        author_email = request.query_params.get("author_email")

        if id:
            author_email = KospexUtils.decode_base64(id)

        if author_email is None:
            logger.error("No email passed to Developer view")
            return templates.TemplateResponse(
                request, "404.html",
                {
                    "error": "No email passed to Developer view",
                },
            )

        # Github uses +, which get interpreted as a " " in the URL.
        if author_email:
            author_email = author_email.replace(" ", "+")

        repo_list = KospexQuery().repos_by_author(author_email)
        techs, labels, datapoints = _developer_technology(author_email)
        developer_name, github_handle = KospexUtils.developer_identity(
            author_email)

        return templates.TemplateResponse(
            request, "developer_view.html",
            {
                "repos": repo_list,
                "tech": techs,
                "author_email": author_email,
                "developer_name": developer_name,
                "github_handle": github_handle,
                "labels": labels,
                "datapoints": datapoints,
            },
        )
    except HTTPException:
        # Re-raise the route's own 4xx. The handler below converts any
        # Exception to a 500, and HTTPException is an Exception, so a
        # deliberate 404/400 was reported as a server error. (#210)
        raise
    except Exception as e:
        logger.error(f"Error in developer_view endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/observation/{uuid}", response_class=HTMLResponse)
def observation(request: Request, uuid: str):
    """
    Display observation information
    """
    try:
        logger.info(f"display single observations with uuid: {uuid}")

        kquery = KospexQuery()

        observation = kquery.get_single_observation(uuid=uuid)

        return templates.TemplateResponse(
            request, "observation.html",
            {
                "observation": observation,
            },
        )
    except HTTPException:
        # Re-raise the route's own 4xx. The handler below converts any
        # Exception to a 500, and HTTPException is an Exception, so a
        # deliberate 404/400 was reported as a server error. (#210)
        raise
    except Exception as e:
        logger.error(f"Error in repo_with_tech endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/observations/", response_class=HTMLResponse)
def observations(request: Request):
    """Display observation information"""
    try:
        logger.info("Observations page requested")

        kquery = KospexQuery()
        repo_id = request.query_params.get("repo_id")
        observation_key = request.query_params.get("observation_key")

        if observation_key:
            # We should have an observation key and a repo_id for this to work
            logger.info(f"Observation key: {observation_key}")
            return templates.TemplateResponse(
                request, "observations_repo_key.html",
                {
                    "data": kquery.observations_summary(
                        repo_id=repo_id, observation_key=observation_key
                    ),
                    "observation_key": observation_key,
                    "repo_id": repo_id,
                },
            )

        elif repo_id:
            logger.info(f"Repo ID: {repo_id}")
            return templates.TemplateResponse(
                request, "observations_repo.html",
                {
                    "data": kquery.observations_summary(repo_id=repo_id),
                    "repo_id": repo_id,
                },
            )
        else:
            return templates.TemplateResponse(
                request, "observations.html",
                {"data": kquery.observations_summary()},
            )
    except HTTPException:
        # Re-raise the route's own 4xx. The handler below converts any
        # Exception to a 500, and HTTPException is an Exception, so a
        # deliberate 404/400 was reported as a server error. (#210)
        raise
    except Exception as e:
        logger.error(f"Error in observations endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/commits/", response_class=HTMLResponse)
@app.get("/commits/{repo_id}", response_class=HTMLResponse)
def commits(request: Request, repo_id: Optional[str] = None):
    """
    Display Git commit information
    """
    try:
        logger.info("Commits page requested")
        _require_known_scope({"repo_id": repo_id}, repo_id)
        if repo_id is None:
            # If no repo_id is provided, use the query parameter
            # This allows for filtering by author or committer email
            repo_id = request.query_params.get("repo_id", "")

        author_email = request.query_params.get("author_email")
        committer_email = request.query_params.get("committer_email")

        logger.info(f"Author email: {author_email}")

        data = KospexQuery().commits(
            limit=1000,
            repo_id=repo_id,
            author_email=author_email,
            committer_email=committer_email,
        )

        return templates.TemplateResponse(
            request, "commits.html", {"commits": data, "repo_id": repo_id}
        )
    except HTTPException:
        # Re-raise the route's own 4xx. The handler below converts any
        # Exception to a 500, and HTTPException is an Exception, so a
        # deliberate 404/400 was reported as a server error. (#210)
        raise
    except Exception as e:
        logger.error(f"Error in commits endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/history/{repo_id}", response_class=HTMLResponse)
def commit_history(request: Request, repo_id: str):
    """Display commit history chart for a repository"""
    try:
        logger.info(f"Commit history page requested for repo: {repo_id}")
        _require_known_scope({"repo_id": repo_id}, repo_id)

        # Get year data (always show)
        year_data = KospexQuery().commit_history(repo_id, group_by='year', fill_gaps=True)

        # Get month data (optional, based on query param)
        show_monthly = request.query_params.get("monthly", "false") == "true"
        month_data = KospexQuery().commit_history(repo_id, group_by='month', fill_gaps=True) if show_monthly else []

        # Get repo metadata for display
        repo_info = KospexQuery().repos(repo_id=repo_id)
        repo_name = repo_info[0].get('_git_repo', repo_id) if repo_info else repo_id

        return templates.TemplateResponse(
            request, "commit_history.html",
            {
                "repo_id": repo_id,
                "repo_name": repo_name,
                "year_data": year_data,
                "month_data": month_data,
                "show_monthly": show_monthly
            }
        )
    except HTTPException:
        # Re-raise the route's own 4xx. The handler below converts any
        # Exception to a 500, and HTTPException is an Exception, so a
        # deliberate 404/400 was reported as a server error. (#210)
        raise
    except Exception as e:
        logger.error(f"Error in commit_history endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/dependencies/", response_class=HTMLResponse)
@app.get("/dependencies/{id}", response_class=HTMLResponse)
def dependencies(
    request: Request, id: Optional[str] = None, file_path: Optional[str] = None
):
    """Display SCA (Software Composition Analysis) information.
    file_path (query param) narrows to one dependency file, as linked from /osi/."""
    try:
        logger.info(f"Dependencies page requested with id: {id}")

        params = KospexWeb.get_id_params(id)
        try:
            data = KospexQuery().get_dependencies(request_id=params, file_path=file_path)
        except ValueError as exc:
            # A scope this query cannot honour -- e.g. an author email.
            # Refusing beats widening to every row. (#158)
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        freshness = KospexWeb.dependency_freshness(data, cache_seconds=url_cache_seconds())
        never_checked = []
        if freshness and freshness["never_checked"]:
            # Only repos with never-checked rows need their extraction outcome
            db = KospexQuery().kospex_db
            repo_ids = {r["_repo_id"] for r in data if not r.get("last_checked")}
            outcomes = {repo_id: get_osi_outcome(db, repo_id) for repo_id in repo_ids}
            never_checked = KospexWeb.never_checked_files(data, outcomes)

        # Dependency files kospex has no parser for are missing from the table
        # whether or not other files produced rows, so warn on every scope.
        # Same scope as get_dependencies above, which has already refused any
        # scope this query cannot honour.
        kquery = KospexQuery()
        dependency_files = kquery.get_dependency_files(request_id=params)
        if file_path:
            dependency_files = [f for f in dependency_files if f.get("Provider") == file_path]
        unsupported = KospexWeb.unsupported_dependency_files(dependency_files)

        empty_state = None
        if not data:
            # Say why the page is empty, and what to run: a newly synced repo
            # has no rows until krunner osi examines it.
            repo_id = params.get("repo_id")
            if repo_id:
                repo = next(iter(kquery.get_repos(repo_id=repo_id)), None)
                count_sql = (f"SELECT COUNT(*) AS n FROM {KospexSchema.TBL_FILE_METADATA} "
                             "WHERE _repo_id = ? AND latest = 1")
                file_count = next(iter(kquery.kospex_db.query(count_sql, [repo_id])))["n"]
                empty_state = KospexWeb.dependencies_empty_state(
                    repo_id, repo_scope=True, repo=repo, file_count=file_count,
                    dependency_files=dependency_files,
                    outcome=get_osi_outcome(kquery.kospex_db, repo_id),
                    file_path=file_path)
            else:
                empty_state = KospexWeb.dependencies_empty_state(id)

        context = {
            "data": data,
            "empty_state": empty_state,
            "unsupported": unsupported,
            "osi_url": f"/osi/{id}" if id else "/osi/",
            "freshness": freshness,
            "never_checked": never_checked,
            **KospexWeb.scope_context(params, "Dependencies"),
        }
        if file_path:
            # Breadcrumb: ... / Dependencies (link to the unfiltered view) / file
            context["file_path"] = file_path
            context["page_url"] = f"/dependencies/{id}" if id else "/dependencies/"
            context["sub_page"] = file_path

        return templates.TemplateResponse(request, "dependencies.html", context)
    except HTTPException:
        # A deliberate 4xx must not be swallowed by the handler below and
        # re-raised as a 500 -- the request was bad, not the server.
        raise
    except Exception as e:
        logger.error(f"Error in dependencies endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/commit/{repo_id}/{commit_hash}", response_class=HTMLResponse)
def commit(request: Request, repo_id: str, commit_hash: str):
    """Display individual commit information"""
    try:
        logger.info(f"Commit page requested for repo: {repo_id}, hash: {commit_hash}")

        _require_known_scope({"repo_id": repo_id}, repo_id)

        data = KospexQuery().commit(repo_id=repo_id, commit_hash=commit_hash)
        if data is None:
            raise HTTPException(
                status_code=404,
                detail=f"No commit '{commit_hash}' in repository '{repo_id}' in the kospex database.",
            )

        files = KospexQuery().commit_files(repo_id=repo_id, commit_hash=commit_hash)

        return templates.TemplateResponse(
            request, "commit.html",
            {
                "commit": data,
                "files": files,
            },
        )
    except HTTPException:
        # Re-raise the route's own 4xx. The handler below converts any
        # Exception to a 500, and HTTPException is an Exception, so a
        # deliberate 404/400 was reported as a server error. (#210)
        raise
    except Exception as e:
        logger.error(f"Error in commit endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


# Human-readable labels for the failure `resolution` categories a dependency
# item can carry when deps.dev could not resolve it. Kept in sync with the
# `_labels` map used on the /dependencies/ page (src/templates/dependencies.html).
UNRESOLVED_STATUS_LABELS = {
    "unresolved_spec": "Unresolved spec",
    "version_yanked": "Yanked",
    "no_satisfying_version": "No matching version",
    "range_not_evaluated": "Range not evaluated",
    "package_not_found": "Not found",
    "no_version": "No version",
    "lookup_error": "Lookup error",
    # Parsed with enrichment off (KOSPEX_DEPS_ENRICH=false / -no-enrich).
    "not_checked": "Not checked",
    # Name matches KOSPEX_DEPS_PRIVATE / GOPRIVATE: never looked up.
    "private_skipped": "Private",
}


def _classify_upload_status(item: dict) -> str:
    """Classify a single package-check result item into a status string.

    Precedence: advisories (Vulnerable) wins over everything else - a package
    that is both vulnerable and unresolved is most usefully flagged as
    Vulnerable. Then an honest label for unresolved/failed-resolution items,
    then the existing numeric versions_behind tiers. `versions_behind` and
    `advisories` are coalesced to 0 so an explicit None (used for unresolved
    dependencies) never reaches a numeric comparison.
    """
    advisories = item.get("advisories") or 0
    versions_behind = item.get("versions_behind") or 0

    if advisories > 0:
        return "Vulnerable"

    resolution = item.get("resolution")
    if resolution in UNRESOLVED_STATUS_LABELS:
        return UNRESOLVED_STATUS_LABELS[resolution]

    if versions_behind > 6:
        return "Outdated"
    elif versions_behind > 2:
        return "Behind"
    return "Current"


# Package check is disabled: unused, not linked from any page, and due to be
# refactored. Kept commented out (with templates/package_check.html and
# _classify_upload_status above) rather than deleted.
# @app.get("/package-check/", response_class=HTMLResponse)
# def package_check(request: Request):
#     """Display the package check page with drag and drop interface"""
#     try:
#         logger.info("Package check page requested")
#
#         return templates.TemplateResponse(request, "package_check.html")
#     except HTTPException:
#         # Re-raise the route's own 4xx. The handler below converts any
#         # Exception to a 500, and HTTPException is an Exception, so a
#         # deliberate 404/400 was reported as a server error. (#210)
#         raise
#     except Exception as e:
#         logger.error(f"Error in package_check endpoint: {e}")
#         raise HTTPException(status_code=500, detail="Internal server error")
#
#
# @app.post("/package-check/upload", response_class=JSONResponse)
# def package_check_upload(file: UploadFile = File(...)):
#     """Handle file upload and analyze dependencies"""
#     try:
#         import os
#         import tempfile
#
#         from kospex.extractors.registry import classify
#
#         logger.info(f"Package upload requested for file: {file.filename}")
#
#         if not file.filename:
#             raise HTTPException(status_code=400, detail="No file selected")
#
#         # Strip any directory components from the client-supplied filename before
#         # joining it onto our temp dir. os.path.join(dir, "/abs") discards dir and
#         # a "../" name traverses out, so the raw name is a path-traversal sink
#         # (CWE-22). basename keeps the meaningful part — assess() dispatches the
#         # parser off it (requirements.txt / package.json / pom.xml / ...).
#         safe_name = os.path.basename(file.filename)
#         if not safe_name or safe_name in (".", ".."):
#             raise HTTPException(status_code=400, detail="Invalid filename")
#
#         # assess() picks the parser from the filename and returns None for one
#         # it doesn't support, which used to surface as a 500 ("'NoneType'
#         # object is not iterable"). Check up front, the same way assess() does.
#         classification = classify(safe_name)
#         if classification.extractor is None or not classification.supported:
#             raise HTTPException(
#                 status_code=400,
#                 detail=f"Unsupported file: {safe_name}. Upload a dependency file "
#                        "kospex can read, such as requirements.txt or package.json.",
#             )
#
#         # Save the uploaded file temporarily
#         temp_dir = tempfile.mkdtemp()
#         temp_path = os.path.join(temp_dir, safe_name)
#
#         # Belt-and-braces: assert the destination stays under the temp dir, the
#         # same containment check clone_repo() uses for clone destinations.
#         if not Path(temp_path).resolve().is_relative_to(Path(temp_dir).resolve()):
#             raise HTTPException(status_code=400, detail="Invalid filename")
#
#         # Read and save file content
#         content = file.file.read()
#         with open(temp_path, "wb") as f:
#             f.write(content)
#
#         try:
#             # Analyze the file using KospexDependencies
#             kospex = Kospex()
#             results = kospex.dependencies.assess(temp_path, quiet=True)
#
#             # Clean up the temporary file
#             os.remove(temp_path)
#             os.rmdir(temp_dir)
#
#             # The name is supported, so None means the parser couldn't read
#             # the file. assess() has already logged why.
#             if results is None:
#                 raise HTTPException(
#                     status_code=422,
#                     detail=f"Could not read {safe_name}. Check the file is "
#                            "complete and in the expected format.",
#                 )
#
#             # Add status based on advisories, resolution and versions behind
#             for item in results:
#                 item["status"] = _classify_upload_status(item)
#
#             return JSONResponse(content=results)
#
#         except HTTPException:
#             # Re-raise the route's own 4xx. The handler below converts any
#             # Exception to a 500, and HTTPException is an Exception, so a
#             # deliberate 404/400 was reported as a server error. (#210)
#             raise
#         except Exception as e:
#             # Clean up in case of error
#             if os.path.exists(temp_path):
#                 os.remove(temp_path)
#             if os.path.exists(temp_dir):
#                 os.rmdir(temp_dir)
#             # Log the exception, but don't send its text to the client: it can
#             # carry internal paths and implementation detail (CWE-209).
#             logger.error(f"Error processing file: {e}")
#             raise HTTPException(status_code=500, detail="Error analyzing file")
#
#     except HTTPException:
#         raise
#     except Exception as e:
#         logger.error(f"Error in package_check_upload endpoint: {e}")
#         raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/hotspots/{repo_id}", response_class=HTMLResponse)
def hotspots(request: Request, repo_id: str):
    """Display code hotspots analysis for a repository"""
    try:
        logger.info(f"Hotspots page requested for repo: {repo_id}")
        _require_known_scope({"repo_id": repo_id}, repo_id)

        data = KospexQuery().hotspots(repo_id=repo_id)

        return templates.TemplateResponse(request, "hotspots.html", {"data": data})
    except HTTPException:
        # Re-raise the route's own 4xx. The handler below converts any
        # Exception to a 500, and HTTPException is an Exception, so a
        # deliberate 404/400 was reported as a server error. (#210)
        raise
    except Exception as e:
        logger.error(f"Error in hotspots endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/files/repo/", response_class=HTMLResponse)
@app.get("/files/repo/{repo_id}", response_class=HTMLResponse)
def repo_files(request: Request, repo_id: Optional[str] = None):
    """Show file metadata for a repository"""
    try:
        logger.info(f"Repository files page requested for repo: {repo_id}")
        _require_known_scope({"repo_id": repo_id}, repo_id)

        data = None
        if repo_id:
            data = KospexQuery().repo_files(repo_id=repo_id)

        return templates.TemplateResponse(request, "files.html", {"data": data})
    except HTTPException:
        # Re-raise the route's own 4xx. The handler below converts any
        # Exception to a 500, and HTTPException is an Exception, so a
        # deliberate 404/400 was reported as a server error. (#210)
        raise
    except Exception as e:
        logger.error(f"Error in repo_files endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/supply-chain/", response_class=HTMLResponse)
def supply_chain(request: Request):
    """
    Display supply chain analysis - either a search form or visualization.
    If no package parameter is provided, shows a search form.
    If package parameter exists, displays bubble chart of dependencies with security status.
    Color coding:
    - Green: No advisories/malware and 0-2 versions behind
    - Yellow: No advisories/malware and 2-6 versions behind
    - Orange: Has advisories or older than 12 months
    - Red: Has malware or older than 2 years
    """
    try:
        import json

        logger.info("Supply chain page requested")

        package = request.query_params.get("package")

        # If no package parameter, show the search form
        if not package:
            logger.info("Showing supply chain search form")
            return templates.TemplateResponse(request, "supply_chain_search.html")

        # Package parameter exists, show visualization
        logger.info(f"Supply chain visualization requested for package: {package}")
        data = None

        # Parse package parameter
        ecosystem = package_name = package_version = None
        try:
            parts = package.split(":")
            if len(parts) != 3:
                raise ValueError("Package must be in format ecosystem:package:version")

            ecosystem, package_name, package_version = parts

            if not all([ecosystem.strip(), package_name.strip(), package_version.strip()]):
                raise ValueError(
                    "All package components (ecosystem, name, version) must be provided"
                )

        except ValueError as e:
            logger.warning(f"Invalid package format: {package} - {e}")
            # Return to search form with error and prefilled data
            return templates.TemplateResponse(
                request, "supply_chain_search.html",
                {
                    "error": f"Invalid package format. {str(e)}",
                    "ecosystem": ecosystem if ecosystem else "",
                    "package_name": package_name if package_name else "",
                    "package_version": package_version if package_version else "",
                },
            )

        # A private name (KOSPEX_DEPS_PRIVATE, GOPRIVATE) is never looked up.
        try:
            permitted, reason = KospexDependencies.lookup_permitted(
                ecosystem.strip(), package_name.strip())
        except ValueError as e:
            # An invalid KOSPEX_DEPS_PRIVATE: say so, not a generic server error.
            return templates.TemplateResponse(
                request, "supply_chain_search.html",
                {"error": str(e), "ecosystem": ecosystem,
                 "package_name": package_name, "package_version": package_version},
            )
        if not permitted:
            return templates.TemplateResponse(
                request, "supply_chain_search.html",
                {
                    "error": f"'{ecosystem}:{package_name}' is private ({reason}), "
                             "so it is never looked up.",
                    "ecosystem": ecosystem,
                    "package_name": package_name,
                    "package_version": package_version,
                },
            )

        # Try to get dependency data
        data = None
        try:
            kospex = Kospex()
            data = kospex.dependencies.package_dependencies(
                package=package_name.strip(),
                version=package_version.strip(),
                ecosystem=ecosystem.strip(),
            )
            logger.info(
                f"Retrieved data for {package}: {json.dumps(data, indent=3) if data else 'No data'}"
            )

        except Exception as e:
            logger.error(f"Error retrieving package dependencies for {package}: {e}")
            # Return to search form with error and prefilled data
            return templates.TemplateResponse(
                request, "supply_chain_search.html",
                {
                    "error": f"Error retrieving package data: {str(e)}. Please check the package details and try again.",
                    "ecosystem": ecosystem,
                    "package_name": package_name,
                    "package_version": package_version,
                },
            )

        # Check if data was found
        if data is None or (isinstance(data, dict) and not data.get("nodes")):
            logger.warning(f"No dependency data found for package: {package}")
            # Return to search form with "not found" error and prefilled data
            return templates.TemplateResponse(
                request, "supply_chain_search.html",
                {
                    "error": f"Package '{ecosystem}:{package_name}:{package_version}' not found. Please check the package details and try again.",
                    "ecosystem": ecosystem,
                    "package_name": package_name,
                    "package_version": package_version,
                },
            )

        # Ensure all nodes have the ecosystem property set
        if data and "nodes" in data:
            for node in data["nodes"]:
                if "ecosystem" not in node or not node["ecosystem"]:
                    node["ecosystem"] = ecosystem.strip()

        logger.info(f"Added ecosystem '{ecosystem}' to {len(data.get('nodes', []))} nodes")

        return templates.TemplateResponse(
            request, "supply_chain.html",
            {
                "data": data,
                "package": package,
                "ecosystem": ecosystem,
            },
        )
    except HTTPException:
        # Re-raise the route's own 4xx. The handler below converts any
        # Exception to a 500, and HTTPException is an Exception, so a
        # deliberate 404/400 was reported as a server error. (#210)
        raise
    except Exception as e:
        logger.error(f"Error in supply_chain endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


# Catch-all route for unmatched paths (must be last)
@app.get("/{full_path:path}", response_class=HTMLResponse)
def catch_all(request: Request, full_path: str):
    """Catch-all route that serves 404 for unmatched paths"""
    logger.warning(f"404 - Path not found: {full_path}")
    raise HTTPException(status_code=404, detail="Page not found")


def main():
    import uvicorn

    # Default values
    host = "127.0.0.1"
    port = 8000
    reload = False

    # Parse command line arguments
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        arg = args[i]

        if arg == "--host":
            if i + 1 < len(args):
                host = args[i + 1]
                i += 2
            else:
                sys.exit("Error: --host requires a value")
        elif arg == "--port":
            if i + 1 < len(args):
                try:
                    port = int(args[i + 1])
                except ValueError:
                    sys.exit("Error: --port must be a valid integer")
                i += 2
            else:
                sys.exit("Error: --port requires a value")
        elif arg == "-debug" or arg == "--debug":
            reload = True
            i += 1
        elif arg == "--help" or arg == "-h":
            print("Usage: kweb2.py [OPTIONS]")
            print("Options:")
            print("  --host HOST     Host to bind to (default: 127.0.0.1)")
            print("  --port PORT     Port to listen on (default: 8000)")
            print("  --debug         Enable debug mode with auto-reload")
            print("  --help, -h      Show this help message")
            sys.exit(0)
        else:
            sys.exit(f"Error: Unknown argument '{arg}'. Use --help for usage information.")

    # Check if running in Docker environment
    in_docker = os.path.exists("/.dockerenv") or os.environ.get("DOCKER_CONTAINER") == "true"

    # Default to 0.0.0.0 if in Docker and host not explicitly set
    if in_docker and host == "127.0.0.1":
        host = "0.0.0.0"
        print("Running in Docker environment, binding to all interfaces")

    print(f"Starting Kospex web server on {host}:{port}")
    if reload:
        print("Debug mode enabled with auto-reload")

    # Watch only kweb's own source dir. uvicorn defaults to the cwd, and without
    # watchfiles it polls every *.py under it: from the repo root that is .venv
    # and .claude/worktrees too, ~5000 files rescanned every 0.25s, and an edit
    # in any worktree (or a pip upgrade) restarts kweb and empties request_cache.
    reload_dirs = [str(Path(__file__).parent)] if reload else None
    uvicorn.run("kweb2:app", host=host, port=port, reload=reload, reload_dirs=reload_dirs)


if __name__ == "__main__":
    main()
