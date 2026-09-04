"""Smoke tests for cosalette package structure.

Test Techniques Used:
- Specification-based: Verify package imports and version metadata exist.
- Error Guessing: Two version sources drifting apart (cos-cmbm).
"""

from importlib.metadata import version

import pytest

import cosalette

pytestmark = pytest.mark.unit


class TestPackageStructure:
    """Verify the cosalette package is properly installed and importable."""

    def test_package_importable(self) -> None:
        """Package can be imported without error and exposes key public symbols.

        Technique: Specification-based — verifying the package contract.
        """
        assert hasattr(cosalette, "App")
        assert hasattr(cosalette, "Router")

    def test_version_is_string(self) -> None:
        """Package exposes a version string.

        Technique: Specification-based — verifying version metadata contract.
        """
        assert isinstance(cosalette.__version__, str)
        assert len(cosalette.__version__) > 0

    def test_version_matches_distribution_metadata(self) -> None:
        """``__version__`` is the installed distribution version, not a second source.

        Technique: Error Guessing — the generated ``_version.py`` used to shadow
        distribution metadata, so the two APIs reported different versions for the
        same process (cos-cmbm).
        """
        # Arrange
        exported = cosalette.__version__

        # Act
        metadata_version = version("cosalette")

        # Assert
        assert exported == metadata_version
