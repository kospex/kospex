#!/usr/bin/env python3
"""
Email analysis module for Kospex.

This module provides comprehensive email address analysis including:
- Bot detection for various automation tools
- Domain classification (personal, corporate, academic, etc.)
- GitHub handle extraction from GitHub emails
- Noreply email detection
"""

from dataclasses import dataclass
from typing import Optional, Dict, Set
import re


@dataclass
class EmailInfo:
    """
    Structured information about an email address.
    
    Attributes:
        username: The part before the @ symbol
        domain_name: The domain part of the email
        github_handle: GitHub username if extractable from GitHub emails
        is_bot: True if this appears to be a bot email
        bot_type: The type of bot if detected
        is_noreply: True if this is a noreply email address
        is_github_noreply: True if this is a GitHub noreply email
        domain_type: Classification of the domain
    """
    username: Optional[str] = None
    domain_name: Optional[str] = None
    github_handle: Optional[str] = None
    is_bot: bool = False
    bot_type: Optional[str] = None
    is_noreply: bool = False
    is_github_noreply: bool = False
    domain_type: str = "unknown"
    
    def to_dict(self) -> Dict:
        """Convert EmailInfo to dictionary for backwards compatibility."""
        return {
            'username': self.username,
            'domain_name': self.domain_name,
            'github_handle': self.github_handle,
            'is_bot': self.is_bot,
            'bot_type': self.bot_type,
            'is_noreply': self.is_noreply,
            'is_github_noreply': self.is_github_noreply,
            'domain_type': self.domain_type
        }


