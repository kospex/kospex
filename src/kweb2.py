#!/usr/bin/env python3
"""FastAPI Kospex web server."""

import csv
import sys
import os

from io import StringIO
from typing import Optional
from pathlib import Path

from fastapi import FastAPI, Request, HTTPException, UploadFile, File
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from kospex_query import KospexQuery
import kospex_web as KospexWeb
import kospex_utils as KospexUtils
import kospex_stats as KospexStats
from kweb_help_service import HelpService
from kweb_graph_service import GraphService
from kospex_core import Kospex
from api_routes import router as api_router

# Initialize Kospex environment
KospexUtils.init(create_directories=True, setup_logging=True, verbose=False)

# Set up logging using centralized system
logger = KospexUtils.get_kospex_logger('kweb2')

# Initialize FastAPI app
app = FastAPI(
    title="Kospex Web",
    description="Kospex Code and Developer analytics platform",
    version=Kospex.VERSION
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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


def download_csv_fastapi(dict_data, filename=None):
    """FastAPI-compatible CSV download function"""
    if not dict_data:
        raise HTTPException(status_code=400, detail="No data to download")

    # Create CSV content
    with StringIO() as output:
        # Use the keys of the first dictionary for the header
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
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


# Custom 404 handler
@app.exception_handler(404)
async def custom_404_handler(request: Request, exc: HTTPException):
    """Custom 404 error handler that serves the 404.html template"""
    try:
        return templates.TemplateResponse(
            "404.html",
            {"request": request},
            status_code=404
        )
    except Exception as e:
        # Fallback to basic 404 if template fails
        logger.error(f"Error rendering 404 template: {e}")
        return HTMLResponse(
            content="<h1>404 - Page Not Found</h1>",
            status_code=404
        )


# Custom exception handler for general HTTP exceptions
@app.exception_handler(StarletteHTTPException)
async def custom_http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Handle HTTP exceptions with custom error pages"""
    if exc.status_code == 404:
        return await custom_404_handler(request, exc)

    # For other status codes, use default handler
    return await http_exception_handler(request, exc)


@app.get("/", response_class=HTMLResponse)
@app.get("/summary/", response_class=HTMLResponse)
@app.get("/summary/{id}", response_class=HTMLResponse)
async def summary(request: Request, id: Optional[str] = None):
    """Serve up the summary home page"""
    try:
        logger.info(f"Summary page requested with id: {id}")

        params = KospexWeb.get_id_params(id)
        devs = KospexQuery().developers(**params)

        dev_stats = KospexUtils.count_key_occurrences(devs, "status")
        dev_percentages = KospexUtils.convert_to_percentage(dev_stats)

        total = sum(dev_stats.values())
        dev_stats["total"] = total

        result = {}
        for name, percentage in dev_percentages.items():
            if percentage:
                dev_stats[f"{name}_percentage"] = round(percentage)
            result[name] = round(100 * (percentage / 100)) + 40

        repos = KospexQuery().repos(**params)
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
            "summary.html",
            {
                "request": request,
                "developers": dev_stats,
                "data_size": result,
                "repos": repo_stats,
                "repo_sizes": repo_sizes,
                "id": params
            }
        )
    except Exception as e:
        logger.error(f"Error in summary endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/help/", response_class=HTMLResponse)
@app.get("/help/{id}", response_class=HTMLResponse)
async def help_page(request: Request, id: Optional[str] = None):
    """Serve up the help pages"""
    try:
        logger.info(f"Help page requested with id: {id}")

        if id is None:
            return templates.TemplateResponse(
                "help/index.html",
                {"request": request}
            )
        else:
            # Try to serve the specific help page
            try:
                return templates.TemplateResponse(
                    f"help/{id}.html",
                    {"request": request}
                )
            except Exception:
                # If specific page doesn't exist, fall back to index
                logger.warning(f"Help page help/{id}.html not found, falling back to index")
                return templates.TemplateResponse(
                    "help/index.html",
                    {"request": request}
                )
    except Exception as e:
        logger.error(f"Error in help endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/developers/active/{repo_id}", response_class=HTMLResponse)
async def active_developers(request: Request, repo_id: str):
    """Developer info page."""
    try:
        logger.info(f"Active developers page requested for repo: {repo_id}")

        data = KospexQuery().summary(days=90, repo_id=repo_id)
        results = KospexQuery().active_devs_by_repo(repo_id)

        return templates.TemplateResponse(
            "developers.html",
            {
                "request": request,
                "data": data,
                "authors": results
            }
        )
    except Exception as e:
        logger.error(f"Error in active_developers endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


# Health check endpoint
@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "service": "kospex-web"}

@app.get("/generate-repo-id/")
async def generate_repo_id(url: str):
    """Generate a repo_id from a git URL"""
    try:
        logger.info(f"Generate repo_id requested for URL: {url}")

        # TODO: Implement repo_id generation logic
        # For now, return a stub response
        repo_id = "TODO_IMPLEMENT_REPO_ID_GENERATION"

        return JSONResponse(content={
            "url": url,
            "repo_id": repo_id
        })

    except Exception as e:
        logger.error(f"Error in generate_repo_id endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/servers/", response_class=HTMLResponse)
async def servers(request: Request):
    """Display Git server information."""
    try:
        logger.info("Servers page requested")

        kquery = KospexQuery()
        data = kquery.server_summary()

        return templates.TemplateResponse(
            "servers.html",
            {
                "request": request,
                "data": data
            }
        )
    except Exception as e:
        logger.error(f"Error in servers endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/metadata/", response_class=HTMLResponse)
async def metadata(request: Request):
    """Metadata about the kospex DB and repos."""
    try:
        logger.info("Metadata page requested")

        data = KospexQuery().summary()

        return templates.TemplateResponse(
            "metadata.html",
            {
                "request": request,
                **data
            }
        )
    except Exception as e:
        logger.error(f"Error in metadata endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/metadata/repos/", response_class=HTMLResponse)
@app.get("/metadata/repos/{id}", response_class=HTMLResponse)
async def metadata_repos(request: Request, id: Optional[str] = None):
    """
    Display repository metadata information based on git commits and sync.
    """
    try:
        logger.info(f"Metadata repos page requested with id: {id}")

        kquery = KospexQuery()
        repos = kquery.get_repos()

        #import pprint as pp
        #pp.pprint(repos)

        # If id is provided, filter to specific repo
        if id:
            repos = [repo for repo in repos if repo.get('_repo_id') == id]

        return templates.TemplateResponse(
            "metadata_repos.html",
            {
                "request": request,
                "repos": repos,
                "repo_id": id
            }
        )
    except Exception as e:
        logger.error(f"Error in metadata repos endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/orphans/", response_class=HTMLResponse)
@app.get("/orphans/{id}", response_class=HTMLResponse)
async def orphans(request: Request, id: Optional[str] = None):
    """Display orphan information"""
    try:
        logger.info(f"Orphans page requested with id: {id}")

        params = KospexWeb.get_id_params(id)
        data = KospexQuery().get_orphans(id=params)

        return templates.TemplateResponse(
            "orphans.html",
            {
                "request": request,
                "data": data
            }
        )
    except Exception as e:
        logger.error(f"Error in orphans endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/bubble/{id}", response_class=HTMLResponse)
@app.get("/treemap/{id}", response_class=HTMLResponse)
async def bubble_treemap(request: Request, id: str):
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

        link_url = ""

        if KospexUtils.parse_repo_id(id):
            link_url = f"repo/{id}"
        elif KospexUtils.is_base64(id):
            link_url = f"dev/{id}"
        else:
            link_url = f"{id}"

        html_template = f"{template}.html"

        return templates.TemplateResponse(
            html_template,
            {
                "request": request,
                "link_url": link_url,
                "template": template,
                "id": id
            }
        )
    except Exception as e:
        logger.error(f"Error in bubble/treemap endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/osi/", response_class=HTMLResponse)
@app.get("/osi/{id}", response_class=HTMLResponse)
async def osi(request: Request, id: Optional[str] = None):
    """Functions around an Open Source Inventory"""
    try:
        logger.info(f"OSI page requested with id: {id}")

        params = KospexWeb.get_id_params(id)
        deps = KospexQuery().get_dependency_files(request_id=params)

        for file in deps:
            file["days_ago"] = KospexUtils.days_ago(file.get("committer_when"))
            file["status"] = KospexUtils.development_status(file.get("days_ago"))

        file_number = len(deps)
        status = KospexUtils.repo_stats(deps, "committer_when")
        filenames = KospexUtils.filenames_by_repo_id(deps)

        return templates.TemplateResponse(
            "osi.html",
            {
                "request": request,
                "data": deps,
                "file_number": file_number,
                "dep_files": filenames,
                "status": status
            }
        )
    except Exception as e:
        logger.error(f"Error in osi endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/collab/{repo_id}", response_class=HTMLResponse)
async def collab(request: Request, repo_id: str):
    """Display repository collaboration information"""
    try:
        logger.info(f"Collaboration page requested for repo: {repo_id}")

        kquery = KospexQuery()

        collabs = kquery.get_collabs(repo_id=repo_id)

        return templates.TemplateResponse(
            "collab.html",
            {
                "request": request,
                "repo_id": repo_id,
                "collabs": collabs
            }
        )
    except Exception as e:
        logger.error(f"Error in collab endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/collab/graph/{repo_id}", response_class=HTMLResponse)
async def collab_graph(request: Request, repo_id: str):
    """Display network graph visualization of repository collaboration"""
    try:
        logger.info(f"Collaboration graph page requested for repo: {repo_id}")

        return templates.TemplateResponse(
            "collab_graph.html",
            {
                "request": request,
                "repo_id": repo_id
            }
        )
    except Exception as e:
        logger.error(f"Error in collab_graph endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/api/collab/graph/{repo_id}", response_class=JSONResponse)
async def collab_graph_data(request: Request, repo_id: str):
    """Return JSON data for collaboration network graph"""
    try:
        logger.info(f"Collaboration graph data requested for repo: {repo_id}")

        kquery = KospexQuery()
        collabs = kquery.get_collabs(repo_id=repo_id)

        return JSONResponse(content=collabs)
    except Exception as e:
        logger.error(f"Error in collab_graph_data endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/file-collab/{repo_id}/", response_class=HTMLResponse)
async def file_collaboration(request: Request, repo_id: str):
    """Display file collaboration information"""
    try:
        logger.info(f"File collaboration page requested for repo: {repo_id}")

        file_path = request.query_params.get('file_path')

        if not file_path:
            raise HTTPException(status_code=400, detail="file_path parameter is required")

        logger.info(f"File collaboration requested for: {file_path}")

        kquery = KospexQuery()
        collaborators = kquery.get_file_collaborators(repo_id=repo_id, file_path=file_path)

        return templates.TemplateResponse(
            "file_collaboration.html",
            {
                "request": request,
                "collaborators": collaborators,
                "repo_id": repo_id,
                "file_path": file_path
            }
        )
    except Exception as e:
        logger.error(f"Error in file_collaboration endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/orgs/", response_class=HTMLResponse)
@app.get("/orgs/{server}", response_class=HTMLResponse)
async def orgs(request: Request, server: Optional[str] = None):
    """Display organization information"""
    try:
        logger.info(f"Organizations page requested with server: {server}")

        org = request.query_params.get('org')
        params = KospexWeb.get_id_params(server)

        kospex = KospexQuery()
        git_orgs = kospex.orgs()
        active_devs = kospex.active_devs(org=True)

        for row in git_orgs:
            row['active_devs'] = active_devs.get(row['org_key'], 0)

        return templates.TemplateResponse(
            "orgs.html",
            {
                "request": request,
                "data": git_orgs
            }
        )
    except Exception as e:
        logger.error(f"Error in orgs endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/recent/", response_class=HTMLResponse)
async def recent_syncs(request: Request):
    """Display recently synced repositories"""
    try:
        logger.info("Recent syncs view requested")
        kospex = KospexQuery()

        # TODO: Implement proper query for last 10 syncs
        # This is stubbed for now - will need to add database query for recent syncs
        data = []  # Placeholder for recent sync data

        return templates.TemplateResponse("recent-syncs.html", {
            "request": request,
            "data": data,
            "page": {"title": "Recent Syncs"}
        })
    except Exception as e:
        logger.error(f"Error in recent syncs endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/repos/", response_class=HTMLResponse)
@app.get("/repos/{id}", response_class=HTMLResponse)
async def repos(request: Request, id: Optional[str] = None):
    """Display repository information"""
    try:
        logger.info(f"Repositories page requested with id: {id}")

        params = KospexWeb.get_id_params(id)
        repo_id = request.query_params.get('repo_id') or params.get("repo_id")
        org_key = request.query_params.get('org_key') or params.get("org_key")
        server = request.query_params.get('server') or params.get("server")

        kospex = KospexQuery()

        page = {}
        # TODO - validate params
        techs = None
        # Maintenance ranges
        ranges = None

        page['repo_id'] = repo_id

        data = []

        all_repos = kospex.get_repos()
        repo_lookup = {d["_repo_id"]: d for d in all_repos}

        if org_key:
            parts = org_key.split("~")
            if len(parts) == 2:
                page['git_server'] = parts[0]
                page['git_owner'] = parts[1]
                techs = kospex.tech_landscape(org_key=org_key)
                ranges = kospex.commit_ranges2(org_key=org_key)
        elif server:
            page['git_server'] = server

        # The repos method handles null values for parameters
        data = kospex.repos(org_key=org_key, server=server)
        active_devs = kospex.active_devs()
        for row in data:
            row['active_devs'] = active_devs.get(row['_repo_id'], 0)

        developers = kospex.developers(org_key=org_key, server=server)
        developer_status = KospexUtils.repo_stats(developers, "last_commit")

        for repo in data:
            if r := repo_lookup.get(repo.get("_repo_id")):
                repo['years_active'] = r.get("years_active")

        return templates.TemplateResponse(
            "repos.html",
            {
                "request": request,
                "data": data,
                "page": page,
                "ranges": ranges,
                "techs": techs,
                "developer_status": developer_status
            }
        )
    except Exception as e:
        logger.error(f"Error in repos endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/repo/{repo_id}", response_class=HTMLResponse)
async def repo(request: Request, repo_id: str):
    """Display individual repository information"""
    try:
        logger.info(f"Repository view requested for repo: {repo_id}")

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
            labels.append(tech['Language'])
            datapoints.append(tech['count'])
            count += 1
            if count > 10:
                break

        return templates.TemplateResponse(
            "repo_view.html",
            {
                "request": request,
                "repo_id": repo_id,
                "ranges": commit_ranges,
                "email_domains": email_domains,
                "landscape": techs,
                "developer_status": developer_status,
                "labels": labels,
                "datapoints": datapoints,
                "summary": summary
            }
        )
    except Exception as e:
        logger.error(f"Error in repo endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/key-person/{repo_id}", response_class=HTMLResponse)
async def key_person(request: Request, repo_id: str):
    """Display key person analysis for a repository"""
    try:
        logger.info(f"Key person view requested for repo: {repo_id}")

        kospex = KospexQuery()

        # Get commits summary data (same as repo view)
        commit_ranges = kospex.commit_ranges(repo_id)

        # Get developer status (same as repo view)
        developers = kospex.developers(repo_id=repo_id)
        developer_status = KospexUtils.repo_stats(developers, "last_commit")

        # Get key person data
        key_people = kospex.key_person(repo_id=repo_id,top=5)

        return templates.TemplateResponse(
            "key_person.html",
            {
                "request": request,
                "repo_id": repo_id,
                "ranges": commit_ranges,
                "developer_status": developer_status,
                "key_people": key_people
            }
        )
    except Exception as e:
        logger.error(f"Error in key_person endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/landscape/", response_class=HTMLResponse)
@app.get("/landscape/{id}", response_class=HTMLResponse)
async def landscape(request: Request, id: Optional[str] = None):
    """Serve up the technology landscape metadata"""
    try:
        logger.info(f"Technology landscape page requested with id: {id}")

        kospex = KospexQuery()

        params = KospexWeb.get_id_params(id)
        repo_id = request.query_params.get('repo_id') or params.get("repo_id")
        org_key = request.query_params.get('org_key') or params.get("org_key")
        data = kospex.tech_landscape(org_key=org_key, repo_id=repo_id)

        download = request.query_params.get('download')

        if download:
            # Download tech landscape data as CSV
            return download_csv_fastapi(data, "tech_landscape.csv")
        else:
            return templates.TemplateResponse(
                "landscape.html",
                {
                    "request": request,
                    "data": data,
                    "org_key": org_key,
                    "id": id
                }
            )
    except Exception as e:
        logger.error(f"Error in landscape endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/developers/", response_class=HTMLResponse)
async def developers(request: Request):
    """Developer info page"""
    try:
        logger.info("Developers page requested")

        author_email = request.query_params.get('author_email')
        download = request.query_params.get('download')
        days = request.query_params.get('days')
        org_key = request.query_params.get('org_key')
        debug = locals()
        logger.info(debug)


        devs = KospexQuery().authors(days=days, org_key=org_key)

        if author_email:
            logger.info(f"Developer view requested for: {author_email}")
            # Github uses +, which get interpreted as a " " in the URL.
            author_email = author_email.replace(" ", "+")
            repo_list = KospexQuery().repos_by_author(author_email)
            techs = KospexQuery().author_tech(author_email=author_email)
            labels = []
            datapoints = []

            github_handle = KospexUtils.extract_github_username(author_email)

            count = 0
            for tech in techs:
                labels.append(tech['_ext'])
                datapoints.append(tech['commits'])
                count += 1
                if count > 10:
                    break

            return templates.TemplateResponse(
                "developer_view.html",
                {
                    "request": request,
                    "repos": repo_list,
                    "tech": techs,
                    "author_email": author_email,
                    "labels": labels,
                    "github_handle": github_handle,
                    "datapoints": datapoints
                }
            )

        elif download:
            return download_csv_fastapi(devs, "developers.csv")
        else:
            data = KospexQuery().summary(days=days,org_key=org_key)
            return templates.TemplateResponse(
                "developers.html",
                {
                    "request": request,
                    "authors": devs,
                    "data": data
                }
            )
    except Exception as e:
        logger.error(f"Error in developers endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/graph/", response_class=HTMLResponse)
@app.get("/graph/{org_key}", response_class=HTMLResponse)
async def graph(request: Request, org_key: Optional[str] = None):
    """Force directed graphs for data in the Kospex DB."""
    try:
        logger.info(f"Graph page requested with org_key: {org_key}")
        focus = None

        author_email = request.query_params.get('author_email')
        if author_email:
            # This is a weird old skool http thing
            # where spaces were represented by + signs
            author_email = author_email.replace(" ", "+")
        repo_id = request.query_params.get('repo_id')

        if repo_id:
            org_key = f"?repo_id={repo_id}"
            focus = "files"
        elif author_email:
            org_key = f"?author_email={author_email}"

        return templates.TemplateResponse(
            "graph.html",
            {
                "request": request,
                "org_key": org_key,
                "focus": focus
            }
        )
    except Exception as e:
        logger.error(f"Error in graph endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/org-graph/", response_class=JSONResponse)
@app.get("/org-graph/{org_key}", response_class=JSONResponse)
@app.get("/org-graph/{focus}/{org_key}", response_class=JSONResponse)
async def org_graph(request: Request, org_key: Optional[str] = None, focus: Optional[str] = None):
    """Return JSON data for the force directed graph."""
    try:
        logger.info(f"Org graph data requested - focus: {focus}, org_key: {org_key}")

        return graph_service.get_graph_data(focus, org_key, dict(request.query_params))
    except Exception as e:
        logger.error(f"Error in org_graph endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/tenure/", response_class=HTMLResponse)
@app.get("/tenure/{id}", response_class=HTMLResponse)
async def tenure(request: Request, id: Optional[str] = None):
    """
    View developer tenure for all, a server, org or a repo
    """
    try:

        logger.info(f"Tenure page requested with id: {id}")

        # TODO - CHECK THIS FOR SECURITY
        params = KospexWeb.get_id_params(id)
        developers = KospexQuery().developers(**params)
        active_devs = []
        dev_leavers = []

        for entry in developers:
            entry['tenure_status'] = KospexUtils.get_status(entry['tenure'])

        for dev in developers:
            if "Active" == dev.get("status"):
                active_devs.append(dev)
            else:
                if KospexUtils.days_ago(dev['last_commit']) < 365:
                    dev_leavers.append(dev)

        #Calculate developers who've left and the distribugion
        for entry in dev_leavers:
            entry['tenure_status'] = KospexUtils.get_status(entry['tenure'])

        leavers = KospexUtils.get_status_distribution(dev_leavers)

        data = {}

        data["leavers"] = len(dev_leavers)


        data['developers'] = len(developers)
        data['active_devs'] = len(active_devs)

        days_values = [entry['tenure'] for entry in developers]
        active_days_values = [entry['tenure'] for entry in active_devs]

        commit_stats = KospexQuery().get_activity_stats(params)
        data['days_active'] = commit_stats.get('days_active')
        data['years_active'] = commit_stats.get('years_active')
        data['repos'] = commit_stats.get('repos')
        data['commits'] = commit_stats.get('commits')

        data |= KospexStats.tenure_stats(days_values)

        # Holds the active stats
        active_data = KospexStats.tenure_stats(active_days_values)

        distribution = KospexUtils.get_status_distribution(developers)
        active_d = KospexUtils.get_status_distribution(active_devs)

        return templates.TemplateResponse(
            "tenure.html",
            {
                "request": request,
                "data": data,
                "active_data": active_data,
                "distribution": distribution,
                "developers": developers,
                "active_distribution": active_d,
                "leavers": leavers
            }
        )
    except Exception as e:
        logger.error(f"Error in tenure endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/meta/author-domains", response_class=HTMLResponse)
async def author_domains(request: Request):
    """Display author email domain analysis"""
    try:
        logger.info("Author domains page requested")

        kospex = KospexQuery()
        email_domains = kospex.email_domains()

        return templates.TemplateResponse(
            "meta-author-domains.html",
            {
                "request": request,
                "email_domains": email_domains
            }
        )
    except Exception as e:
        logger.error(f"Error in author_domains endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/tech-change/", response_class=HTMLResponse)
async def tech_change(request: Request):
    """Technology change radar visualization"""
    try:
        logger.info("Tech change radar page requested")

        labels = ["Java", "Go", "JavaScript", "Python", "Kotlin"]

        return templates.TemplateResponse(
            "tech-change.html",
            {
                "request": request,
                "labels": labels
            }
        )
    except Exception as e:
        logger.error(f"Error in tech_change endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/tech/{tech}", response_class=HTMLResponse)
async def repo_with_tech(request: Request, tech: str):
    """Show repositories with the given technology"""
    try:
        logger.info(f"Tech filtering page requested for technology: {tech}")

        repo_id = request.query_params.get('repo_id')
        kospex = KospexQuery()
        template = "repos.html"

        if repo_id:
            repos_with_tech = kospex.repo_files(tech, repo_id=repo_id)
            template = "repo_files.html"
        else:
            repos_with_tech = kospex.repos_with_tech(tech)

        return templates.TemplateResponse(
            template,
            {
                "request": request,
                "data": repos_with_tech,
                "page": {}
            }
        )
    except Exception as e:
        logger.error(f"Error in repo_with_tech endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/developer/", response_class=HTMLResponse)
@app.get("/developer/{id}", response_class=HTMLResponse)
async def developer_view(request: Request, id: Optional[str] = None):
    """
    View individual developer details
    """
    try:
        logger.info(f"Developer view requested with id: {id}")

        author_email = request.query_params.get('author_email')

        if id:
            author_email = KospexUtils.decode_base64(id)

        if author_email is None:
            logger.error("No email passed to Developer view")
            return templates.TemplateResponse(
                "404.html",
                {
                    "request": request,
                    "error": "No email passed to Developer view",
                }
            )

        # Github uses +, which get interpreted as a " " in the URL.
        if author_email:
            author_email = author_email.replace(" ", "+")

        repo_list = KospexQuery().repos_by_author(author_email)
        techs = KospexQuery().author_tech(author_email=author_email)
        labels = []
        datapoints = []

        count = 0
        for tech in techs:
            labels.append(tech['_ext'])
            datapoints.append(tech['commits'])
            count += 1
            if count > 10:
                break

        return templates.TemplateResponse(
            "developer_view.html",
            {
                "request": request,
                "repos": repo_list,
                "tech": techs,
                "author_email": author_email,
                "labels": labels,
                "datapoints": datapoints
            }
        )
    except Exception as e:
        logger.error(f"Error in developer_view endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/observation/{uuid}", response_class=HTMLResponse)
async def observation(request: Request, uuid: str):
    """
    Display observation information
    """
    try:
        logger.info(f"display single observations with uuid: {uuid}")

        kquery = KospexQuery()

        observation = kquery.get_single_observation(uuid=uuid)

        return templates.TemplateResponse(
            "observation.html",
            {
                "request": request,
                "observation": observation,
            }
        )
    except Exception as e:
        logger.error(f"Error in repo_with_tech endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")



@app.get("/observations/", response_class=HTMLResponse)
async def observations(request: Request):
    """Display observation information"""
    try:
        logger.info("Observations page requested")

        kquery = KospexQuery()
        repo_id = request.query_params.get('repo_id')
        observation_key = request.query_params.get('observation_key')

        if observation_key:
            # We should have an observation key and a repo_id for this to work
            logger.info(f"Observation key: {observation_key}")
            return templates.TemplateResponse(
                "observations_repo_key.html",
                {
                    "request": request,
                    "data": kquery.observations_summary(repo_id=repo_id, observation_key=observation_key),
                    "observation_key": observation_key,
                    "repo_id": repo_id
                }
            )

        elif repo_id:
            logger.info(f"Repo ID: {repo_id}")
            return templates.TemplateResponse(
                "observations_repo.html",
                {
                    "request": request,
                    "data": kquery.observations_summary(repo_id=repo_id),
                    "repo_id": repo_id
                }
            )
        else:
            return templates.TemplateResponse(
                "observations.html",
                {
                    "request": request,
                    "data": kquery.observations_summary()
                }
            )
    except Exception as e:
        logger.error(f"Error in observations endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/commits/", response_class=HTMLResponse)
@app.get("/commits/{repo_id}", response_class=HTMLResponse)
async def commits(request: Request, repo_id: Optional[str] = None):
    """
    Display Git commit information
    """
    try:
        logger.info("Commits page requested")
        if repo_id is None:
            # If no repo_id is provided, use the query parameter
            # This allows for filtering by author or committer email
            repo_id = request.query_params.get('repo_id', "")

        author_email = request.query_params.get('author_email')
        committer_email = request.query_params.get('committer_email')

        logger.info(f"Author email: {author_email}")

        data = KospexQuery().commits(
            limit=1000,
            repo_id=repo_id,
            author_email=author_email,
            committer_email=committer_email
        )

        return templates.TemplateResponse(
            "commits.html",
            {
                "request": request,
                "commits": data,
                "repo_id": repo_id
            }
        )
    except Exception as e:
        logger.error(f"Error in commits endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/dependencies/", response_class=HTMLResponse)
@app.get("/dependencies/{id}", response_class=HTMLResponse)
async def dependencies(request: Request, id: Optional[str] = None):
    """Display SCA (Software Composition Analysis) information"""
    try:
        logger.info(f"Dependencies page requested with id: {id}")

        params = KospexWeb.get_id_params(id)
        data = KospexQuery().get_dependencies(request_id=params)

        return templates.TemplateResponse(
            "dependencies.html",
            {
                "request": request,
                "data": data
            }
        )
    except Exception as e:
        logger.error(f"Error in dependencies endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/commit/{repo_id}/{commit_hash}", response_class=HTMLResponse)
async def commit(request: Request, repo_id: str, commit_hash: str):
    """Display individual commit information"""
    try:
        logger.info(f"Commit page requested for repo: {repo_id}, hash: {commit_hash}")

        data = KospexQuery().commit(repo_id=repo_id, commit_hash=commit_hash)

        files = KospexQuery().commit_files(repo_id=repo_id, commit_hash=commit_hash)

        return templates.TemplateResponse(
            "commit.html",
            {
                "request": request,
                "commit": data,
                "files": files,
            }
        )
    except Exception as e:
        logger.error(f"Error in commit endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/package-check/", response_class=HTMLResponse)
async def package_check(request: Request):
    """Display the package check page with drag and drop interface"""
    try:
        logger.info("Package check page requested")

        return templates.TemplateResponse(
            "package_check.html",
            {
                "request": request
            }
        )
    except Exception as e:
        logger.error(f"Error in package_check endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/package-check/upload", response_class=JSONResponse)
async def package_check_upload(file: UploadFile = File(...)):
    """Handle file upload and analyze dependencies"""
    try:
        import tempfile
        import os
        import pprint

        logger.info(f"Package upload requested for file: {file.filename}")

        if not file.filename:
            raise HTTPException(status_code=400, detail="No file selected")

        # Save the uploaded file temporarily
        temp_dir = tempfile.mkdtemp()
        temp_path = os.path.join(temp_dir, file.filename)

        # Read and save file content
        content = await file.read()
        with open(temp_path, 'wb') as f:
            f.write(content)

        try:
            # Analyze the file using KospexDependencies
            kospex = Kospex()
            results = kospex.dependencies.assess(temp_path)
            print(results)

            # Sort results by status
            pprint.PrettyPrinter(indent=4).pprint(results)

            # Clean up the temporary file
            os.remove(temp_path)
            os.rmdir(temp_dir)

            # Add status based on advisories and versions behind
            for item in results:
                if item.get('advisories', 0) > 0:
                    item['status'] = 'Vulnerable'
                elif item.get('versions_behind', 0) > 6:
                    item['status'] = 'Outdated'
                elif item.get('versions_behind', 0) > 2:
                    item['status'] = 'Behind'
                else:
                    item['status'] = 'Current'

            return JSONResponse(content=results)

        except Exception as e:
            # Clean up in case of error
            if os.path.exists(temp_path):
                os.remove(temp_path)
            if os.path.exists(temp_dir):
                os.rmdir(temp_dir)
            logger.error(f"Error processing file: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in package_check_upload endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/hotspots/{repo_id}", response_class=HTMLResponse)
async def hotspots(request: Request, repo_id: str):
    """Display code hotspots analysis for a repository"""
    try:
        logger.info(f"Hotspots page requested for repo: {repo_id}")

        data = KospexQuery().hotspots(repo_id=repo_id)

        return templates.TemplateResponse(
            "hotspots.html",
            {
                "request": request,
                "data": data
            }
        )
    except Exception as e:
        logger.error(f"Error in hotspots endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/files/repo/", response_class=HTMLResponse)
@app.get("/files/repo/{repo_id}", response_class=HTMLResponse)
async def repo_files(request: Request, repo_id: Optional[str] = None):
    """Show file metadata for a repository"""
    try:
        logger.info(f"Repository files page requested for repo: {repo_id}")

        data = None
        if repo_id:
            data = KospexQuery().repo_files(repo_id=repo_id)

        return templates.TemplateResponse(
            "files.html",
            {
                "request": request,
                "data": data
            }
        )
    except Exception as e:
        logger.error(f"Error in repo_files endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/supply-chain/", response_class=HTMLResponse)
async def supply_chain(request: Request):
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

        package = request.query_params.get('package')

        # If no package parameter, show the search form
        if not package:
            logger.info("Showing supply chain search form")
            return templates.TemplateResponse(
                "supply_chain_search.html",
                {
                    "request": request
                }
            )

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
                raise ValueError("All package components (ecosystem, name, version) must be provided")

        except ValueError as e:
            logger.warning(f"Invalid package format: {package} - {e}")
            # Return to search form with error and prefilled data
            return templates.TemplateResponse(
                "supply_chain_search.html",
                {
                    "request": request,
                    "error": f"Invalid package format. {str(e)}",
                    "ecosystem": ecosystem if ecosystem else "",
                    "package_name": package_name if package_name else "",
                    "package_version": package_version if package_version else ""
                }
            )

        # Try to get dependency data
        data = None
        try:
            kospex = Kospex()
            data = kospex.dependencies.package_dependencies(
                package=package_name.strip(),
                version=package_version.strip(),
                ecosystem=ecosystem.strip()
            )
            logger.info(f"Retrieved data for {package}: {json.dumps(data, indent=3) if data else 'No data'}")

        except Exception as e:
            logger.error(f"Error retrieving package dependencies for {package}: {e}")
            # Return to search form with error and prefilled data
            return templates.TemplateResponse(
                "supply_chain_search.html",
                {
                    "request": request,
                    "error": f"Error retrieving package data: {str(e)}. Please check the package details and try again.",
                    "ecosystem": ecosystem,
                    "package_name": package_name,
                    "package_version": package_version
                }
            )

        # Check if data was found
        if data is None or (isinstance(data, dict) and not data.get("nodes")):
            logger.warning(f"No dependency data found for package: {package}")
            # Return to search form with "not found" error and prefilled data
            return templates.TemplateResponse(
                "supply_chain_search.html",
                {
                    "request": request,
                    "error": f"Package '{ecosystem}:{package_name}:{package_version}' not found. Please check the package details and try again.",
                    "ecosystem": ecosystem,
                    "package_name": package_name,
                    "package_version": package_version
                }
            )

        # Ensure all nodes have the ecosystem property set
        if data and "nodes" in data:
            for node in data["nodes"]:
                if "ecosystem" not in node or not node["ecosystem"]:
                    node["ecosystem"] = ecosystem.strip()

        logger.info(f"Added ecosystem '{ecosystem}' to {len(data.get('nodes', []))} nodes")

        return templates.TemplateResponse(
            "supply_chain.html",
            {
                "request": request,
                "data": data,
                "package": package,
                "ecosystem": ecosystem
            }
        )
    except Exception as e:
        logger.error(f"Error in supply_chain endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


# Catch-all route for unmatched paths (must be last)
@app.get("/{full_path:path}", response_class=HTMLResponse)
async def catch_all(request: Request, full_path: str):
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
    in_docker = os.path.exists('/.dockerenv') or os.environ.get('DOCKER_CONTAINER') == 'true'

    # Default to 0.0.0.0 if in Docker and host not explicitly set
    if in_docker and host == '127.0.0.1':
        host = '0.0.0.0'
        print("Running in Docker environment, binding to all interfaces")

    print(f"Starting Kospex web server on {host}:{port}")
    if reload:
        print("Debug mode enabled with auto-reload")

    uvicorn.run("kweb2:app", host=host, port=port, reload=reload)

if __name__ == "__main__":
    main()
