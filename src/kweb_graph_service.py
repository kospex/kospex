#!/usr/bin/env python3
"""Graph data service for Kospex web interface."""

from os.path import basename
from kospex_query import KospexQuery
import kospex_utils as KospexUtils
import kospex_web as KospexWeb
import kospex_email as KospexEmail


class GraphService:
    """Service class for generating graph data for visualizations."""

    def __init__(self):
        self.query = KospexQuery()

    def get_graph_data(self, focus=None, org_key=None, request_args=None):
        """
        Generate graph data for force-directed visualizations.

        Args:
            focus: Optional focus type ('repo', etc.)
            org_key: Organization key or identifier
            request_args: Flask request.args or FastAPI query params for additional parameters

        Returns:
            dict: Graph data with nodes and links
        """
        # Extract parameters
        params = KospexWeb.get_id_params(org_key, request_args or {})
        repo_id = params.get("repo_id")
        org_key_param = params.get("org_key")
        git_server = params.get("server")
        author_email = params.get("author_email")

        print(f"GraphService - org_key: {org_key_param}, repo_id: {repo_id}, focus: {focus}, author_email: {author_email}")

        # Get raw graph information
        org_info = self._get_org_info(
            focus, org_key_param, repo_id, author_email, git_server, request_args
        )

        # Process the data into graph format
        return self._process_graph_data(org_info, repo_id, focus)

    def _get_org_info(self, focus, org_key, repo_id, author_email, git_server, request_args):
        """Fetch raw organization/graph info based on parameters."""
        if org_key:
            return self.query.get_graph_info(org_key=org_key)
        elif focus:
            if focus == "repo":
                return self.query.get_graph_info(repo_id=repo_id)
            else:
                return self.query.get_graph_info(author_email=author_email, by_repo=True)
        elif repo_id:
            return self.query.get_repo_files_graph_info(repo_id=repo_id)
        elif author_email:
            return self.query.get_graph_info(author_email=author_email)
        elif git_server:
            return self.query.get_graph_info(git_server=git_server)
        else:
            # Fallback to check request args for author_email
            fallback_author_email = None
            if request_args and hasattr(request_args, 'get'):
                fallback_author_email = request_args.get('author_email')
            elif request_args and isinstance(request_args, dict):
                fallback_author_email = request_args.get('author_email')

            if fallback_author_email:
                fallback_author_email = fallback_author_email.replace(" ", "+")
            return self.query.get_graph_info(author_email=fallback_author_email)

    def _process_graph_data(self, org_info, repo_id, focus):
        """Process raw org info into graph nodes and links."""
        dev_lookup = {}
        repo_lookup = {}
        file_lookup = {}
        links = []
        nodes = []

        group_numbers = {
            'Active': 1,
            'Aging': 2,
            'Stale': 3,
            'Unmaintained': 4
        }

        for element in org_info:
            last_commit = element.get("last_commit")
            status = KospexUtils.development_status(
                KospexUtils.days_ago(last_commit)
            )

            # Process developers
            self._process_developer_node(
                element, dev_lookup, group_numbers, status, last_commit
            )

            # Process repositories or files
            if repo_id and not focus:
                self._process_file_node(element, file_lookup)
            else:
                self._process_repo_node(
                    element, repo_lookup, group_numbers, status, last_commit
                )

            # Create links
            link_key = "file_path" if repo_id else "_repo_id"
            links.append({
                "source": element['author'],
                "target": element.get(link_key),
                "commits": element['commits']
            })

        # Collect all nodes
        nodes.extend(dev_lookup.values())
        nodes.extend(repo_lookup.values())
        nodes.extend(file_lookup.values())

        return {
            "nodes": nodes,
            "links": links,
        }

    def _process_developer_node(self, element, dev_lookup, group_numbers, status, last_commit):
        """Process developer node data."""
        author = element['author']
        if author not in dev_lookup:
            b64_email = KospexUtils.encode_base64(author)
            email_details = KospexEmail.get_email_type(author)
            dev_lookup[author] = {
                "id": author,
                "id_b64": b64_email,
                "group": 1,
                "node_type": "developer",
                "label": KospexUtils.extract_github_username(author),
                "info": author,
                "is_bot": email_details.get("is_bot", False),
                "commits": element.get("commits"),
                "status_group": group_numbers.get(status, 4),
                "status": status,
                "last_commit": last_commit,
                "repos": 1
            }
        else:
            dev_lookup[author]['repos'] += 1

    def _process_file_node(self, element, file_lookup):
        """Process file node data."""
        file_path = element.get('file_path')
        if file_path and file_path not in file_lookup:
            file_lookup[file_path] = {
                "id": file_path,
                "group": 2,
                "label": basename(file_path),
                "info": file_path
            }

    def _process_repo_node(self, element, repo_lookup, group_numbers, status, last_commit):
        """Process repository node data."""
        repo_id = element['_repo_id']
        if repo_id not in repo_lookup:
            repo_lookup[repo_id] = {
                "id": repo_id,
                "group": 2,
                "node_type": "repo",
                "commits": element.get("commits", 0),
                "status_group": group_numbers.get(status, 4),
                "status": status,
                "link": f"/repo/{repo_id}",
                "last_commit": last_commit,
                "label": element['_git_repo'],
                "info": repo_id
            }
