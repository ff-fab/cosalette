"""Tests for HealthCheckable protocol and adapter detection (COS-497.2)."""

from __future__ import annotations

import logging as _logging

import pytest

from cosalette import App, HealthCheckable
from cosalette._adapter_lifecycle import detect_health_checkable
from cosalette._context import DeviceContext
from cosalette._health import AdapterHealthStatus
from cosalette._registration import _DeviceRegistration
from cosalette._settings import Settings
from cosalette._wiring import DeviceInfo, build_adapter_device_map

pytestmark = pytest.mark.unit


class _HealthyAdapter:
    async def health_check(self) -> bool:
        return True


class _PlainAdapter:
    """Adapter without health_check — should not satisfy the protocol."""


class _PortA:
    """Dummy port type A."""


class _PortB:
    """Dummy port type B."""


class _PortC:
    """Dummy port type C."""


class TestHealthCheckableProtocol:
    def test_adapter_with_health_check_satisfies_protocol(self) -> None:
        assert isinstance(_HealthyAdapter(), HealthCheckable)

    def test_adapter_without_health_check_does_not_satisfy(self) -> None:
        assert not isinstance(_PlainAdapter(), HealthCheckable)


class TestDetectHealthCheckable:
    def test_detects_health_checkable_adapters(self) -> None:
        healthy = _HealthyAdapter()
        plain = _PlainAdapter()
        resolved = {_PortA: healthy, _PortB: plain}

        result = detect_health_checkable(resolved)

        assert result == {_PortA: healthy}

    def test_returns_empty_when_none_checkable(self) -> None:
        resolved = {_PortA: _PlainAdapter(), _PortB: _PlainAdapter()}

        result = detect_health_checkable(resolved)

        assert result == {}

    def test_returns_empty_for_empty_input(self) -> None:
        assert detect_health_checkable({}) == {}

    def test_multiple_health_checkable_adapters(self) -> None:
        h1 = _HealthyAdapter()
        h2 = _HealthyAdapter()
        resolved = {_PortA: h1, _PortB: _PlainAdapter(), _PortC: h2}

        result = detect_health_checkable(resolved)

        assert result == {_PortA: h1, _PortC: h2}


class TestHealthCheckIntervalParameter:
    def test_default_interval_is_30(self) -> None:
        app = App("test")
        assert app._health_check_interval == 30.0

    def test_custom_interval(self) -> None:
        app = App("test", health_check_interval=60.0)
        assert app._health_check_interval == 60.0

    def test_none_disables_health_check(self) -> None:
        app = App("test", health_check_interval=None)
        assert app._health_check_interval is None

    def test_zero_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="health_check_interval must be positive"):
            App("test", health_check_interval=0)

    def test_negative_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="health_check_interval must be positive"):
            App("test", health_check_interval=-5.0)


# ---------------------------------------------------------------------------
# Helpers for build_adapter_device_map tests
# ---------------------------------------------------------------------------


def _make_reg(
    name: str,
    *,
    injection_plan: list[tuple[str, type]] | None = None,
    is_root: bool = False,
) -> _DeviceRegistration:
    """Create a minimal _DeviceRegistration for testing."""

    async def _noop() -> None:
        pass

    return _DeviceRegistration(
        name=name,
        func=_noop,
        injection_plan=injection_plan or [],
        is_root=is_root,
    )


# ---------------------------------------------------------------------------
# AdapterHealthStatus
# ---------------------------------------------------------------------------


class TestAdapterHealthStatus:
    def test_defaults(self) -> None:
        status = AdapterHealthStatus()
        assert status.healthy is True
        assert status.consecutive_failures == 0
        assert status.last_check == 0.0

    def test_custom_values(self) -> None:
        status = AdapterHealthStatus(
            healthy=False, consecutive_failures=3, last_check=42.0
        )
        assert status.healthy is False
        assert status.consecutive_failures == 3
        assert status.last_check == 42.0

    def test_immutable(self) -> None:
        status = AdapterHealthStatus()
        with pytest.raises(AttributeError):
            status.healthy = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# build_adapter_device_map
# ---------------------------------------------------------------------------


class TestBuildAdapterDeviceMap:
    def test_maps_adapter_to_devices(self) -> None:
        """Devices that inject an adapter type are mapped to it."""
        regs = [
            _make_reg("blind", injection_plan=[("adapter", _PortA)]),
            _make_reg("window", injection_plan=[("adapter", _PortA)]),
        ]
        adapters: dict[type, object] = {_PortA: _HealthyAdapter()}

        result = build_adapter_device_map(regs, adapters)

        assert result == {
            _PortA: [DeviceInfo("blind", False), DeviceInfo("window", False)]
        }

    def test_ignores_known_injectable_types(self) -> None:
        """Framework-provided types are not treated as adapters."""
        regs = [
            _make_reg(
                "dev",
                injection_plan=[
                    ("ctx", DeviceContext),
                    ("settings", Settings),
                    ("logger", _logging.Logger),
                    ("adapter", _PortA),
                ],
            ),
        ]
        adapters: dict[type, object] = {_PortA: _HealthyAdapter()}

        result = build_adapter_device_map(regs, adapters)

        assert _PortA in result
        assert len(result) == 1  # only _PortA, not framework types

    def test_deduplicates_shared_names(self) -> None:
        """Telemetry + command sharing a name produce one mapping entry."""
        regs = [
            _make_reg("sensor", injection_plan=[("adapter", _PortA)]),
            _make_reg("sensor", injection_plan=[("adapter", _PortA)]),
        ]
        adapters: dict[type, object] = {_PortA: _HealthyAdapter()}

        result = build_adapter_device_map(regs, adapters)

        assert result[_PortA] == [DeviceInfo("sensor", False)]

    def test_multiple_adapters(self) -> None:
        """Multiple adapter types map to their respective devices."""
        regs = [
            _make_reg("blind", injection_plan=[("a", _PortA)]),
            _make_reg("sensor", injection_plan=[("b", _PortB)]),
        ]
        adapters: dict[type, object] = {
            _PortA: _HealthyAdapter(),
            _PortB: _PlainAdapter(),
        }

        result = build_adapter_device_map(regs, adapters)

        assert result[_PortA] == [DeviceInfo("blind", False)]
        assert result[_PortB] == [DeviceInfo("sensor", False)]

    def test_root_device_preserves_is_root(self) -> None:
        """Root devices carry is_root=True in the mapping."""
        regs = [
            _make_reg("app", injection_plan=[("adapter", _PortA)], is_root=True),
        ]
        adapters: dict[type, object] = {_PortA: _HealthyAdapter()}

        result = build_adapter_device_map(regs, adapters)

        assert result[_PortA] == [DeviceInfo("app", True)]

    def test_empty_registrations(self) -> None:
        adapters: dict[type, object] = {_PortA: _HealthyAdapter()}
        result = build_adapter_device_map([], adapters)
        assert result == {_PortA: []}

    def test_empty_adapters(self) -> None:
        regs = [_make_reg("dev", injection_plan=[("adapter", _PortA)])]
        result = build_adapter_device_map(regs, {})
        assert result == {}
