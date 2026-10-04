"""cosalette.

An opinionated Python framework for building IoT-to-MQTT bridge applications.

Public names are imported lazily on first access (PEP 562), so ``import
cosalette`` stays cheap for short-lived processes such as the ``health`` probe.
"""

from typing import TYPE_CHECKING

from cosalette._lazy import lazy_exports

if TYPE_CHECKING:
    from cosalette._app import App, LifespanFunc
    from cosalette._app._store_defaults import set_default_store_backend
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
    from cosalette._health._liveness import StaleTelemetryError
    from cosalette._logging import JsonFormatter, configure_logging
    from cosalette._mcp._introspect import (
        build_registry_snapshot,
        format_registry_json,
        format_registry_table,
    )
    from cosalette._mqtt import (
        MessageCallback,
        MockMqttClient,
        MqttClient,
        MqttLifecycle,
        MqttMessageHandler,
        MqttNotConnectedError,
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
    from cosalette._persistence._state import StateRegistration
    from cosalette._persistence._stores import (
        DeviceStore,
        JsonFileStore,
        MemoryStore,
        NullStore,
        SqliteStore,
        Store,
    )
    from cosalette._registration import (
        CommandRegistration,
        CronSpec,
        DeviceRegistration,
        EnabledSpec,
        IntervalSpec,
        NameSpec,
        StreamRegistration,
        TelemetryRegistration,
        TimeoutSpec,
    )
    from cosalette._retry import (
        BackoffStrategy,
        CircuitBreaker,
        ExponentialBackoff,
        FixedBackoff,
        LinearBackoff,
    )
    from cosalette._router import Router
    from cosalette._runners._contracts import (
        PayloadValidationError,
        ReturnValidationError,
    )
    from cosalette._runners._device_trigger import DeviceTrigger
    from cosalette._runners._notifier import (
        EntityNotifier,
        EntityNotifierError,
        NotifierNotReadyError,
        UnknownEntityError,
    )
    from cosalette._runners._periodic import PeriodicRegistration

    # Streaming
    from cosalette._runners._stream_types import (
        BackpressurePolicy,
        Stream,
        StreamablePort,
    )
    from cosalette._runners._trigger import (
        TriggerableSpec,
        TriggerPayload,
        TriggerRunSource,
        TriggerSource,
    )
    from cosalette._settings import LoggingSettings, MqttSettings, Settings
    from cosalette._settings._config_file import SettingsLoadError
    from cosalette._settings._ref import SettingRef, setting_ref
    from cosalette._strategies import (
        AllStrategy,
        AnyStrategy,
        Every,
        OnChange,
        PublishStrategy,
    )
    from cosalette._supervisor import TaskSupervisionError
    from cosalette.di import Depends, Optional
    from cosalette.filters import Filter, MedianFilter, OneEuroFilter, Pt1Filter
    from cosalette.mqtt import Message, Payload, Topic

    __version__: str

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
    "TimeoutSpec",
    "TriggerPayload",
    "TriggerRunSource",
    "TriggerSource",
    "TriggerableSpec",
    # Local trigger source (ADR-064, ADR-065)
    "DeviceTrigger",
    "EntityNotifier",
    "EntityNotifierError",
    "NotifierNotReadyError",
    "UnknownEntityError",
    # Registration types
    "CommandRegistration",
    "DeviceRegistration",
    "PeriodicRegistration",
    "StateRegistration",
    "StreamRegistration",
    "TelemetryRegistration",
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
    "MqttNotConnectedError",
    "MqttPort",
    "NullMqttClient",
    "WillConfig",
    # Errors
    "ErrorPayload",
    "ErrorPublisher",
    "build_error_payload",
    # Task supervision (ADR-081)
    "TaskSupervisionError",
    # Health file and exit_after_stale (ADR-083)
    "StaleTelemetryError",
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
    "SettingsLoadError",
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
    "set_default_store_backend",
    "SqliteStore",
    "Store",
    # Streaming
    "BackpressurePolicy",
    "Stream",
    "StreamablePort",
    # Typed handler contracts (ADR-046)
    "Depends",
    "Optional",
    "Message",
    "Payload",
    "PayloadValidationError",
    "ReturnValidationError",
    "Topic",
]

