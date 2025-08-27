#!/usr/bin/env python3
"""
Bitbucket Repository Access Checker

This script reads a file containing Bitbucket clone URLs and checks if you have
access to each repository using your Bitbucket API token.

Usage:
    python bitbucket_checker.py <filename> <api_token>

    Or set BITBUCKET_TOKEN environment variable and run:
    python bitbucket_checker.py <filename>
"""

import sys
import os
import re
import requests
from urllib.parse import urlparse
from typing import List, Tuple, Optional


def parse_bitbucket_url(clone_url: str) -> Optional[Tuple[str, str]]:
    """
    Parse a Bitbucket clone URL to extract workspace and repository name.

    Supports both SSH and HTTPS formats:
    - https://bitbucket.org/workspace/repo.git
    - git@bitbucket.org:workspace/repo.git

    Returns:
        Tuple of (workspace, repo_name) or None if parsing fails
    """
    # Remove .git suffix if present
    url = clone_url.strip().rstrip('.git')

    # SSH format: git@bitbucket.org:workspace/repo
    ssh_pattern = r'git@bitbucket\.org:([^/]+)/(.+)'
    ssh_match = re.match(ssh_pattern, url)
    if ssh_match:
        return ssh_match.groups()

    # HTTPS format: https://bitbucket.org/workspace/repo
    try:
        parsed = urlparse(url)
        if parsed.hostname == 'bitbucket.org' and parsed.path:
            path_parts = parsed.path.strip('/').split('/')
            if len(path_parts) >= 2:
                return path_parts[0], path_parts[1]
    except Exception:
        pass

    return None


def check_repository_access(workspace: str, repo_name: str, token: str) -> Tuple[bool, str, Optional[dict]]:
    """
    Check if the API token has access to the specified repository.

    Returns:
        Tuple of (has_access, status_message, repo_info)
    """
    url = f"https://api.bitbucket.org/2.0/repositories/{workspace}/{repo_name}"

    headers = {
        'Authorization': f'Bearer {token}',
        'Accept': 'application/json'
    }

    try:
        response = requests.get(url, headers=headers, timeout=10)

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


def read_urls_from_file(filename: str) -> List[str]:
    """Read clone URLs from file, one per line."""
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            urls = [line.strip() for line in f if line.strip()]
        return urls
    except FileNotFoundError:
        print(f"Error: File '{filename}' not found")
        sys.exit(1)
    except Exception as e:
        print(f"Error reading file '{filename}': {e}")
        sys.exit(1)


def main():
    # Parse command line arguments
    if len(sys.argv) < 2:
        print("Usage: python bitbucket_checker.py <filename> [api_token]")
        print("Or set BITBUCKET_TOKEN environment variable")
        sys.exit(1)

    filename = sys.argv[1]

    # Get API token from command line or environment
    if len(sys.argv) >= 3:
        token = sys.argv[2]
    else:
        token = os.getenv('BITBUCKET_TOKEN')
        if not token:
            print("Error: Please provide API token as argument or set BITBUCKET_TOKEN environment variable")
            sys.exit(1)

    # Read URLs from file
    print(f"Reading URLs from '{filename}'...")
    urls = read_urls_from_file(filename)
    print(f"Found {len(urls)} URLs to check\n")

    # Check access for each repository
    accessible_repos = []
    inaccessible_repos = []

    for i, url in enumerate(urls, 1):
        print(f"[{i}/{len(urls)}] Checking: {url}")

        # Parse the URL
        parsed = parse_bitbucket_url(url)
        if not parsed:
            print(f"  ❌ Could not parse URL format")
            inaccessible_repos.append((url, "Invalid URL format"))
            continue

        workspace, repo_name = parsed
        print(f"  Repository: {workspace}/{repo_name}")

        # Check access
        has_access, message, repo_info = check_repository_access(workspace, repo_name, token)

        if has_access:
            print(f"  ✅ {message}")
            if repo_info:
                print(f"     Description: {repo_info.get('description', 'N/A')}")
                print(f"     Language: {repo_info.get('language', 'N/A')}")
                print(f"     Private: {repo_info.get('is_private', 'N/A')}")
            accessible_repos.append((url, workspace, repo_name))
        else:
            print(f"  ❌ {message}")
            inaccessible_repos.append((url, message))

        print()

    # Summary
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Total repositories checked: {len(urls)}")
    print(f"Accessible: {len(accessible_repos)}")
    print(f"Inaccessible: {len(inaccessible_repos)}")

    if accessible_repos:
        print(f"\n✅ ACCESSIBLE REPOSITORIES ({len(accessible_repos)}):")
        for url, workspace, repo_name in accessible_repos:
            print(f"  - {workspace}/{repo_name}")

    if inaccessible_repos:
        print(f"\n❌ INACCESSIBLE REPOSITORIES ({len(inaccessible_repos)}):")
        for url, reason in inaccessible_repos:
            print(f"  - {url} ({reason})")


if __name__ == "__main__":
    main()
