"""Tests for network compliance monitoring."""

from __future__ import annotations

from datetime import datetime

from cosalette._schema_monitor import (
    AppComplianceState,
    ComplianceReport,
    NetworkComplianceMonitor,
)


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
