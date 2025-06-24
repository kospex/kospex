"""
Security utilities for kweb application.
Handles input validation and sanitization for web routes.
"""

import re
from typing import Optional


class SecurityValidator:
    """Handles security validation for web inputs."""
    
    # Valid characters for filename/path components
    SAFE_FILENAME_CHARS = set('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_')
    
    @classmethod
    def is_safe_filename(cls, filename: Optional[str]) -> bool:
        """
        Validate that a filename contains only safe characters.
        
        Args:
            filename: The filename to validate
            
        Returns:
            True if filename is safe, False otherwise
        """
        if not filename:
            return False
            
        if not isinstance(filename, str):
            return False
            
        # Check length to prevent excessively long filenames
        if len(filename) > 255:
            return False
            
        # Check for directory traversal attempts
        if '..' in filename or '/' in filename or '\\' in filename:
            return False
            
        # Check that all characters are in allowed set
        return set(filename).issubset(cls.SAFE_FILENAME_CHARS)
    
    @classmethod
    def is_safe_path_component(cls, component: Optional[str]) -> bool:
        """
        Validate that a path component is safe for use in routes.
        
        Args:
            component: The path component to validate
            
        Returns:
            True if component is safe, False otherwise
        """
        return cls.is_safe_filename(component)
    
    @classmethod
    def sanitize_filename(cls, filename: Optional[str]) -> Optional[str]:
        """
        Sanitize a filename by removing unsafe characters.
        
        Args:
            filename: The filename to sanitize
            
        Returns:
            Sanitized filename or None if input is invalid
        """
        if not filename or not isinstance(filename, str):
            return None
            
        # Remove any characters not in the safe set
        sanitized = ''.join(c for c in filename if c in cls.SAFE_FILENAME_CHARS)
        
        # Return None if the result is empty or too long
        if not sanitized or len(sanitized) > 255:
            return None
            
        return sanitized