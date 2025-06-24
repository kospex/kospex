"""
Help service for kweb application.
Handles help page routing and template resolution.
"""

from typing import Optional, Tuple
from flask import render_template, Response
from jinja2 import TemplateNotFound

from kweb_security import SecurityValidator

class HelpService:
    """Service for handling help page requests."""

    DEFAULT_HELP_PAGE = "help/index"
    ERROR_PAGE = "404"

    def __init__(self):
        self.security_validator = SecurityValidator()

    def get_help_template_name(self, page_id: Optional[str]) -> str:
        """
        Determine the template name for a help page request.

        Args:
            page_id: The requested help page ID

        Returns:
            Template name to render
        """
        if not page_id:
            return self.DEFAULT_HELP_PAGE

        if self.security_validator.is_safe_filename(page_id):
            return f"help/{page_id}"

        return self.ERROR_PAGE

    def render_help_page(self, page_id: Optional[str]) -> Tuple[Response, int]:
        """
        Render a help page with appropriate error handling.

        Args:
            page_id: The requested help page ID

        Returns:
            Tuple of (rendered template, status code)
        """
        template_name = self.get_help_template_name(page_id)

        try:
            if template_name == self.ERROR_PAGE:
                return render_template('404.html'), 404
            else:
                return render_template(f'{template_name}.html'), 200

        except TemplateNotFound:
            return render_template('404.html'), 404

    def is_valid_help_page(self, page_id: Optional[str]) -> bool:
        """
        Check if a help page ID is valid without rendering.

        Args:
            page_id: The help page ID to validate

        Returns:
            True if the page ID is valid, False otherwise
        """
        if page_id is None:
            return True  # Default page is always valid

        if page_id == "":
            return False  # Empty string is not valid

        return self.security_validator.is_safe_filename(page_id)
