#!/usr/bin/env python3
"""
Web endpoint regression tests for kweb2.py

These tests validate that all web endpoints are functional and return expected responses.
Requires a running kweb server on localhost:8000.

Usage:
- Run locally with kweb running: pytest -m web
- Skip in CI: Tests are automatically skipped when CI=true environment variable is set
- Run all tests except web: pytest -m "not web"
"""

import os
import pytest
import requests
from typing import List, Dict, Any


# Skip all web tests in CI environment
pytestmark = pytest.mark.skipif(
    os.environ.get("CI") == "true" or os.environ.get("SKIP_WEB_TESTS") == "true",
    reason="Web tests skipped in CI environment or when SKIP_WEB_TESTS=true"
)

# Test configuration
BASE_URL = "http://localhost:8000"
TIMEOUT = 10  # seconds

# Test data - these should exist in a typical kospex database
TEST_REPO_ID = "github.com~kospex~kospex"
TEST_SERVER = "github.com"
TEST_ORG_KEY = "github.com~kospex"
TEST_TECH = "Python"
TEST_DEVELOPER_EMAIL = "test@example.com"
TEST_COMMIT_HASH = "abc123"


class TestWebEndpoints:
    """Test class for web endpoint regression testing."""

    @pytest.fixture(autouse=True)
    def check_server_running(self):
        """Check if kweb server is running before running tests."""
        try:
            response = requests.get(f"{BASE_URL}/health", timeout=5)
            if response.status_code != 200:
                pytest.skip("kweb server not responding properly on localhost:8000")
        except requests.exceptions.RequestException:
            pytest.skip("kweb server not running on localhost:8000. Start with: kweb or python run_fastapi.py")

    # HTML Endpoints - Main Pages
    @pytest.mark.web
    @pytest.mark.parametrize("endpoint", [
        "/",
        "/summary/",
        "/help/",
        "/developers/",
        "/servers/",
        "/metadata/",
        "/metadata/repos/",
        "/orphans/",
        "/osi/",
        #"/collab/",
        "/orgs/",
        "/recent/",
        "/repos/",
        "/landscape/",
        "/graph/",
        "/tenure/",
        "/meta/author-domains",
        "/tech-change/",
        #"/developer/",
        "/observations/",
        "/commits/",
        "/dependencies/",
        "/package-check/",
        "/files/repo/",
        "/supply-chain/",
    ])
    def test_html_endpoints_main_pages(self, endpoint: str):
        """Test that main HTML pages return 200 and HTML content."""
        response = requests.get(f"{BASE_URL}{endpoint}", timeout=TIMEOUT)

        assert response.status_code == 200, f"Endpoint {endpoint} returned {response.status_code}"
        assert "text/html" in response.headers.get("content-type", ""), f"Endpoint {endpoint} did not return HTML"
        assert len(response.text) > 0, f"Endpoint {endpoint} returned empty response"

    # HTML Endpoints - Parametrized with IDs
    @pytest.mark.web
    @pytest.mark.parametrize("endpoint,test_param", [
        ("/summary/{}", TEST_REPO_ID),
        ("/help/{}", "commands"),
        ("/metadata/repos/{}", TEST_REPO_ID),
        ("/orphans/{}", TEST_SERVER),
        ("/bubble/{}", TEST_REPO_ID),
        ("/treemap/{}", TEST_REPO_ID),
        ("/osi/{}", TEST_REPO_ID),
        ("/collab/{}", TEST_REPO_ID),
        ("/collab/graph/{}", TEST_REPO_ID),
        ("/orgs/{}", TEST_SERVER),
        ("/repos/{}", TEST_SERVER),
        ("/repo/{}", TEST_REPO_ID),
        ("/key-person/{}", TEST_REPO_ID),
        ("/landscape/{}", TEST_REPO_ID),
        ("/graph/{}", TEST_ORG_KEY),
        ("/tenure/{}", TEST_REPO_ID),
        ("/tech/{}", TEST_TECH),
        ("/developer/?author_email={}", TEST_DEVELOPER_EMAIL),
        ("/commits/{}", TEST_REPO_ID),
        ("/dependencies/{}", TEST_REPO_ID),
        ("/hotspots/{}", TEST_REPO_ID),
        ("/files/repo/{}", TEST_REPO_ID),
    ])
    def test_html_endpoints_with_params(self, endpoint: str, test_param: str):
        """Test HTML endpoints that require parameters."""
        url = f"{BASE_URL}{endpoint.format(test_param)}"
        response = requests.get(url, timeout=TIMEOUT)

        # Accept both 200 (data found) and 404 (no data) as valid responses
        # since test data might not exist in the database
        assert response.status_code in [200, 404], f"Endpoint {url} returned unexpected status {response.status_code}"

        if response.status_code == 200:
            assert "text/html" in response.headers.get("content-type", ""), f"Endpoint {url} did not return HTML"
            assert len(response.text) > 0, f"Endpoint {url} returned empty response"

    # API Endpoints - JSON responses
    @pytest.mark.web
    @pytest.mark.parametrize("endpoint", [
        "/api/servers/",
        "/api/developers/",
        "/api/orgs/",
        "/api/repos/",
        "/api/health",
        "/api/summary",
        "/api/tech-landscape",
    ])
    def test_api_endpoints_json(self, endpoint: str):
        """Test that API endpoints return valid JSON responses."""
        response = requests.get(f"{BASE_URL}{endpoint}", timeout=TIMEOUT)

        assert response.status_code == 200, f"API endpoint {endpoint} returned {response.status_code}"
        assert "application/json" in response.headers.get("content-type", ""), f"API endpoint {endpoint} did not return JSON"

        # Verify it's valid JSON
        try:
            json_data = response.json()
            assert isinstance(json_data, (dict, list)), f"API endpoint {endpoint} returned invalid JSON structure"
        except ValueError as e:
            pytest.fail(f"API endpoint {endpoint} returned invalid JSON: {e}")

    # API Endpoints - With parameters
    @pytest.mark.web
    @pytest.mark.parametrize("endpoint,test_param", [
        ("/api/servers/{}", TEST_SERVER),
        ("/api/developers/?author_email={}", TEST_DEVELOPER_EMAIL),
        ("/api/repos/{}", TEST_SERVER),
        ("/api/developer/?author_email={}", TEST_DEVELOPER_EMAIL),
        ("/api/collab/graph/{}", TEST_REPO_ID),
    ])
    def test_api_endpoints_with_params(self, endpoint: str, test_param: str):
        """Test API endpoints that require parameters."""
        url = f"{BASE_URL}{endpoint.format(test_param)}"
        response = requests.get(url, timeout=TIMEOUT)

        # Accept both 200 (data found) and 404 (no data) as valid responses
        assert response.status_code in [200, 404], f"API endpoint {url} returned unexpected status {response.status_code}"

        if response.status_code == 200:
            assert "application/json" in response.headers.get("content-type", ""), f"API endpoint {url} did not return JSON"

            try:
                json_data = response.json()
                assert isinstance(json_data, (dict, list)), f"API endpoint {url} returned invalid JSON structure"
            except ValueError as e:
                pytest.fail(f"API endpoint {url} returned invalid JSON: {e}")

    # Special endpoints with complex parameters
    @pytest.mark.web
    @pytest.mark.parametrize("endpoint,test_params", [
        ("/org-graph/{}", [TEST_ORG_KEY]),
        ("/org-graph/{}/{}", ["focus", TEST_ORG_KEY]),
        ("/developers/active/{}", [TEST_REPO_ID]),
        ("/file-collab/{}", [TEST_REPO_ID]),
        ("/commit/{}/{}", [TEST_REPO_ID, TEST_COMMIT_HASH]),
    ])
    def test_special_endpoints(self, endpoint: str, test_params: List[str]):
        """Test endpoints with complex parameter patterns."""
        url = f"{BASE_URL}{endpoint.format(*test_params)}"
        response = requests.get(url, timeout=TIMEOUT)

        # Accept various successful status codes
        assert response.status_code in [200, 404], f"Special endpoint {url} returned unexpected status {response.status_code}"

        if response.status_code == 200:
            content_type = response.headers.get("content-type", "")
            assert any(ct in content_type for ct in ["text/html", "application/json"]), f"Endpoint {url} returned unexpected content type: {content_type}"

    # Health check endpoint - critical for monitoring
    @pytest.mark.web
    def test_health_endpoint_detailed(self):
        """Test the health endpoint in detail."""
        response = requests.get(f"{BASE_URL}/health", timeout=TIMEOUT)

        assert response.status_code == 200, "Health endpoint must return 200"

        # Should return JSON with status information
        try:
            health_data = response.json()
            assert "status" in health_data or isinstance(health_data, dict), "Health endpoint should return structured data"
        except ValueError:
            # If it's not JSON, it should at least return some content
            assert len(response.text) > 0, "Health endpoint returned empty response"

    # Test for URL generation endpoint
    @pytest.mark.web
    def test_generate_repo_id_endpoint(self):
        """Test the repo ID generation endpoint."""
        # Test with a valid GitHub URL
        test_url = "https://github.com/kospex/kospex"
        response = requests.get(f"{BASE_URL}/generate-repo-id/", params={"url": test_url}, timeout=TIMEOUT)

        # This endpoint might return various formats, just ensure it responds
        assert response.status_code in [200, 400, 404], f"Generate repo ID endpoint returned unexpected status {response.status_code}"

    # Test POST endpoint (without actually uploading a file)
    @pytest.mark.web
    def test_package_upload_endpoint_options(self):
        """Test the package upload endpoint with OPTIONS request."""
        # Test OPTIONS request to ensure CORS is working
        response = requests.options(f"{BASE_URL}/package-check/upload", timeout=TIMEOUT)

        # OPTIONS should be allowed, or we get 405 Method Not Allowed (which is also fine)
        assert response.status_code in [200, 204, 405], f"Package upload OPTIONS returned unexpected status {response.status_code}"

    # Error handling tests
    @pytest.mark.web
    def test_404_handling(self):
        """Test that non-existent endpoints return appropriate 404 errors."""
        response = requests.get(f"{BASE_URL}/nonexistent-endpoint", timeout=TIMEOUT)

        # Should return 404 or be handled by catch-all route
        assert response.status_code in [404, 200], f"Non-existent endpoint returned unexpected status {response.status_code}"

    # Performance test - ensure endpoints respond quickly
    @pytest.mark.web
    def test_response_time_reasonable(self):
        """Test that key endpoints respond within reasonable time."""
        import time

        endpoints_to_test = ["/", "/health", "/summary/", "/api/health"]

        for endpoint in endpoints_to_test:
            start_time = time.time()
            response = requests.get(f"{BASE_URL}{endpoint}", timeout=TIMEOUT)
            elapsed_time = time.time() - start_time

            assert response.status_code in [200, 404], f"Endpoint {endpoint} returned unexpected status"
            assert elapsed_time < 5.0, f"Endpoint {endpoint} took too long to respond: {elapsed_time:.2f}s"


if __name__ == "__main__":
    # Allow running this file directly for quick testing
    import subprocess
    import sys

    print("Running web endpoint tests...")
    print("Make sure kweb is running on localhost:8000 first!")

    # Run pytest with web marker
    result = subprocess.run([sys.executable, "-m", "pytest", "-m", "web", "-v", __file__], capture_output=False)
    sys.exit(result.returncode)
