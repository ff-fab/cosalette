"""Health reporting and availability for IoT-to-MQTT bridge applications.

Publishes app-level heartbeats and per-device availability over MQTT,
with LWT (Last Will and Testament) integration for crash detection.

Exports are lazy (PEP 562) so importing ``cosalette._health._liveness`` for the
``health`` probe does not pull in the MQTT stack via ``_reporter``.

See Also:
    ADR-012 — Health and availability reporting.
    ADR-006 — Protocol-based ports (MqttPort, ClockPort).
"""

from typing import TYPE_CHECKING

from cosalette._lazy import lazy_exports

if TYPE_CHECKING:
    from cosalette._health._checker import (
        AdapterHealthStatus,
        HealthCheckable,
        HealthCheckRunner,
    )
    from cosalette._health._reporter import (
        DeviceStatus,
        HealthReporter,
        HeartbeatPayload,
        build_will_config,
    )

__all__ = [
    "AdapterHealthStatus",
    "DeviceStatus",
    "HealthCheckRunner",
    "HealthCheckable",
    "HealthReporter",
    "HeartbeatPayload",
    "build_will_config",
]

__getattr__, __dir__ = lazy_exports(
    __name__,
    {
        "cosalette._health._checker": (
            "AdapterHealthStatus",
            "HealthCheckable",
            "HealthCheckRunner",
        ),
        "cosalette._health._reporter": (
            "DeviceStatus",
            "HealthReporter",
            "HeartbeatPayload",
            "build_will_config",
        ),
    },
)
