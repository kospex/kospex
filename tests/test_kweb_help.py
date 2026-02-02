"""
Tests for kweb help functionality.
"""

import sys
import os
import pytest

# Add src directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from kweb_security import SecurityValidator
from kweb_help_service import HelpService


class TestSecurityValidator:
    """Test cases for SecurityValidator."""

    def test_is_safe_filename_valid_cases(self):
        """Test valid filename cases."""
        assert SecurityValidator.is_safe_filename("index")
        assert SecurityValidator.is_safe_filename("test-page")
        assert SecurityValidator.is_safe_filename("page_123")
        assert SecurityValidator.is_safe_filename("ABC")

    def test_is_safe_filename_invalid_cases(self):
        """Test invalid filename cases."""
        assert not SecurityValidator.is_safe_filename(None)
        assert not SecurityValidator.is_safe_filename("")
        assert not SecurityValidator.is_safe_filename("../etc/passwd")
        assert not SecurityValidator.is_safe_filename("page/subpage")
        assert not SecurityValidator.is_safe_filename("page\\subpage")
        assert not SecurityValidator.is_safe_filename("page with spaces")
        assert not SecurityValidator.is_safe_filename("page@domain.com")
        assert not SecurityValidator.is_safe_filename("page?query=1")
        assert not SecurityValidator.is_safe_filename("a" * 256)  # Too long

    def test_is_safe_filename_non_string(self):
        """Test non-string inputs."""
        assert not SecurityValidator.is_safe_filename(123)
        assert not SecurityValidator.is_safe_filename(["test"])
        assert not SecurityValidator.is_safe_filename({"name": "test"})

    def test_sanitize_filename(self):
        """Test filename sanitization."""
        assert SecurityValidator.sanitize_filename("test-page") == "test-page"
        assert SecurityValidator.sanitize_filename("test@page!") == "testpage"
        assert SecurityValidator.sanitize_filename("") is None
        assert SecurityValidator.sanitize_filename(None) is None
        assert SecurityValidator.sanitize_filename("a" * 256) is None


class TestHelpService:
    """Test cases for HelpService."""

    def setup_method(self):
        """Set up test fixtures."""
        self.help_service = HelpService()

    def test_get_help_template_name_default(self):
        """Test default help template name."""
        assert self.help_service.get_help_template_name(None) == "help/index"
        assert self.help_service.get_help_template_name("") == "help/index"

    def test_get_help_template_name_valid_page(self):
        """Test valid help page names."""
        assert self.help_service.get_help_template_name("installation") == "help/installation"
        assert self.help_service.get_help_template_name("getting-started") == "help/getting-started"

    def test_get_help_template_name_invalid_page(self):
        """Test invalid help page names."""
        assert self.help_service.get_help_template_name("../admin") == "404"
        assert self.help_service.get_help_template_name("page/subpage") == "404"
        assert self.help_service.get_help_template_name("page with spaces") == "404"

    def test_is_valid_help_page(self):
        """Test help page validation."""
        assert self.help_service.is_valid_help_page(None) is True
        assert self.help_service.is_valid_help_page("") is False
        assert self.help_service.is_valid_help_page("installation") is True
        assert self.help_service.is_valid_help_page("../admin") is False

    def test_get_help_template_response_success(self):
        """Test successful help page template resolution."""
        template, status = self.help_service.get_help_template_response("installation")
        assert template == "help/installation.html"
        assert status == 200

    def test_get_help_template_response_default(self):
        """Test default help page template resolution."""
        template, status = self.help_service.get_help_template_response(None)
        assert template == "help/index.html"
        assert status == 200

    def test_get_help_template_response_invalid(self):
        """Test invalid help page returns 404."""
        template, status = self.help_service.get_help_template_response("../admin")
        assert template == "404.html"
        assert status == 404


class TestHelpIntegration:
    """Integration tests for help functionality using FastAPI TestClient."""

    @pytest.fixture
    def client(self):
        """Create FastAPI test client."""
        pytest.importorskip("httpx", reason="httpx required for FastAPI TestClient")
        from fastapi.testclient import TestClient
        from kweb2 import app
        return TestClient(app)

    def test_help_default_route(self, client):
        """Test default help route."""
        response = client.get("/help/")
        assert response.status_code == 200

    def test_help_valid_page_route(self, client):
        """Test valid help page route - returns 200 even if template falls back to index."""
        response = client.get("/help/installation")
        # This will return 200 whether the specific template exists or falls back to index
        assert response.status_code == 200

    def test_help_path_traversal_blocked(self):
        """Test that path traversal attempts are handled safely."""
        # Security validation happens at the HelpService level
        help_service = HelpService()
        assert not help_service.is_valid_help_page("../admin")
        assert help_service.get_help_template_name("../admin") == "404"