class EmailAnalyzer:
    """
    Comprehensive email address analyzer for developer activity analysis.
    
    This class provides methods to analyze email addresses and extract useful
    information for repository analysis, including bot detection and domain
    classification.
    """
    
    # Known bot patterns for exact email matches
    EXACT_BOT_EMAILS = {
        'noreply@github.com': 'github-system',
        'support@github.com': 'github-support',
        'security@github.com': 'github-security',
        'web-flow@github.com': 'github-web-flow',
        'merge@github.com': 'github-merge'
    }
    
    # Known bot keywords for username matching
    KNOWN_BOTS = {
        'dependabot': 'dependabot',
        'renovatebot': 'renovatebot',
        'renovate': 'renovatebot',
        'github-actions': 'github-actions',
        'actions-user': 'github-actions',
        'greenkeeper': 'greenkeeper',
        'snyk-bot': 'snyk-bot',
        'whitesource-bolt': 'whitesource-bolt',
        'pyup-bot': 'pyup-bot',
        'jenkins': 'jenkins',
        'travis': 'travis-ci',
        'circleci': 'circleci',
        'gitlab-ci': 'gitlab-ci',
        'azure-devops': 'azure-devops',
        'codecov': 'codecov',
        'sonarcloud': 'sonarcloud',
        'codacy': 'codacy',
        'deepsource': 'deepsource'
    }
    
    # Generic bot indicators
    GENERIC_BOT_KEYWORDS = {
        'automation': 'automation-bot',
        'deploy': 'deploy-bot',
        'build': 'build-bot',
        'service': 'service-bot',
        'system': 'system-bot',
        'robot': 'robot-bot',
        'script': 'script-bot',
        'pipeline': 'pipeline-bot',
        'security': 'security-bot',
        'vulnerability': 'vulnerability-bot',
        'docs': 'docs-bot',
        'wiki': 'wiki-bot'
    }
    
    # Personal email domains
    PERSONAL_DOMAINS = {
        'gmail.com', 'yahoo.com', 'hotmail.com', 'outlook.com',
        'icloud.com', 'me.com', 'live.com', 'msn.com',
        'aol.com', 'protonmail.com', 'fastmail.com'
    }
    
    def __init__(self):
        """Initialize the EmailAnalyzer."""
        pass
    
    def analyze(self, email_address: str) -> EmailInfo:
        """
        Analyze an email address and return structured information.
        
        Args:
            email_address: The email address to analyze
            
        Returns:
            EmailInfo object containing analysis results
        """
        if not email_address:
            return EmailInfo()
        
        # Parse basic email components
        email_info = self._parse_email_parts(email_address)
        
        # Detect bot patterns
        self._detect_bots(email_info)
        
        # Classify domain
        self._classify_domain(email_info)
        
        # Extract GitHub handle if applicable
        self._extract_github_handle(email_info)
        
        # Detect noreply patterns
        self._detect_noreply(email_info)
        
        return email_info
    
    def _parse_email_parts(self, email_address: str) -> EmailInfo:
        """Parse email into username and domain components."""
        email_info = EmailInfo()
        
        if '@' in email_address:
            parts = email_address.split('@', 1)
            email_info.username = parts[0]
            email_info.domain_name = parts[1]
        else:
            # Handle malformed emails
            email_info.username = email_address
            email_info.domain_name = ""
        
        return email_info
    
    def _detect_bots(self, email_info: EmailInfo) -> None:
        """Detect if this email belongs to a bot."""
        if not email_info.username or not email_info.domain_name:
            return
        
        email_lower = f"{email_info.username}@{email_info.domain_name}".lower()
        username_lower = email_info.username.lower()
        
        # Check exact email matches first
        if email_lower in self.EXACT_BOT_EMAILS:
            email_info.is_bot = True
            email_info.bot_type = self.EXACT_BOT_EMAILS[email_lower]
            return
        
        # Check for known bot names in username
        for bot_keyword, bot_type in self.KNOWN_BOTS.items():
            if bot_keyword in username_lower:
                email_info.is_bot = True
                email_info.bot_type = bot_type
                return
        
        # Check for generic bot indicators
        for keyword, bot_type in self.GENERIC_BOT_KEYWORDS.items():
            if keyword in username_lower:
                email_info.is_bot = True
                email_info.bot_type = bot_type
                return
        
        # Check for numeric-only usernames (often bots)
        # Extract the main part before any + signs for GitHub emails
        main_username = username_lower.split('+')[-1] if '+' in username_lower else username_lower
        # Remove [bot] suffix for GitHub bot format
        main_username = re.sub(r'\[bot\]$', '', main_username)
        
        if main_username.isdigit():
            email_info.is_bot = True
            email_info.bot_type = "numeric-account"
    
    def _classify_domain(self, email_info: EmailInfo) -> None:
        """Classify the domain type."""
        if not email_info.domain_name:
            email_info.domain_type = "unknown"
            return
        
        domain_lower = email_info.domain_name.lower()
        username_lower = email_info.username.lower() if email_info.username else ""
        
        # Check for noreply patterns in username first (takes priority)
        if 'noreply' in username_lower or 'no-reply' in username_lower:
            email_info.domain_type = "noreply"
        
        # GitHub domains
        elif 'github.com' in domain_lower:
            email_info.domain_type = "github"
        
        # Personal email providers
        elif domain_lower in self.PERSONAL_DOMAINS:
            email_info.domain_type = "personal"
        
        # Academic institutions
        elif domain_lower.endswith('.edu'):
            email_info.domain_type = "academic"
        
        # Government
        elif domain_lower.endswith('.gov'):
            email_info.domain_type = "government"
        
        # Organizations
        elif domain_lower.endswith('.org'):
            email_info.domain_type = "organization"
        
        # Noreply domains
        elif 'noreply' in domain_lower or 'no-reply' in domain_lower:
            email_info.domain_type = "noreply"
        
        # Default to corporate
        else:
            email_info.domain_type = "corporate"
    
    def _extract_github_handle(self, email_info: EmailInfo) -> None:
        """Extract GitHub handle from GitHub emails."""
        if not email_info.domain_name or 'github.com' not in email_info.domain_name.lower():
            return
        
        if not email_info.username:
            return
        
        # GitHub noreply format: "12345678+username@users.noreply.github.com"
        if '+' in email_info.username:
            parts = email_info.username.split('+')
            if len(parts) >= 2:
                email_info.github_handle = parts[1]
    
    def _detect_noreply(self, email_info: EmailInfo) -> None:
        """Detect noreply email patterns."""
        if not email_info.username or not email_info.domain_name:
            return
        
        username_lower = email_info.username.lower()
        domain_lower = email_info.domain_name.lower()
        
        # Check username for noreply patterns
        noreply_patterns = ['noreply', 'no-reply', 'donotreply']
        email_info.is_noreply = any(pattern in username_lower for pattern in noreply_patterns)
        
        # Check domain for noreply patterns
        if not email_info.is_noreply:
            email_info.is_noreply = any(pattern in domain_lower for pattern in noreply_patterns)
        
        # GitHub-specific noreply detection
        email_info.is_github_noreply = 'noreply.github.com' in domain_lower


# Convenience function for backwards compatibility
def get_email_type(email_address: str) -> Dict:
    """
    Analyze an email address and return information as a dictionary.
    
    This function provides backwards compatibility with the original implementation.
    
    Args:
        email_address: The email address to analyze
        
    Returns:
        Dictionary containing email analysis results
    """
    analyzer = EmailAnalyzer()
    email_info = analyzer.analyze(email_address)
    return email_info.to_dict()


# Create default analyzer instance for module-level usage
default_analyzer = EmailAnalyzer()
analyze_email = default_analyzer.analyze