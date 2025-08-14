# Kospex MCP Server Options

This document outlines various approaches to integrate kospex as an MCP (Model Context Protocol) server with Claude Code.

## Option 1: Direct FastAPI MCP Server

Create a dedicated MCP server that wraps your existing FastAPI routes:

```python
# kospex_mcp_server.py
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent
import json
import requests
import asyncio

# Assuming kospex web server runs on localhost:8000
KOSPEX_BASE_URL = "http://localhost:8000"

server = Server("kospex")

@server.list_tools()
async def list_tools():
    return [
        Tool(
            name="get_repo_summary",
            description="Get summary statistics for a repository",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_id": {"type": "string", "description": "Repository ID"}
                },
                "required": ["repo_id"]
            }
        ),
        Tool(
            name="get_developers",
            description="Get developer information for repositories",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_id": {"type": "string", "description": "Optional repository ID"},
                    "days": {"type": "integer", "description": "Number of days to look back"}
                }
            }
        ),
        Tool(
            name="get_tech_landscape",
            description="Get technology landscape data",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_id": {"type": "string", "description": "Repository ID"},
                    "org_key": {"type": "string", "description": "Organization key"}
                }
            }
        )
    ]

@server.call_tool()
async def call_tool(name: str, arguments: dict):
    if name == "get_repo_summary":
        # Call existing API endpoint
        response = requests.get(f"{KOSPEX_BASE_URL}/api/summary/{arguments['repo_id']}")
        return [TextContent(type="text", text=json.dumps(response.json()))]
    
    elif name == "get_developers":
        params = {k: v for k, v in arguments.items() if v is not None}
        response = requests.get(f"{KOSPEX_BASE_URL}/api/developers", params=params)
        return [TextContent(type="text", text=json.dumps(response.json()))]
    
    elif name == "get_tech_landscape":
        params = {k: v for k, v in arguments.items() if v is not None}
        response = requests.get(f"{KOSPEX_BASE_URL}/api/landscape", params=params)
        return [TextContent(type="text", text=json.dumps(response.json()))]

if __name__ == "__main__":
    asyncio.run(stdio_server(server))
```

## Option 2: Extend Existing API Routes

Add MCP-specific endpoints to your existing `api_routes.py`:

```python
# In api_routes.py
from mcp.server import Server
from mcp.server.stdio import stdio_server

@router.get("/mcp/tools")
async def mcp_list_tools():
    """MCP tools endpoint"""
    return {
        "tools": [
            {
                "name": "analyze_repository",
                "description": "Analyze a repository for developer activity and tech stack",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "repo_id": {"type": "string"},
                        "analysis_type": {"type": "string", "enum": ["summary", "developers", "tech", "dependencies"]}
                    },
                    "required": ["repo_id", "analysis_type"]
                }
            }
        ]
    }

@router.post("/mcp/call_tool")
async def mcp_call_tool(tool_request: dict):
    """MCP tool execution endpoint"""
    tool_name = tool_request["name"]
    arguments = tool_request["arguments"]
    
    if tool_name == "analyze_repository":
        repo_id = arguments["repo_id"]
        analysis_type = arguments["analysis_type"]
        
        if analysis_type == "summary":
            data = KospexQuery().summary(repo_id=repo_id)
        elif analysis_type == "developers":
            data = KospexQuery().developers(repo_id=repo_id)
        elif analysis_type == "tech":
            data = KospexQuery().tech_landscape(repo_id=repo_id)
        elif analysis_type == "dependencies":
            data = KospexQuery().get_dependencies(id={"repo_id": repo_id})
        
        return {"result": data}
```

## Option 3: Claude Code Settings Configuration

Add kospex MCP server to your Claude Code settings:

```json
{
  "mcpServers": {
    "kospex": {
      "command": "python",
      "args": ["path/to/kospex_mcp_server.py"],
      "env": {
        "KOSPEX_DATABASE_PATH": "/path/to/kospex.db"
      }
    }
  }
}
```

## Option 4: Direct Database Access MCP Server

Create an MCP server that directly accesses your kospex database:

```python
# kospex_db_mcp_server.py
from mcp.server import Server
from kospex_query import KospexQuery
import json

server = Server("kospex-db")

@server.list_tools()
async def list_tools():
    return [
        Tool(
            name="query_repositories",
            description="Query repository data from kospex database",
            inputSchema={
                "type": "object",
                "properties": {
                    "query_type": {"type": "string", "enum": ["list", "summary", "developers", "tech_landscape"]},
                    "repo_id": {"type": "string", "description": "Optional repository ID"},
                    "limit": {"type": "integer", "description": "Limit results"}
                },
                "required": ["query_type"]
            }
        )
    ]

@server.call_tool()
async def call_tool(name: str, arguments: dict):
    if name == "query_repositories":
        kquery = KospexQuery()
        query_type = arguments["query_type"]
        
        if query_type == "list":
            data = kquery.repos(limit=arguments.get("limit", 50))
        elif query_type == "summary":
            data = kquery.summary(repo_id=arguments.get("repo_id"))
        elif query_type == "developers":
            data = kquery.developers(repo_id=arguments.get("repo_id"))
        elif query_type == "tech_landscape":
            data = kquery.tech_landscape(repo_id=arguments.get("repo_id"))
        
        return [TextContent(type="text", text=json.dumps(data))]
```

## Recommended Approach

**Option 1** (Direct FastAPI MCP Server) is recommended because:

- **Leverages existing infrastructure** - Uses your existing FastAPI routes and business logic
- **Clean separation** - Keeps MCP server separate from main application
- **Easy to test and debug** - Can be started independently for testing
- **Scalable** - Can easily add more tools without modifying main application
- **Maintains existing API** - Doesn't require changes to current kospex web server

### Implementation Steps

1. Install MCP SDK: `pip install mcp`
2. Create `kospex_mcp_server.py` with the code above
3. Start kospex web server: `kweb`
4. Start MCP server: `python kospex_mcp_server.py`
5. Configure Claude Code settings to use the MCP server

### Available Tools

The MCP server would expose these tools to Claude Code:

- **get_repo_summary** - Repository statistics and overview
- **get_developers** - Developer activity and contributions
- **get_tech_landscape** - Technology stack analysis
- **get_dependencies** - Software composition analysis
- **get_commits** - Commit history and metadata
- **get_hotspots** - Code hotspot analysis
- **get_observations** - Code quality observations

This would give Claude Code access to all your repository analytics data for enhanced code analysis and insights.