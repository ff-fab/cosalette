"""cosalette.

An opinionated Python framework for building IoT-to-MQTT bridge applications.
"""

from importlib.metadata import PackageNotFoundError, version

from cosalette._app import App, LifespanFunc
from cosalette._clock import ClockPort, SystemClock
from cosalette._context import AppContext, DeviceContext
from cosalette._errors import ErrorPayload, ErrorPublisher, build_error_payload
from cosalette._health import (
    DeviceStatus,
    HealthReporter,
    HeartbeatPayload,
    build_will_config,
)
from cosalette._logging import JsonFormatter, configure_logging
from cosalette._mqtt import (
    MessageCallback,
    MockMqttClient,
    MqttClient,
    MqttLifecycle,
    MqttMessageHandler,
    MqttPort,
    NullMqttClient,
    WillConfig,
)
from cosalette._settings import LoggingSettings, MqttSettings, Settings
from cosalette._strategies import Every, OnChange, PublishStrategy

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
    # App
    "App",
    "AppContext",
    "DeviceContext",
    "LifespanFunc",
    # Clock
    "ClockPort",
    "SystemClock",
    # Logging
    "JsonFormatter",
    "configure_logging",
    # MQTT
    "MessageCallback",
    "MockMqttClient",
    "MqttClient",
    "MqttLifecycle",
    "MqttMessageHandler",
    "MqttPort",
    "NullMqttClient",
    "WillConfig",
    # Errors
    "ErrorPayload",
    "ErrorPublisher",
    "build_error_payload",
    # Health
    "DeviceStatus",
    "HeartbeatPayload",
    "HealthReporter",
    "build_will_config",
    # Settings
    "LoggingSettings",
    "MqttSettings",
    "Settings",
    # Strategies
    "Every",
    "OnChange",
    "PublishStrategy",
]
