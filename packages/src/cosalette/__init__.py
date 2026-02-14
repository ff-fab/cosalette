"""cosalette.

An opinionated Python framework for building IoT-to-MQTT bridge applications.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    # Prefer the generated version file (setuptools_scm at build time)
    from cosalette._version import __version__
except ImportError:
    try:
        # Fallback to installed package metadata
        __version__ = version("cosalette")
    except PackageNotFoundError:
        # Last resort fallback for editable installs without metadata
        __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
