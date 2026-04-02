"""Tests for HealthCheckable protocol and adapter detection (COS-497.2)."""

from __future__ import annotations

import pytest

from cosalette import App, HealthCheckable
from cosalette._adapter_lifecycle import detect_health_checkable

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

    def test_protocol_is_runtime_checkable(self) -> None:
        assert hasattr(HealthCheckable, "__protocol_attrs__") or hasattr(
            HealthCheckable, "__abstractmethods__"
        )


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
