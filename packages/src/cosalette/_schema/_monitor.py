"""Network compliance monitor for schema enforcement.

Standalone lightweight subscriber that watches +/schema/status and +/status
topics to detect offline or non-compliant apps in the fleet.

See Also:
    ADR-033 — MQTT schema enforcement.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from cosalette._json import loads
from cosalette._schema import SchemaRegistry

logger = logging.getLogger(__name__)


@dataclass
class AppComplianceState:
    """Tracked compliance state for one app."""

    app_name: str
    last_schema_status: dict[str, Any] | None = None
    last_heartbeat: datetime | None = None
    is_online: bool = False
    violation_count: int = 0
    enforcement_mode: str = "unknown"


@dataclass(frozen=True, slots=True)
class ComplianceReport:
    """Compliance report for the fleet."""

    compliant: tuple[str, ...]
    non_compliant: tuple[str, ...]
    offline: tuple[str, ...]
    unknown: tuple[str, ...]

    def summary(self) -> str:
        """Human-readable summary."""
        lines = []
        if self.compliant:
            lines.append(f"✓ Compliant: {', '.join(self.compliant)}")
        if self.non_compliant:
            for app in self.non_compliant:
                lines.append(f"✗ Non-compliant: {app}")
        if self.offline:
            for app in self.offline:
                lines.append(f"? Offline: {app}")
        if self.unknown:
            for app in self.unknown:
                lines.append(f"⚠ Unknown (not in schema): {app}")
        return "\n".join(lines)


class NetworkComplianceMonitor:
    """Monitors schema compliance across all apps in a fleet.

    Subscribes to +/schema/status and +/status topics. Tracks
    expected apps from the schema registry and reports:
    - Apps that are online but have schema violations
    - Apps that are expected but not reporting (offline)
    - Apps that are reporting but not in the schema (unknown)
    """

    def __init__(self, expected_apps: frozenset[str]) -> None:
        self._expected_apps = expected_apps
        self._states: dict[str, AppComplianceState] = {}

    def handle_schema_status(self, app_name: str, payload: dict[str, Any]) -> None:
        """Process a schema/status message from an app."""
        state = self._states.setdefault(app_name, AppComplianceState(app_name=app_name))
        state.last_schema_status = payload
        state.violation_count = payload.get("violation_count", 0)
        state.enforcement_mode = payload.get("enforcement", "unknown")

    def handle_heartbeat(self, app_name: str, payload: str) -> None:
        """Process a status/heartbeat message from an app."""
        state = self._states.setdefault(app_name, AppComplianceState(app_name=app_name))
        state.last_heartbeat = datetime.now(tz=UTC)
        state.is_online = payload != "offline"

    def get_report(self) -> ComplianceReport:
        """Generate a compliance report for all expected and observed apps."""
        compliant = []
        non_compliant = []
        offline = []
        unknown = []

        # Check all expected apps
        for app_name in self._expected_apps:
            state = self._states.get(app_name)
            if state is None or not state.is_online:
                # App is expected but never seen or marked offline
                offline.append(app_name)
            elif state.violation_count == 0:
                # App is online and compliant
                compliant.append(app_name)
            else:
                # App is online but has violations
                non_compliant.append(app_name)

        # Check for unknown apps (reporting but not in expected set)
        for app_name in self._states:
            if app_name not in self._expected_apps:
                unknown.append(app_name)

        return ComplianceReport(
            compliant=tuple(sorted(compliant)),
            non_compliant=tuple(sorted(non_compliant)),
            offline=tuple(sorted(offline)),
            unknown=tuple(sorted(unknown)),
        )


def _decode_payload(raw: bytes | bytearray | str, topic: object) -> str | None:
    """Decode MQTT payload, returning None for malformed data."""
    if isinstance(raw, (bytes, bytearray)):
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            logger.warning("Skipping malformed payload on %r", topic)
            return None
    return str(raw)


def _dispatch_message(
    monitor: NetworkComplianceMonitor,
    topic_str: str,
    payload_str: str,
) -> None:
    """Route a decoded message to the appropriate monitor handler."""
    app_name, sep, subtopic = topic_str.partition("/")
    if not sep:
        return

    if subtopic == "schema/status":
        try:
            parsed = loads(payload_str)
        except ValueError, RecursionError:
            # JSONDecodeError is a ValueError; RecursionError is defensive
            # against a backend without a C-level nesting cap (CWE-674).
            logger.debug("Invalid JSON on %r", topic_str)
            return
        if isinstance(parsed, dict):
            monitor.handle_schema_status(app_name, parsed)
        else:
            logger.debug("Non-object schema status on %r", topic_str)
    elif subtopic == "status":
        monitor.handle_heartbeat(app_name, payload_str)


async def run_monitor(
    broker_url: str,
    registry: SchemaRegistry,
    *,
    timeout: float | None = None,
) -> ComplianceReport:
    """Run the network compliance monitor.

    Connects to the broker, subscribes to status topics, waits for
    messages (up to timeout), then returns a compliance report.

    Args:
        broker_url: MQTT broker URL (e.g., "localhost:1883")
        registry: Network-level schema to determine expected apps
        timeout: Seconds to collect messages before reporting (None = 10s default)
    """
    import aiomqtt

    expected_apps = registry.all_app_names()
    monitor = NetworkComplianceMonitor(expected_apps)

    wait_time = timeout if timeout is not None else 10.0
    host, _, port_str = broker_url.partition(":")
    port = int(port_str) if port_str else 1883

    async with aiomqtt.Client(host, port=port) as client:
        await client.subscribe("+/schema/status")
        await client.subscribe("+/status")

        try:
            async with asyncio.timeout(wait_time):
                async for message in client.messages:
                    payload_str = _decode_payload(
                        message.payload,
                        message.topic,
                    )
                    if payload_str is not None:
                        _dispatch_message(
                            monitor,
                            str(message.topic),
                            payload_str,
                        )
        except TimeoutError:
            pass  # Expected — collection period ended

    return monitor.get_report()
