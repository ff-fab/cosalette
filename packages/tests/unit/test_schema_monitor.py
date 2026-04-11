"""Unit tests for cosalette._schema_monitor — network compliance monitoring.

Test Techniques Used:
- Specification-based Testing: NetworkComplianceMonitor and ComplianceReport contracts
- State Transition Testing: online/offline transitions, compliance state changes
- Equivalence Partitioning: compliant/non-compliant/offline/unknown app categories
- Boundary Value Analysis: empty expected sets, missing schema fields
- Error Guessing: missing fields defaults, offline-then-online transitions
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cosalette._schema import ChannelSchema, EnforcementConfig, SchemaRegistry
from cosalette._schema_monitor import (
    AppComplianceState,
    ComplianceReport,
    NetworkComplianceMonitor,
    run_monitor,
)

pytestmark = pytest.mark.unit


class TestAppComplianceState:
    """Test AppComplianceState dataclass."""

    def test_default_values(self) -> None:
        """Test default field values."""
        state = AppComplianceState("test-app")
        assert state.app_name == "test-app"
        assert state.last_schema_status is None
        assert state.last_heartbeat is None
        assert state.is_online is False
        assert state.violation_count == 0
        assert state.enforcement_mode == "unknown"


class TestComplianceReport:
    """Test ComplianceReport dataclass and summary."""

    def test_empty_report_summary(self) -> None:
        """Test summary for empty report."""
        report = ComplianceReport(
            compliant=(),
            non_compliant=(),
            offline=(),
            unknown=(),
        )
        assert report.summary() == ""

    def test_report_summary_format(self) -> None:
        """Test summary string contains expected markers."""
        report = ComplianceReport(
            compliant=("app1", "app2"),
            non_compliant=("app3",),
            offline=("app4",),
            unknown=("app5",),
        )
        summary = report.summary()

        # Check for expected prefixes
        assert "✓ Compliant: app1, app2" in summary
        assert "✗ Non-compliant: app3" in summary
        assert "? Offline: app4" in summary
        assert "⚠ Unknown (not in schema): app5" in summary


class TestNetworkComplianceMonitor:
    """Test NetworkComplianceMonitor compliance classification."""

    def test_all_apps_compliant(self) -> None:
        """Test expected apps report compliant status."""
        expected_apps = frozenset(["app1", "app2"])
        monitor = NetworkComplianceMonitor(expected_apps)

        # Both apps report as online with no violations
        monitor.handle_heartbeat("app1", "online")
        monitor.handle_schema_status(
            "app1", {"violation_count": 0, "enforcement": "strict"}
        )

        monitor.handle_heartbeat("app2", "online")
        monitor.handle_schema_status(
            "app2", {"violation_count": 0, "enforcement": "warn"}
        )

        report = monitor.get_report()
        assert report.compliant == ("app1", "app2")
        assert report.non_compliant == ()
        assert report.offline == ()
        assert report.unknown == ()

    def test_non_compliant_app_detected(self) -> None:
        """Test app with violations reported."""
        expected_apps = frozenset(["app1"])
        monitor = NetworkComplianceMonitor(expected_apps)

        # App is online but has violations
        monitor.handle_heartbeat("app1", "online")
        monitor.handle_schema_status(
            "app1", {"violation_count": 5, "enforcement": "strict"}
        )

        report = monitor.get_report()
        assert report.compliant == ()
        assert report.non_compliant == ("app1",)
        assert report.offline == ()
        assert report.unknown == ()

    def test_offline_app_detected(self) -> None:
        """Test expected app never reports."""
        expected_apps = frozenset(["app1", "app2"])
        monitor = NetworkComplianceMonitor(expected_apps)

        # Only app1 reports, app2 never seen
        monitor.handle_heartbeat("app1", "online")
        monitor.handle_schema_status(
            "app1", {"violation_count": 0, "enforcement": "strict"}
        )

        report = monitor.get_report()
        assert report.compliant == ("app1",)
        assert report.non_compliant == ()
        assert report.offline == ("app2",)
        assert report.unknown == ()

    def test_unknown_app_detected(self) -> None:
        """Test unexpected app reports."""
        expected_apps = frozenset(["app1"])
        monitor = NetworkComplianceMonitor(expected_apps)

        # Expected app reports compliant
        monitor.handle_heartbeat("app1", "online")
        monitor.handle_schema_status(
            "app1", {"violation_count": 0, "enforcement": "strict"}
        )

        # Unknown app also reports
        monitor.handle_heartbeat("app-unknown", "online")
        monitor.handle_schema_status(
            "app-unknown", {"violation_count": 2, "enforcement": "warn"}
        )

        report = monitor.get_report()
        assert report.compliant == ("app1",)
        assert report.non_compliant == ()
        assert report.offline == ()
        assert report.unknown == ("app-unknown",)

    def test_handle_heartbeat_sets_online(self) -> None:
        """Test heartbeat marks app online."""
        expected_apps = frozenset(["app1"])
        monitor = NetworkComplianceMonitor(expected_apps)

        # App sends heartbeat with "online" status
        monitor.handle_heartbeat("app1", "online")

        state = monitor._states["app1"]
        assert state.is_online is True
        assert state.last_heartbeat is not None
        assert isinstance(state.last_heartbeat, datetime)

    def test_handle_heartbeat_offline(self) -> None:
        """Test "offline" payload marks app offline."""
        expected_apps = frozenset(["app1"])
        monitor = NetworkComplianceMonitor(expected_apps)

        # App sends "offline" status
        monitor.handle_heartbeat("app1", "offline")

        state = monitor._states["app1"]
        assert state.is_online is False
        assert state.last_heartbeat is not None

    def test_handle_schema_status_updates_state(self) -> None:
        """Test schema status updates app state."""
        expected_apps = frozenset(["app1"])
        monitor = NetworkComplianceMonitor(expected_apps)

        payload = {
            "violation_count": 3,
            "enforcement": "strict",
            "last_check": "2026-04-11T10:30:00Z",
        }

        monitor.handle_schema_status("app1", payload)

        state = monitor._states["app1"]
        assert state.last_schema_status == payload
        assert state.violation_count == 3
        assert state.enforcement_mode == "strict"

    def test_empty_expected_all_unknown(self) -> None:
        """Test no expected apps, everything is unknown."""
        expected_apps = frozenset()
        monitor = NetworkComplianceMonitor(expected_apps)

        # Some app reports status
        monitor.handle_heartbeat("amazing-app", "online")
        monitor.handle_schema_status(
            "amazing-app", {"violation_count": 0, "enforcement": "off"}
        )

        report = monitor.get_report()
        assert report.compliant == ()
        assert report.non_compliant == ()
        assert report.offline == ()
        assert report.unknown == ("amazing-app",)

    def test_app_with_missing_schema_fields(self) -> None:
        """Test schema status with missing fields uses defaults."""
        expected_apps = frozenset(["app1"])
        monitor = NetworkComplianceMonitor(expected_apps)

        # Schema status missing violation_count and enforcement
        monitor.handle_heartbeat("app1", "online")
        monitor.handle_schema_status("app1", {"other_field": "value"})

        state = monitor._states["app1"]
        assert state.violation_count == 0  # Default from .get()
        assert state.enforcement_mode == "unknown"  # Default from .get()

        # Should be marked compliant since violation_count defaults to 0
        report = monitor.get_report()
        assert report.compliant == ("app1",)

    def test_offline_then_online_transition(self) -> None:
        """Test app transitioning from offline to online."""
        expected_apps = frozenset(["app1"])
        monitor = NetworkComplianceMonitor(expected_apps)

        # App starts offline
        monitor.handle_heartbeat("app1", "offline")
        assert monitor._states["app1"].is_online is False

        # App comes online
        monitor.handle_heartbeat("app1", "online")
        monitor.handle_schema_status(
            "app1", {"violation_count": 0, "enforcement": "warn"}
        )

        report = monitor.get_report()
        assert report.compliant == ("app1",)
        assert report.offline == ()

    def test_multiple_apps_mixed_states(self) -> None:
        """Test complex scenario with multiple apps in different states."""
        expected_apps = frozenset(["compliant", "violations", "offline"])
        monitor = NetworkComplianceMonitor(expected_apps)

        # Compliant app
        monitor.handle_heartbeat("compliant", "online")
        monitor.handle_schema_status(
            "compliant", {"violation_count": 0, "enforcement": "strict"}
        )

        # App with violations
        monitor.handle_heartbeat("violations", "online")
        monitor.handle_schema_status(
            "violations", {"violation_count": 7, "enforcement": "warn"}
        )

        # Offline app never reports anything

        # Unknown app not in schema
        monitor.handle_heartbeat("rogue-app", "online")
        monitor.handle_schema_status(
            "rogue-app", {"violation_count": 1, "enforcement": "off"}
        )

        report = monitor.get_report()
        assert report.compliant == ("compliant",)
        assert report.non_compliant == ("violations",)
        assert report.offline == ("offline",)
        assert report.unknown == ("rogue-app",)


# ===========================================================================
# run_monitor() async tests
# ===========================================================================


def _make_monitor_registry(*app_names: str) -> SchemaRegistry:
    """Build a minimal network-level registry for monitor tests."""
    channels = {}
    for name in app_names:
        channels[f"{name}/status"] = ChannelSchema(
            address=f"{name}/status",
            address_template=f"{name}/status",
            direction="send",
            app_name=name,
        )
    return SchemaRegistry(
        app_name=None,
        app_version="1.0.0",
        asyncapi_version="3.0.0",
        enforcement=EnforcementConfig(mode="strict", network_level=True),
        channels=channels,
        operations={},
        component_schemas={},
        device_names=frozenset(),
    )


def _fake_message(topic: str, payload: bytes | str) -> MagicMock:
    """Create a mock MQTT message."""
    msg = MagicMock()
    msg.topic = topic
    msg.payload = payload if isinstance(payload, bytes) else payload.encode()
    return msg


class TestRunMonitor:
    """Async tests for run_monitor() with mocked aiomqtt.

    Technique: Error Guessing — verifying timeout, bad payloads, malformed UTF-8.
    """

    async def test_timeout_returns_report(self) -> None:
        """run_monitor returns a report after the timeout period."""
        registry = _make_monitor_registry("app1")

        status_payload = json.dumps(
            {"violation_count": 0, "enforcement": "strict"}
        ).encode()

        messages = [
            _fake_message("app1/status", b"online"),
            _fake_message("app1/schema/status", status_payload),
        ]

        async def _message_iter():
            for msg in messages:
                yield msg
            # Block until timeout
            await asyncio.sleep(100)

        mock_client = AsyncMock()
        mock_client.subscribe = AsyncMock()
        mock_client.messages = _message_iter()

        mock_aiomqtt = MagicMock()
        mock_aiomqtt.Client.return_value.__aenter__ = AsyncMock(
            return_value=mock_client
        )
        mock_aiomqtt.Client.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch.dict("sys.modules", {"aiomqtt": mock_aiomqtt}):
            report = await run_monitor("localhost:1883", registry, timeout=0.1)

        assert report.compliant == ("app1",)
        assert report.offline == ()

    async def test_malformed_json_skipped(self) -> None:
        """Malformed JSON on schema/status is skipped, monitor continues."""
        registry = _make_monitor_registry("app1")

        messages = [
            _fake_message("app1/schema/status", b"not-valid-json"),
            _fake_message("app1/status", b"online"),
        ]

        async def _message_iter():
            for msg in messages:
                yield msg
            await asyncio.sleep(100)

        mock_client = AsyncMock()
        mock_client.subscribe = AsyncMock()
        mock_client.messages = _message_iter()

        mock_aiomqtt = MagicMock()
        mock_aiomqtt.Client.return_value.__aenter__ = AsyncMock(
            return_value=mock_client
        )
        mock_aiomqtt.Client.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch.dict("sys.modules", {"aiomqtt": mock_aiomqtt}):
            report = await run_monitor("localhost:1883", registry, timeout=0.1)

        # app1 sent heartbeat but no valid schema status — online, 0 violations
        assert report.compliant == ("app1",)

    async def test_malformed_utf8_skipped(self) -> None:
        """Malformed UTF-8 payload is skipped, monitor continues."""
        registry = _make_monitor_registry("app1")

        messages = [
            _fake_message("app1/status", b"\xff\xfe"),  # Invalid UTF-8
            _fake_message("app1/status", b"online"),  # Valid follow-up
        ]

        async def _message_iter():
            for msg in messages:
                yield msg
            await asyncio.sleep(100)

        mock_client = AsyncMock()
        mock_client.subscribe = AsyncMock()
        mock_client.messages = _message_iter()

        mock_aiomqtt = MagicMock()
        mock_aiomqtt.Client.return_value.__aenter__ = AsyncMock(
            return_value=mock_client
        )
        mock_aiomqtt.Client.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch.dict("sys.modules", {"aiomqtt": mock_aiomqtt}):
            report = await run_monitor("localhost:1883", registry, timeout=0.1)

        # First message skipped, second processed — app1 is online
        assert report.compliant == ("app1",)

    async def test_no_messages_all_offline(self) -> None:
        """When no messages arrive, all expected apps are offline."""
        registry = _make_monitor_registry("app1", "app2")

        async def _message_iter():
            await asyncio.sleep(100)
            # Never yields — empty
            return
            yield  # noqa: RET504 — make this an async generator

        mock_client = AsyncMock()
        mock_client.subscribe = AsyncMock()
        mock_client.messages = _message_iter()

        mock_aiomqtt = MagicMock()
        mock_aiomqtt.Client.return_value.__aenter__ = AsyncMock(
            return_value=mock_client
        )
        mock_aiomqtt.Client.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch.dict("sys.modules", {"aiomqtt": mock_aiomqtt}):
            report = await run_monitor("localhost:1883", registry, timeout=0.1)

        assert report.offline == ("app1", "app2")
        assert report.compliant == ()
