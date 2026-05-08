"""cosalette.

An opinionated Python framework for building IoT-to-MQTT bridge applications.
"""

from importlib.metadata import PackageNotFoundError, version

from cosalette._app import App, LifespanFunc
from cosalette._clock import ClockPort, SystemClock
from cosalette._command import Command
from cosalette._context import AppContext, DeviceContext, SubEntityContext
from cosalette._cron import CronSchedule
from cosalette._errors import ErrorPayload, ErrorPublisher, build_error_payload
from cosalette._health import (
    AdapterHealthStatus,
    DeviceStatus,
    HealthCheckable,
    HealthReporter,
    HeartbeatPayload,
    build_will_config,
)
from cosalette._introspect import (
    build_registry_snapshot,
    format_registry_json,
    format_registry_table,
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
from cosalette._persistence._persist import (
    AllSavePolicy,
    AnySavePolicy,
    PersistPolicy,
    SaveOnChange,
    SaveOnPublish,
    SaveOnShutdown,
)
from cosalette._persistence._stores import (
    DeviceStore,
    JsonFileStore,
    MemoryStore,
    NullStore,
    SqliteStore,
    Store,
)
from cosalette._registration import CronSpec, EnabledSpec, IntervalSpec, NameSpec
from cosalette._retry import (
    BackoffStrategy,
    CircuitBreaker,
    ExponentialBackoff,
    FixedBackoff,
    LinearBackoff,
)
from cosalette._router import Router
from cosalette._runners._trigger import TriggerPayload
from cosalette._settings import LoggingSettings, MqttSettings, Settings
from cosalette._settings._ref import SettingRef, setting_ref
from cosalette._strategies import (
    AllStrategy,
    AnyStrategy,
    Every,
    OnChange,
    PublishStrategy,
)

# Streaming
from cosalette._stream import BackpressurePolicy, Stream, StreamablePort
from cosalette.filters import Filter, MedianFilter, OneEuroFilter, Pt1Filter

try:
    # Prefer the generated version file (setuptools_scm at build time)
    from cosalette._version import __version__ as _v

    __version__: str = _v
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
    "Command",
    "CronSchedule",
    "CronSpec",
    "DeviceContext",
    "SubEntityContext",
    "EnabledSpec",
    "IntervalSpec",
    "LifespanFunc",
    "NameSpec",
    "Router",
    "TriggerPayload",
    # Introspection
    "build_registry_snapshot",
    "format_registry_json",
    "format_registry_table",
    # Clock
    "ClockPort",
    "SystemClock",
    # Logging
    "JsonFormatter",
    "configure_logging",
    # MQTT
    "MessageCallback",
    # MockMqttClient is intentionally in the production namespace — it's a
    # first-class API for downstream projects to simplify their test setup
    # without needing to import from cosalette.testing.
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
    "AdapterHealthStatus",
    "DeviceStatus",
    "HealthCheckable",
    "HeartbeatPayload",
    "HealthReporter",
    "build_will_config",
    # Settings
    "LoggingSettings",
    "MqttSettings",
    "Settings",
    "SettingRef",
    "setting_ref",
    # Strategies
    "AllStrategy",
    "AnyStrategy",
    "Every",
    "OnChange",
    "PublishStrategy",
    # Retry / Backoff
    "BackoffStrategy",
    "CircuitBreaker",
    "ExponentialBackoff",
    "FixedBackoff",
    "LinearBackoff",
    # Persist
    "AllSavePolicy",
    "AnySavePolicy",
    "PersistPolicy",
    "SaveOnChange",
    "SaveOnPublish",
    "SaveOnShutdown",
    # Filters
    "Filter",
    "MedianFilter",
    "OneEuroFilter",
    "Pt1Filter",
    # Stores
    "DeviceStore",
    "JsonFileStore",
    "MemoryStore",
    "NullStore",
    "SqliteStore",
    "Store",
    # Streaming
    "BackpressurePolicy",
    "Stream",
    "StreamablePort",
]
