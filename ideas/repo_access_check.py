#!/usr/bin/env python3
"""
Repository Access Checker

This script checks if you have access to a single Bitbucket repository
using your Bitbucket API token from the environment. Supports both
Bitbucket Cloud (bitbucket.org) and private/self-hosted Bitbucket instances.

Usage:
    python repo_access_check.py <git_clone_url>

Environment:
    BITBUCKET_TOKEN - Your Bitbucket API token
    CACERTS - Path to CA certificates file/directory (optional)
"""

import sys
import os
import re
import requests
from urllib.parse import urlparse
from typing import Optional, Tuple


def parse_bitbucket_url(clone_url: str) -> Optional[Tuple[str, str, str]]:
    """
    Parse a Bitbucket clone URL to extract hostname, workspace and repository name.

    Supports both SSH and HTTPS formats for any Bitbucket instance:
    - https://your-bitbucket.com/workspace/repo.git
    - git@your-bitbucket.com:workspace/repo.git

    Returns:
        Tuple of (hostname, workspace, repo_name) or None if parsing fails
    """
    # Remove .git suffix if present
    url = clone_url.strip().rstrip('.git')

    # SSH format: git@hostname:workspace/repo
    ssh_pattern = r'git@([^:]+):([^/]+)/(.+)'
    ssh_match = re.match(ssh_pattern, url)
    if ssh_match:
        hostname, workspace, repo_name = ssh_match.groups()
        return hostname, workspace, repo_name

    # HTTPS format: https://hostname/workspace/repo
    try:
        parsed = urlparse(url)
        if parsed.hostname and parsed.path:
            path_parts = parsed.path.strip('/').split('/')
            if len(path_parts) >= 2:
                return parsed.hostname, path_parts[0], path_parts[1]
    except Exception:
        pass

    return None


def check_repository_access(hostname: str, workspace: str, repo_name: str, token: str, verify_certs=True) -> Tuple[bool, str, Optional[dict]]:
    """
    Check if the API token has access to the specified repository.

    Args:
        hostname: Bitbucket hostname (e.g., 'bitbucket.org' or 'your-bitbucket.com')
        workspace: Repository workspace/project
        repo_name: Repository name
        token: API token
        verify_certs: SSL certificate verification (True, False, or path to CA certs)

    Returns:
        Tuple of (has_access, status_message, repo_info)
    """
    # Construct API URL based on hostname
    if hostname == 'bitbucket.org':
        # Bitbucket Cloud API
        api_url = f"https://api.bitbucket.org/2.0/repositories/{workspace}/{repo_name}"
    else:
        # Private Bitbucket Server API (assumes standard path)
        api_url = f"https://{hostname}/rest/api/1.0/projects/{workspace}/repos/{repo_name}"

    headers = {
        'Authorization': f'Bearer {token}',
        'Accept': 'application/json'
    }

    try:
        response = requests.get(api_url, headers=headers, timeout=10, verify=verify_certs)

        if response.status_code == 200:
            repo_info = response.json()
            return True, "Access granted", repo_info
        elif response.status_code == 403:
            return False, "Access denied (403 Forbidden)", None
        elif response.status_code == 404:
            return False, "Repository not found (404)", None
        elif response.status_code == 401:
            return False, "Invalid or expired token (401 Unauthorized)", None
        else:
            return False, f"HTTP {response.status_code}: {response.reason}", None

    except requests.exceptions.RequestException as e:
        return False, f"Request failed: {str(e)}", None


def get_ca_certs_config():
    """
    Get CA certificate configuration from environment variable.
    
    Returns:
        CA certificate verification setting for requests (True, False, or path)
    """
    ca_certs_path = os.getenv('CACERTS')
    if not ca_certs_path:
        return True  # Default to system CA bundle
    
    # Validate the path exists and is readable
    if os.path.exists(ca_certs_path):
        if os.path.isfile(ca_certs_path) or os.path.isdir(ca_certs_path):
            try:
                # Test if we can read the path
                if os.path.isfile(ca_certs_path):
                    with open(ca_certs_path, 'r'):
                        pass
                elif os.path.isdir(ca_certs_path):
                    os.listdir(ca_certs_path)
                return ca_certs_path
            except (PermissionError, OSError) as e:
                print(f"Warning: CACERTS path '{ca_certs_path}' is not readable: {e}")
                print("Falling back to system CA bundle")
                return True
        else:
            print(f"Warning: CACERTS path '{ca_certs_path}' is not a valid file or directory")
            print("Falling back to system CA bundle")
            return True
    else:
        print(f"Warning: CACERTS path '{ca_certs_path}' does not exist")
        print("Falling back to system CA bundle")
        return True


def main():
    # Parse command line arguments
    if len(sys.argv) != 2:
        print("Usage: python repo_access_check.py <git_clone_url>")
        print("Environment:")
        print("  BITBUCKET_TOKEN - Your Bitbucket API token (required)")
        print("  CACERTS - Path to CA certificates file/directory (optional)")
        sys.exit(1)

    clone_url = sys.argv[1]

    # Get API token from environment
    token = os.getenv('BITBUCKET_TOKEN')
    if not token:
        print("Error: BITBUCKET_TOKEN environment variable not set")
        sys.exit(1)

    # Get CA certificate configuration
    verify_certs = get_ca_certs_config()
    if isinstance(verify_certs, str):
        print(f"Using custom CA certificates: {verify_certs}")
    elif verify_certs is True:
        print("Using system CA certificate bundle")

    print(f"Checking access to: {clone_url}")

    # Parse the URL
    parsed = parse_bitbucket_url(clone_url)
    if not parsed:
        print("❌ Could not parse URL format")
        print("Supported formats:")
        print("  - https://hostname/workspace/repo.git")
        print("  - git@hostname:workspace/repo.git")
        sys.exit(1)

    hostname, workspace, repo_name = parsed
    print(f"Hostname: {hostname}")
    print(f"Repository: {workspace}/{repo_name}")

    # Check access
    has_access, message, repo_info = check_repository_access(hostname, workspace, repo_name, token, verify_certs)

    if has_access:
        print(f"✅ {message}")
        if repo_info:
            # Handle different response formats between Cloud and Server
            if hostname == 'bitbucket.org':
                # Bitbucket Cloud format
                print(f"Description: {repo_info.get('description', 'N/A')}")
                print(f"Language: {repo_info.get('language', 'N/A')}")
                print(f"Private: {repo_info.get('is_private', 'N/A')}")
                print(f"Size: {repo_info.get('size', 'N/A')} bytes")
            else:
                # Bitbucket Server format
                print(f"Description: {repo_info.get('description', 'N/A')}")
                print(f"Project: {repo_info.get('project', {}).get('name', 'N/A')}")
                print(f"Public: {repo_info.get('public', 'N/A')}")
                print(f"Clone URL: {repo_info.get('cloneUrl', 'N/A')}")
        sys.exit(0)
    else:
        print(f"❌ {message}")
        sys.exit(1)


if __name__ == "__main__":
    main()