# Mirrors the TYPE_CHECKING imports above; test_lazy_exports.py keeps them in sync.
_EXPORTS: dict[str, tuple[str, ...]] = {
    "cosalette._app": ("App", "LifespanFunc"),
    "cosalette._app._store_defaults": ("set_default_store_backend",),
    "cosalette._clock": ("ClockPort", "SystemClock"),
    "cosalette._command": ("Command",),
    "cosalette._context": ("AppContext", "DeviceContext", "SubEntityContext"),
    "cosalette._cron": ("CronSchedule",),
    "cosalette._errors": ("ErrorPayload", "ErrorPublisher", "build_error_payload"),
    "cosalette._health": (
        "AdapterHealthStatus",
        "DeviceStatus",
        "HealthCheckable",
        "HealthReporter",
        "HeartbeatPayload",
        "build_will_config",
    ),
    "cosalette._health._liveness": ("StaleTelemetryError",),
    "cosalette._logging": ("JsonFormatter", "configure_logging"),
    "cosalette._mcp._introspect": (
        "build_registry_snapshot",
        "format_registry_json",
        "format_registry_table",
    ),
    "cosalette._mqtt": (
        "MessageCallback",
        "MockMqttClient",
        "MqttClient",
        "MqttLifecycle",
        "MqttMessageHandler",
        "MqttNotConnectedError",
        "MqttPort",
        "NullMqttClient",
        "WillConfig",
    ),
    "cosalette._persistence._persist": (
        "AllSavePolicy",
        "AnySavePolicy",
        "PersistPolicy",
        "SaveOnChange",
        "SaveOnPublish",
        "SaveOnShutdown",
    ),
    "cosalette._persistence._state": ("StateRegistration",),
    "cosalette._persistence._stores": (
        "DeviceStore",
        "JsonFileStore",
        "MemoryStore",
        "NullStore",
        "SqliteStore",
        "Store",
    ),
    "cosalette._registration": (
        "CommandRegistration",
        "CronSpec",
        "DeviceRegistration",
        "EnabledSpec",
        "IntervalSpec",
        "NameSpec",
        "StreamRegistration",
        "TelemetryRegistration",
        "TimeoutSpec",
    ),
    "cosalette._retry": (
        "BackoffStrategy",
        "CircuitBreaker",
        "ExponentialBackoff",
        "FixedBackoff",
        "LinearBackoff",
    ),
    "cosalette._router": ("Router",),
    "cosalette._runners._contracts": (
        "PayloadValidationError",
        "ReturnValidationError",
    ),
    "cosalette._runners._device_trigger": ("DeviceTrigger",),
    "cosalette._runners._notifier": (
        "EntityNotifier",
        "EntityNotifierError",
        "NotifierNotReadyError",
        "UnknownEntityError",
    ),
    "cosalette._runners._periodic": ("PeriodicRegistration",),
    "cosalette._runners._stream_types": (
        "BackpressurePolicy",
        "Stream",
        "StreamablePort",
    ),
    "cosalette._runners._trigger": (
        "TriggerableSpec",
        "TriggerPayload",
        "TriggerRunSource",
        "TriggerSource",
    ),
    "cosalette._settings": ("LoggingSettings", "MqttSettings", "Settings"),
    "cosalette._settings._config_file": ("SettingsLoadError",),
    "cosalette._settings._ref": ("SettingRef", "setting_ref"),
    "cosalette._strategies": (
        "AllStrategy",
        "AnyStrategy",
        "Every",
        "OnChange",
        "PublishStrategy",
    ),
    "cosalette._supervisor": ("TaskSupervisionError",),
    "cosalette.di": ("Depends", "Optional"),
    "cosalette.filters": ("Filter", "MedianFilter", "OneEuroFilter", "Pt1Filter"),
    "cosalette.mqtt": ("Message", "Payload", "Topic"),
}

_lazy_getattr, __dir__ = lazy_exports(__name__, _EXPORTS)


def __getattr__(name: str) -> object:
    if name != "__version__":
        return _lazy_getattr(name)
    # Lazy too: importlib.metadata is costly. Single source of truth: installed
    # distribution metadata, so that cosalette.__version__ and
    # importlib.metadata.version("cosalette") can never disagree.
    from importlib.metadata import PackageNotFoundError, version

    try:
        value = version("cosalette")
    except PackageNotFoundError:
        # Not installed at all (e.g. running straight from a source tree).
        value = "0.0.0+unknown"
    globals()["__version__"] = value
    return value
