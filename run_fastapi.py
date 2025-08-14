#!/usr/bin/env python3
"""Startup script for FastAPI Kospex web server."""

import uvicorn
import sys
import os
import argparse

# Add src to path so we can import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

def main():
    parser = argparse.ArgumentParser(description='Run FastAPI Kospex web server')
    parser.add_argument('--host', default='127.0.0.1', 
                        help='Host to bind to (default: 127.0.0.1, use 0.0.0.0 for Docker)')
    parser.add_argument('--port', type=int, default=8000,
                        help='Port to bind to (default: 8000)')
    parser.add_argument('--no-reload', action='store_true',
                        help='Disable auto-reload (useful for production)')
    
    args = parser.parse_args()
    
    # Check if running in Docker environment
    in_docker = os.path.exists('/.dockerenv') or os.environ.get('DOCKER_CONTAINER') == 'true'
    
    # Default to 0.0.0.0 if in Docker and host not explicitly set
    if in_docker and args.host == '127.0.0.1':
        args.host = '0.0.0.0'
    
    uvicorn.run(
        "src.kweb2:app",
        host=args.host,
        port=args.port,
        reload=not args.no_reload,
        reload_dirs=["src"] if not args.no_reload else None
    )

if __name__ == "__main__":
    main()