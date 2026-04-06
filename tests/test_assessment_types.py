"""Tests for assessment_types module.

Tests for AssessmentTypes class including:
- Assessment type key definitions
- Filename generation
- Path generation with HabitatConfig integration
"""

import tempfile
from pathlib import Path

import pytest

from kospex.assessment_types import AssessmentTypes
from kospex.habitat_config import HabitatConfig


class TestAssessmentTypesKeys:
    """Tests for assessment type key definitions."""

    def test_osi_key_defined(self):
        """Test OSI key is defined."""
        assert AssessmentTypes.OSI == "OSI"

    def test_osi_key_is_string(self):
        """Test OSI key is a string."""
        assert isinstance(AssessmentTypes.OSI, str)

    def test_tech_landscape_key_defined(self):
        """Test TECH_LANDSCAPE key is defined."""
        assert AssessmentTypes.TECH_LANDSCAPE == "TECH-LANDSCAPE"

    def test_tech_landscape_dev_key_defined(self):
        """Test TECH_LANDSCAPE_DEV key is defined."""
        assert AssessmentTypes.TECH_LANDSCAPE_DEV == "TECH-LANDSCAPE-DEV"


class TestAssessmentTypesGenerateFilename:
    """Tests for generate_filename static method."""

    def test_generate_filename_default_scope(self):
        """Test filename generation with default scope."""
        filename = AssessmentTypes.generate_filename("OSI")
        assert filename == "OSI-all.csv"

    def test_generate_filename_custom_scope(self):
        """Test filename generation with custom scope."""
        filename = AssessmentTypes.generate_filename("OSI", "github.com")
        assert filename == "OSI-github.com.csv"

    def test_generate_filename_org_scope(self):
        """Test filename generation with org scope."""
        filename = AssessmentTypes.generate_filename("OSI", "github.com~kospex")
        assert filename == "OSI-github.com~kospex.csv"

    def test_generate_filename_repo_scope(self):
        """Test filename generation with full repo scope."""
        filename = AssessmentTypes.generate_filename("OSI", "github.com~kospex~kospex")
        assert filename == "OSI-github.com~kospex~kospex.csv"

    def test_generate_filename_uses_key_constant(self):
        """Test filename generation using the OSI constant."""
        filename = AssessmentTypes.generate_filename(AssessmentTypes.OSI)
        assert filename == "OSI-all.csv"

    def test_generate_filename_different_key(self):
        """Test filename generation with a different key."""
        filename = AssessmentTypes.generate_filename("KEY-PERSON", "all")
        assert filename == "KEY-PERSON-all.csv"

    def test_generate_filename_tech_landscape(self):
        """Test filename generation for TECH_LANDSCAPE."""
        filename = AssessmentTypes.generate_filename(AssessmentTypes.TECH_LANDSCAPE)
        assert filename == "TECH-LANDSCAPE-all.csv"

        filename_with_year = AssessmentTypes.generate_filename(
            AssessmentTypes.TECH_LANDSCAPE, "all-2024"
        )
        assert filename_with_year == "TECH-LANDSCAPE-all-2024.csv"

    def test_generate_filename_tech_landscape_dev(self):
        """Test filename generation for TECH_LANDSCAPE_DEV."""
        filename = AssessmentTypes.generate_filename(AssessmentTypes.TECH_LANDSCAPE_DEV)
        assert filename == "TECH-LANDSCAPE-DEV-all.csv"

        filename_with_year = AssessmentTypes.generate_filename(
            AssessmentTypes.TECH_LANDSCAPE_DEV, "all-2024"
        )
        assert filename_with_year == "TECH-LANDSCAPE-DEV-all-2024.csv"


