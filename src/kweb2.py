#!/usr/bin/env python3
"""FastAPI implementation of Kospex web server."""

import logging
from typing import Optional
from pathlib import Path

from fastapi import FastAPI, Request, HTTPException
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
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
from kweb_help_service import HelpService

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(
    title="Kospex Web",
    description="Kospex software development analytics platform",
    version="2.0.0"
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


# Catch-all route for unmatched paths (must be last)
@app.get("/{full_path:path}", response_class=HTMLResponse)
async def catch_all(request: Request, full_path: str):
    """Catch-all route that serves 404 for unmatched paths"""
    logger.warning(f"404 - Path not found: {full_path}")
    raise HTTPException(status_code=404, detail="Page not found")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)