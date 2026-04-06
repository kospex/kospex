"""Assessment type definitions and filename generation for kospex.

This module defines standardized assessment type keys and provides utilities
for generating filenames and paths for assessment outputs.

Assessment files follow the naming convention: {KEY}-{scope}.csv
where KEY is the assessment type (e.g., OSI) and scope identifies
the coverage (e.g., "all", "github.com", "github.com~kospex").

Usage:
    from kospex.assessment_types import AssessmentTypes

    # Generate filename
    filename = AssessmentTypes.generate_filename(AssessmentTypes.OSI, "all")
    # Returns: "OSI-all.csv"

    # Get full path in assessments directory
    path = AssessmentTypes.get_assessments_path(AssessmentTypes.OSI, "github.com")
    # Returns: ~/kospex/assessments/OSI-github.com.csv
"""

from pathlib import Path

from kospex.habitat_config import HabitatConfig


class AssessmentTypes:
    """Defines assessment type keys and provides filename utilities.

    This class provides constants for assessment type keys and utility
    methods for generating standardized filenames and paths.

    Assessment Types:
        OSI: Open Source Inventory - lists all open source dependencies
             with version information and security advisories.

    Future assessment types can be added as class constants.
    """

    # Assessment type keys
    OSI = "OSI"  # Open Source Inventory
    TECH_LANDSCAPE = "TECH-LANDSCAPE"  # Organization/repo tech landscape (by language)
    TECH_LANDSCAPE_DEV = "TECH-LANDSCAPE-DEV"  # Developer-specific tech landscape (by author + language)

    # Future assessment types can be added here:
    # KEY_PERSON = "KEY-PERSON"
    # DEPENDENCIES = "DEPENDENCIES"

    @staticmethod
    def generate_filename(key: str, scope: str = "all") -> str:
        """Generate assessment filename.

        Creates a standardized filename for assessment outputs following
        the convention: {KEY}-{scope}.csv

        Args:
            key: Assessment type key (e.g., "OSI")
            scope: Scope identifier (default: "all")
                   Examples: "all", "github.com", "github.com~kospex"

        Returns:
            Filename in format: {KEY}-{scope}.csv

        Examples:
            >>> AssessmentTypes.generate_filename("OSI")
            'OSI-all.csv'
            >>> AssessmentTypes.generate_filename("OSI", "github.com")
            'OSI-github.com.csv'
            >>> AssessmentTypes.generate_filename("OSI", "github.com~kospex")
            'OSI-github.com~kospex.csv'
        """
        return f"{key}-{scope}.csv"

    @staticmethod
    def get_assessments_path(key: str, scope: str = "all") -> Path:
        """Get full path to assessment file in assessments directory.

        Combines the assessments directory from HabitatConfig with
        a generated filename to produce the full output path.

        Args:
            key: Assessment type key (e.g., "OSI")
            scope: Scope identifier (default: "all")

        Returns:
            Full Path to assessment file in assessments_dir

        Examples:
            >>> path = AssessmentTypes.get_assessments_path("OSI", "all")
            >>> str(path)  # e.g., '/Users/user/kospex/assessments/OSI-all.csv'
        """
        config = HabitatConfig.get_instance()
        filename = AssessmentTypes.generate_filename(key, scope)
        return config.assessments_dir / filename

    @staticmethod
    def ensure_assessments_dir() -> Path:
        """Ensure the assessments directory exists.

        Creates the assessments directory if it doesn't exist,
        using HabitatConfig's ensure_directories functionality.

        Returns:
            Path to the assessments directory
        """
        config = HabitatConfig.get_instance()
        config.ensure_directories()
        return config.assessments_dir
