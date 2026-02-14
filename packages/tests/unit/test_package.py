"""Smoke tests for cosalette package structure.

Test Techniques Used:
- Specification-based: Verify package imports and version metadata exist.
"""

import cosalette


class TestPackageStructure:
    """Verify the cosalette package is properly installed and importable."""

    def test_package_importable(self) -> None:
        """Package can be imported without error.

        Technique: Specification-based — verifying the package contract.
        """
        assert cosalette is not None

    def test_version_is_string(self) -> None:
        """Package exposes a version string.

        Technique: Specification-based — verifying version metadata contract.
        """
        assert isinstance(cosalette.__version__, str)
        assert len(cosalette.__version__) > 0
