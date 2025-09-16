#!/usr/bin/env python3
"""
Tests for the get_email_type function in kospex_utils.py

Run with: pytest tests/test_email_type.py -v
"""

import pytest
import sys
import os

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import kospex_email as KospexEmail


class TestGetEmailType:
    """Test class for get_email_type function."""

    def test_basic_functionality(self):
        """Test basic email parsing functionality."""
        result = KospexEmail.get_email_type("user@domain.com")
        
        assert result['username'] == "user"
        assert result['domain_name'] == "domain.com"
        assert result['github_handle'] is None
        assert result['is_bot'] is False
        assert result['bot_type'] is None
        assert result['is_noreply'] is False
        assert result['is_github_noreply'] is False
        assert result['domain_type'] == "corporate"

    def test_malformed_email_handling(self):
        """Test handling of malformed or invalid email addresses."""
        # Test empty string
        result = KospexEmail.get_email_type("")
        assert result['username'] is None
        assert result['domain_name'] is None
        assert result['domain_type'] == "unknown"
        
        # Test None input
        result = KospexEmail.get_email_type(None)
        assert result['username'] is None
        assert result['domain_name'] is None
        
        # Test email without @ symbol
        result = KospexEmail.get_email_type("notanemail")
        assert result['username'] == "notanemail"
        assert result['domain_name'] == ""
        assert result['domain_type'] == "unknown"

    def test_github_email_detection(self):
        """Test GitHub email detection and handle extraction."""
        # GitHub noreply email with handle
        result = KospexEmail.get_email_type("12345678+username@users.noreply.github.com")
        assert result['domain_type'] == "github"
        assert result['is_github_noreply'] is True
        assert result['is_noreply'] is True
        assert result['github_handle'] == "username"
        assert result['is_bot'] is False
        
        # Regular GitHub email
        result = KospexEmail.get_email_type("user@github.com")
        assert result['domain_type'] == "github"
        assert result['is_github_noreply'] is False
        assert result['github_handle'] is None

    def test_dependabot_detection(self):
        """Test detection of Dependabot emails."""
        test_cases = [
            "49699333+dependabot[bot]@users.noreply.github.com",
            "123456+dependabot@users.noreply.github.com",
            "dependabot@example.com"
        ]
        
        for email in test_cases:
            result = KospexEmail.get_email_type(email)
            assert result['is_bot'] is True
            assert result['bot_type'] == "dependabot"

    def test_github_actions_detection(self):
        """Test detection of GitHub Actions bot emails."""
        test_cases = [
            "41898282+github-actions[bot]@users.noreply.github.com",
            "github-actions@example.com",
            "actions-user@domain.com"
        ]
        
        for email in test_cases:
            result = KospexEmail.get_email_type(email)
            assert result['is_bot'] is True
            assert result['bot_type'] == "github-actions"

    def test_renovate_bot_detection(self):
        """Test detection of Renovate bot emails."""
        test_cases = [
            "renovate@example.com",
            "renovatebot@domain.com",
            "29139614+renovate[bot]@users.noreply.github.com"
        ]
        
        for email in test_cases:
            result = KospexEmail.get_email_type(email)
            assert result['is_bot'] is True
            assert result['bot_type'] == "renovatebot"

    def test_exact_bot_email_matches(self):
        """Test exact bot email matches."""
        exact_matches = {
            'noreply@github.com': 'github-system',
            'support@github.com': 'github-support',
            'security@github.com': 'github-security',
            'web-flow@github.com': 'github-web-flow',
            'merge@github.com': 'github-merge'
        }
        
        for email, expected_bot_type in exact_matches.items():
            result = KospexEmail.get_email_type(email)
            assert result['is_bot'] is True
            assert result['bot_type'] == expected_bot_type

    def test_ci_cd_bot_detection(self):
        """Test detection of CI/CD related bots."""
        test_cases = [
            ("jenkins@example.com", "jenkins"),
            ("travis@domain.com", "travis-ci"),
            ("circleci@test.com", "circleci"),
            ("gitlab-ci@gitlab.com", "gitlab-ci"),
            ("azure-devops@microsoft.com", "azure-devops")
        ]
        
        for email, expected_bot_type in test_cases:
            result = KospexEmail.get_email_type(email)
            assert result['is_bot'] is True
            assert result['bot_type'] == expected_bot_type

    def test_code_quality_bot_detection(self):
        """Test detection of code quality bots."""
        test_cases = [
            ("codecov@example.com", "codecov"),
            ("sonarcloud@domain.com", "sonarcloud"),
            ("codacy@test.com", "codacy"),
            ("deepsource@example.com", "deepsource")
        ]
        
        for email, expected_bot_type in test_cases:
            result = KospexEmail.get_email_type(email)
            assert result['is_bot'] is True
            assert result['bot_type'] == expected_bot_type

    def test_generic_bot_indicators(self):
        """Test detection of generic bot indicators."""
        test_cases = [
            ("automation@example.com", "automation-bot"),
            ("deploy@domain.com", "deploy-bot"),
            ("build@test.com", "build-bot"),
            ("service@example.com", "service-bot"),
            ("system@domain.com", "system-bot"),
            ("robot@test.com", "robot-bot"),
            ("script@example.com", "script-bot"),
            ("pipeline@domain.com", "pipeline-bot")
        ]
        
        for email, expected_bot_type in test_cases:
            result = KospexEmail.get_email_type(email)
            assert result['is_bot'] is True
            assert result['bot_type'] == expected_bot_type

    def test_numeric_account_detection(self):
        """Test detection of numeric-only accounts as bots."""
        test_cases = [
            "123456@example.com",
            "987654321@domain.com",
            "42@test.com"
        ]
        
        for email in test_cases:
            result = KospexEmail.get_email_type(email)
            assert result['is_bot'] is True
            assert result['bot_type'] == "numeric-account"

    def test_domain_type_classification(self):
        """Test domain type classification."""
        test_cases = [
            ("user@gmail.com", "personal"),
            ("test@yahoo.com", "personal"), 
            ("email@hotmail.com", "personal"),
            ("person@outlook.com", "personal"),
            ("user@icloud.com", "personal"),
            ("student@university.edu", "academic"),
            ("official@agency.gov", "government"),
            ("contact@nonprofit.org", "organization"),
            ("noreply@company.com", "noreply"),
            ("no-reply@service.com", "noreply"),
            ("employee@company.com", "corporate")
        ]
        
        for email, expected_domain_type in test_cases:
            result = KospexEmail.get_email_type(email)
            assert result['domain_type'] == expected_domain_type

    def test_noreply_detection(self):
        """Test detection of noreply email addresses."""
        test_cases = [
            "noreply@example.com",
            "no-reply@domain.com",
            "donotreply@test.com",
            "user@noreply.github.com",
            "test@no-reply-service.com"
        ]
        
        for email in test_cases:
            result = KospexEmail.get_email_type(email)
            assert result['is_noreply'] is True

    def test_case_insensitive_detection(self):
        """Test that bot detection is case insensitive."""
        test_cases = [
            "DEPENDABOT@EXAMPLE.COM",
            "GitHub-Actions@DOMAIN.COM",
            "RENOVATE@test.com",
            "Jenkins@COMPANY.COM"
        ]
        
        for email in test_cases:
            result = KospexEmail.get_email_type(email)
            assert result['is_bot'] is True

    def test_complex_github_emails(self):
        """Test complex GitHub email patterns."""
        # GitHub Actions with specific format
        result = KospexEmail.get_email_type("41898282+github-actions[bot]@users.noreply.github.com")
        assert result['is_bot'] is True
        assert result['bot_type'] == "github-actions"
        assert result['github_handle'] == "github-actions[bot]"
        assert result['is_github_noreply'] is True
        
        # Dependabot with GitHub format
        result = KospexEmail.get_email_type("49699333+dependabot[bot]@users.noreply.github.com")
        assert result['is_bot'] is True
        assert result['bot_type'] == "dependabot"
        assert result['github_handle'] == "dependabot[bot]"

    def test_human_developers(self):
        """Test that human developers are not flagged as bots."""
        human_emails = [
            "john.doe@company.com",
            "developer@startup.io",
            "engineer@bigtech.com",
            "contributor@opensource.org",
            "maintainer@project.dev",
            "12345678+realuser@users.noreply.github.com"
        ]
        
        for email in human_emails:
            result = KospexEmail.get_email_type(email)
            # The last one (GitHub noreply) should not be detected as bot
            # unless the username itself indicates it's a bot
            if "realuser" in email:
                assert result['is_bot'] is False
                assert result['github_handle'] == "realuser"
            else:
                assert result['is_bot'] is False

    def test_edge_cases(self):
        """Test edge cases and special scenarios."""
        # Email with multiple @ symbols (malformed)
        result = KospexEmail.get_email_type("user@domain@example.com")
        assert result['username'] == "user"
        assert result['domain_name'] == "domain@example.com"
        
        # Email with special characters
        result = KospexEmail.get_email_type("user+tag@example.com")
        assert result['username'] == "user+tag"
        assert result['domain_name'] == "example.com"
        
        # Very long email
        long_email = "verylongusername@verylongdomainname.com"
        result = KospexEmail.get_email_type(long_email)
        assert result['username'] == "verylongusername"
        assert result['domain_name'] == "verylongdomainname.com"

    def test_security_related_bots(self):
        """Test detection of security-related bots."""
        test_cases = [
            ("security@example.com", "security-bot"),
            ("vulnerability@domain.com", "vulnerability-bot"),
            ("snyk-bot@test.com", "snyk-bot")
        ]
        
        for email, expected_bot_type in test_cases:
            result = KospexEmail.get_email_type(email)
            assert result['is_bot'] is True
            assert result['bot_type'] == expected_bot_type

    def test_documentation_bots(self):
        """Test detection of documentation-related bots."""
        test_cases = [
            ("docs@example.com", "docs-bot"),
            ("wiki@domain.com", "wiki-bot")
        ]
        
        for email, expected_bot_type in test_cases:
            result = KospexEmail.get_email_type(email)
            assert result['is_bot'] is True
            assert result['bot_type'] == expected_bot_type

    def test_return_value_structure(self):
        """Test that return value has correct structure."""
        result = KospexEmail.get_email_type("test@example.com")
        
        # Check all required keys are present
        required_keys = [
            'username', 'domain_name', 'github_handle', 'is_bot',
            'bot_type', 'is_noreply', 'is_github_noreply', 'domain_type'
        ]
        
        for key in required_keys:
            assert key in result
        
        # Check data types
        assert isinstance(result['username'], (str, type(None)))
        assert isinstance(result['domain_name'], (str, type(None)))
        assert isinstance(result['github_handle'], (str, type(None)))
        assert isinstance(result['is_bot'], bool)
        assert isinstance(result['bot_type'], (str, type(None)))
        assert isinstance(result['is_noreply'], bool)
        assert isinstance(result['is_github_noreply'], bool)
        assert isinstance(result['domain_type'], str)

    @pytest.mark.parametrize("email,expected_bot", [
        ("dependabot@example.com", True),
        ("renovate@example.com", True),
        ("github-actions@example.com", True),
        ("human.developer@company.com", False),
        ("jenkins@ci.com", True),
        ("codecov@quality.com", True),
        ("12345678+realuser@users.noreply.github.com", False),
        ("noreply@github.com", True)
    ])
    def test_parametrized_bot_detection(self, email, expected_bot):
        """Parametrized test for bot detection."""
        result = KospexEmail.get_email_type(email)
        assert result['is_bot'] == expected_bot

    @pytest.mark.parametrize("email,expected_domain_type", [
        ("user@gmail.com", "personal"),
        ("test@company.com", "corporate"),
        ("student@university.edu", "academic"),
        ("official@gov.gov", "government"),
        ("contact@nonprofit.org", "organization"),
        ("12345+user@users.noreply.github.com", "github"),
        ("noreply@service.com", "noreply")
    ])
    def test_parametrized_domain_classification(self, email, expected_domain_type):
        """Parametrized test for domain classification."""
        result = KospexEmail.get_email_type(email)
        assert result['domain_type'] == expected_domain_type


if __name__ == "__main__":
    # Allow running this file directly
    pytest.main([__file__, "-v"])