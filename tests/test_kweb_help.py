"""
Tests for kweb help functionality.
"""

import sys
import os
import pytest
from unittest.mock import Mock, patch
from flask import Flask, render_template
from jinja2 import TemplateNotFound

# Add src directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

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
        assert not SecurityValidator.is_safe_filename(['test'])
        assert not SecurityValidator.is_safe_filename({'name': 'test'})
        
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
        
    @patch('kweb_help_service.render_template')
    def test_render_help_page_success(self, mock_render):
        """Test successful help page rendering."""
        mock_render.return_value = "rendered_content"
        
        result, status = self.help_service.render_help_page("installation")
        
        assert result == "rendered_content"
        assert status == 200
        mock_render.assert_called_once_with("help/installation.html")
        
    @patch('kweb_help_service.render_template')
    def test_render_help_page_not_found(self, mock_render):
        """Test help page rendering when template not found."""
        # Mock the first call to raise TemplateNotFound, second call to return 404 page
        mock_render.side_effect = [TemplateNotFound("help/nonexistent.html"), "404_content"]
        
        result, status = self.help_service.render_help_page("nonexistent")
        
        assert result == "404_content"
        assert status == 404
        
    @patch('kweb_help_service.render_template')
    def test_render_help_page_invalid_id(self, mock_render):
        """Test help page rendering with invalid page ID."""
        mock_render.return_value = "404_content"
        
        result, status = self.help_service.render_help_page("../admin")
        
        assert result == "404_content"
        assert status == 404
        mock_render.assert_called_once_with("404.html")


class TestHelpIntegration:
    """Integration tests for help functionality."""
    
    @pytest.fixture
    def app(self):
        """Create test Flask app."""
        app = Flask(__name__)
        app.config['TESTING'] = True
        
        help_service = HelpService()
        
        @app.route('/help')
        @app.route('/help/')
        @app.route('/help/<id>')
        def help(id=None):
            return help_service.render_help_page(id)
            
        return app
        
    @pytest.fixture
    def client(self, app):
        """Create test client."""
        return app.test_client()
        
    def test_help_default_route(self, client):
        """Test default help route."""
        with patch('kweb_help_service.render_template') as mock_render:
            mock_render.return_value = "help_index"
            response = client.get('/help')
            assert response.status_code == 200
            
    def test_help_valid_page_route(self, client):
        """Test valid help page route."""
        with patch('kweb_help_service.render_template') as mock_render:
            mock_render.return_value = "help_page"
            response = client.get('/help/installation')
            assert response.status_code == 200
            
    def test_help_invalid_page_route(self, client):
        """Test invalid help page route."""
        with patch('kweb_help_service.render_template') as mock_render:
            mock_render.return_value = "404_page"
            response = client.get('/help/../admin')
            assert response.status_code == 404