#!/usr/bin/env python3
"""API routes for Kospex web application."""

import logging
from typing import Optional
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse

from kospex_query import KospexQuery
import kospex_web as KospexWeb
import kospex_utils as KospexUtils

logger = logging.getLogger(__name__)

# Create router instance with API prefix
router = APIRouter(prefix="/api", tags=["api"])


@router.get("/servers/", response_class=JSONResponse)
@router.get("/servers/{id}", response_class=JSONResponse)
async def api_servers(request: Request, id: Optional[str] = None):
    """API endpoint for server information"""
    try:
        logger.info(f"API servers endpoint requested with id: {id}")

        kquery = KospexQuery()

        if id:
            # Get specific server data
            data = kquery.server_summary(id=id)
        else:
            # Get all servers
            data = kquery.server_summary()

        return JSONResponse(content={
            "status": "success",
            "data": data,
            "server_id": id
        })
    except Exception as e:
        logger.error(f"Error in api_servers endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/developers/", response_class=JSONResponse)
@router.get("/developers/{id}", response_class=JSONResponse)
async def api_developers(request: Request, id: Optional[str] = None):
    """API endpoint for developers information"""
    try:
        logger.info(f"API developers endpoint requested with id: {id}")

        days = request.query_params.get('days')
        org_key = request.query_params.get('org_key')

        kquery = KospexQuery()

        if id:
            # Get specific developer data using base64 decoded ID
            author_email = KospexUtils.decode_base64(id)
            if author_email:
                author_email = author_email.replace(" ", "+")

            repo_list = kquery.repos_by_author(author_email)
            techs = kquery.author_tech(author_email=author_email)
            github_handle = KospexUtils.extract_github_username(author_email)

            data = {
                "developer_id": id,
                "author_email": author_email,
                "github_handle": github_handle,
                "repositories": repo_list,
                "technologies": techs
            }
        else:
            # Get all developers
            data = kquery.authors(days=days, org_key=org_key)

        return JSONResponse(content={
            "status": "success",
            "data": data,
            "developer_id": id
        })
    except Exception as e:
        logger.error(f"Error in api_developers endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/orgs/", response_class=JSONResponse)
async def api_orgs(request: Request):
    """API endpoint for organizations information"""
    try:
        logger.info("API orgs endpoint requested")

        kquery = KospexQuery()
        git_orgs = kquery.orgs()
        active_devs = kquery.active_devs(org=True)

        # Enhance org data with active developer counts
        for row in git_orgs:
            row['active_devs'] = active_devs.get(row['org_key'], 0)

        return JSONResponse(content={
            "status": "success",
            "data": git_orgs
        })
    except Exception as e:
        logger.error(f"Error in api_orgs endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/repos/", response_class=JSONResponse)
@router.get("/repos/{id}", response_class=JSONResponse)
async def api_repos(request: Request, id: Optional[str] = None):
    """API endpoint for repositories information"""
    try:
        logger.info(f"API repos endpoint requested with id: {id}")

        kquery = KospexQuery()

        if id:
            # Get specific repository data
            repo_id = id
            commit_ranges = kquery.commit_ranges(repo_id)
            email_domains = kquery.email_domains(repo_id=repo_id)
            summary = kquery.author_summary(repo_id)
            techs = kquery.tech_landscape(repo_id=repo_id)
            developers = kquery.developers(repo_id=repo_id)

            data = {
                "repo_id": repo_id,
                "commit_ranges": commit_ranges,
                "email_domains": email_domains,
                "summary": summary,
                "technologies": techs,
                "developers": developers
            }
        else:
            # Get all repositories with filters
            params = KospexWeb.get_id_params(id)
            repo_id = request.query_params.get('repo_id') or params.get("repo_id")
            org_key = request.query_params.get('org_key') or params.get("org_key")
            server = request.query_params.get('server') or params.get("server")

            data = kquery.repos(org_key=org_key, server=server)
            active_devs = kquery.active_devs()

            # Enhance repo data with active developer counts
            for row in data:
                row['active_devs'] = active_devs.get(row['_repo_id'], 0)

        return JSONResponse(content={
            "status": "success",
            "data": data,
            "repo_id": id
        })
    except Exception as e:
        logger.error(f"Error in api_repos endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/developer/{id}", response_class=JSONResponse)
async def api_developer(request: Request, id: str):
    """API endpoint for individual developer information (ID required)"""
    try:
        logger.info(f"API developer endpoint requested with id: {id}")

        # Decode the base64 ID to get author email
        author_email = KospexUtils.decode_base64(id)

        # Handle GitHub + signs in URLs
        if author_email:
            author_email = author_email.replace(" ", "+")

        kquery = KospexQuery()
        repo_list = kquery.repos_by_author(author_email)
        techs = kquery.author_tech(author_email=author_email)

        # Extract GitHub username if possible
        github_handle = KospexUtils.extract_github_username(author_email)

        data = {
            "developer_id": id,
            "author_email": author_email,
            "github_handle": github_handle,
            "repositories": repo_list,
            "technologies": techs
        }

        return JSONResponse(content={
            "status": "success",
            "data": data
        })
    except Exception as e:
        logger.error(f"Error in api_developer endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


# Additional API endpoints for completeness
@router.get("/health", response_class=JSONResponse)
async def api_health():
    """API health check endpoint"""
    return JSONResponse(content={
        "status": "healthy",
        "service": "kospex-api",
        "version": "2.0.0"
    })


@router.get("/summary", response_class=JSONResponse)
async def api_summary(request: Request):
    """API endpoint for summary information"""
    try:
        logger.info("API summary endpoint requested")

        days = request.query_params.get('days')
        org_key = request.query_params.get('org_key')

        kquery = KospexQuery()
        data = kquery.summary(days=days, org_key=org_key)

        return JSONResponse(content={
            "status": "success",
            "data": data
        })
    except Exception as e:
        logger.error(f"Error in api_summary endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/tech-landscape", response_class=JSONResponse)
async def api_tech_landscape(request: Request):
    """API endpoint for technology landscape"""
    try:
        logger.info("API tech landscape endpoint requested")

        repo_id = request.query_params.get('repo_id')
        org_key = request.query_params.get('org_key')

        kquery = KospexQuery()
        data = kquery.tech_landscape(org_key=org_key, repo_id=repo_id)

        return JSONResponse(content={
            "status": "success",
            "data": data,
            "filters": {
                "repo_id": repo_id,
                "org_key": org_key
            }
        })
    except Exception as e:
        logger.error(f"Error in api_tech_landscape endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
