"""cosalette.

An opinionated Python framework for building IoT-to-MQTT bridge applications.
"""

from importlib.metadata import PackageNotFoundError, version

from cosalette._clock import ClockPort, SystemClock
from cosalette._logging import JsonFormatter, configure_logging
from cosalette._settings import LoggingSettings, MqttSettings, Settings

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

__all__ = [
    # Version
    "__version__",
    # Clock
    "ClockPort",
    "SystemClock",
    # Logging
    "JsonFormatter",
    "configure_logging",
    # Settings
    "LoggingSettings",
    "MqttSettings",
    "Settings",
]
