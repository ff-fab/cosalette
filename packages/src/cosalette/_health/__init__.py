"""Health reporting and availability for IoT-to-MQTT bridge applications.

Publishes app-level heartbeats and per-device availability over MQTT,
with LWT (Last Will and Testament) integration for crash detection.

See Also:
    ADR-012 — Health and availability reporting.
    ADR-006 — Protocol-based ports (MqttPort, ClockPort).
"""

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
