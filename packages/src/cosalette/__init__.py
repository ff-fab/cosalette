"""cosalette

an opinionated Python framework for building IoT-to-MQTT bridge applications
"""

from importlib import import_module
from importlib.metadata import PackageNotFoundError, version

try:
    # Try to get version from generated version file
    # (updated by setuptools_scm at build time)
    from cosalette._version import __version__
except ImportError:
    try:
        # Fallback to installed package metadata
        from importlib.metadata import version

        __version__ = version("{{ _copier_answers.module_name }}")
    except Exception:
        # Last resort fallback
        __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