class TestAssessmentTypesGetAssessmentsPath:
    """Tests for get_assessments_path static method."""

    def setup_method(self):
        """Reset singleton before each test."""
        HabitatConfig.reset_instance()

    def teardown_method(self):
        """Clean up after each test."""
        HabitatConfig.reset_instance()

    def test_get_assessments_path_default_scope(self):
        """Test getting full assessment path with default scope."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = HabitatConfig.get_instance()
            with config.with_overrides(KOSPEX_HOME=tmpdir):
                path = AssessmentTypes.get_assessments_path("OSI")
                expected = Path(tmpdir) / "assessments" / "OSI-all.csv"
                assert path == expected

    def test_get_assessments_path_custom_scope(self):
        """Test getting full assessment path with custom scope."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = HabitatConfig.get_instance()
            with config.with_overrides(KOSPEX_HOME=tmpdir):
                path = AssessmentTypes.get_assessments_path("OSI", "github.com")
                expected = Path(tmpdir) / "assessments" / "OSI-github.com.csv"
                assert path == expected

    def test_get_assessments_path_org_scope(self):
        """Test getting full assessment path with org scope."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = HabitatConfig.get_instance()
            with config.with_overrides(KOSPEX_HOME=tmpdir):
                path = AssessmentTypes.get_assessments_path("OSI", "github.com~kospex")
                expected = Path(tmpdir) / "assessments" / "OSI-github.com~kospex.csv"
                assert path == expected

    def test_get_assessments_path_returns_path_object(self):
        """Test that get_assessments_path returns a Path object."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = HabitatConfig.get_instance()
            with config.with_overrides(KOSPEX_HOME=tmpdir):
                path = AssessmentTypes.get_assessments_path("OSI", "all")
                assert isinstance(path, Path)

    def test_get_assessments_path_with_osi_constant(self):
        """Test getting assessment path using the OSI constant."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = HabitatConfig.get_instance()
            with config.with_overrides(KOSPEX_HOME=tmpdir):
                path = AssessmentTypes.get_assessments_path(AssessmentTypes.OSI, "all")
                expected = Path(tmpdir) / "assessments" / "OSI-all.csv"
                assert path == expected


class TestAssessmentTypesEnsureAssessmentsDir:
    """Tests for ensure_assessments_dir static method."""

    def setup_method(self):
        """Reset singleton before each test."""
        HabitatConfig.reset_instance()

    def teardown_method(self):
        """Clean up after each test."""
        HabitatConfig.reset_instance()

    def test_ensure_assessments_dir_creates_directory(self):
        """Test that ensure_assessments_dir creates the directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            new_home = Path(tmpdir) / "kospex_test"
            config = HabitatConfig.get_instance()
            with config.with_overrides(KOSPEX_HOME=str(new_home)):
                assessments_path = AssessmentTypes.ensure_assessments_dir()
                assert assessments_path.exists()
                assert assessments_path.is_dir()

    def test_ensure_assessments_dir_returns_path(self):
        """Test that ensure_assessments_dir returns the assessments path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            new_home = Path(tmpdir) / "kospex_test"
            config = HabitatConfig.get_instance()
            with config.with_overrides(KOSPEX_HOME=str(new_home)):
                assessments_path = AssessmentTypes.ensure_assessments_dir()
                expected = new_home / "assessments"
                assert assessments_path == expected

    def test_ensure_assessments_dir_idempotent(self):
        """Test that ensure_assessments_dir can be called multiple times."""
        with tempfile.TemporaryDirectory() as tmpdir:
            new_home = Path(tmpdir) / "kospex_test"
            config = HabitatConfig.get_instance()
            with config.with_overrides(KOSPEX_HOME=str(new_home)):
                # Call multiple times - should not raise
                path1 = AssessmentTypes.ensure_assessments_dir()
                path2 = AssessmentTypes.ensure_assessments_dir()
                assert path1 == path2
                assert path1.exists()


class TestAssessmentTypesIntegration:
    """Integration tests combining multiple AssessmentTypes methods."""

    def setup_method(self):
        """Reset singleton before each test."""
        HabitatConfig.reset_instance()

    def teardown_method(self):
        """Clean up after each test."""
        HabitatConfig.reset_instance()

    def test_full_workflow_create_and_verify_path(self):
        """Test full workflow of ensuring directory and getting path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            new_home = Path(tmpdir) / "kospex_test"
            config = HabitatConfig.get_instance()
            with config.with_overrides(KOSPEX_HOME=str(new_home)):
                # Ensure directory exists
                AssessmentTypes.ensure_assessments_dir()

                # Get path for assessment
                path = AssessmentTypes.get_assessments_path(AssessmentTypes.OSI, "all")

                # Verify path is in the assessments directory
                assert path.parent.exists()
                assert path.parent.name == "assessments"
                assert path.name == "OSI-all.csv"

    def test_consistent_filename_generation(self):
        """Test that generate_filename and get_assessments_path use same naming."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = HabitatConfig.get_instance()
            with config.with_overrides(KOSPEX_HOME=tmpdir):
                scope = "github.com~kospex"
                filename = AssessmentTypes.generate_filename(AssessmentTypes.OSI, scope)
                full_path = AssessmentTypes.get_assessments_path(AssessmentTypes.OSI, scope)

                # The filename from full_path should match generated filename
                assert full_path.name == filename
