"""Unit tests for cosalette.testing — public test-support utilities.

Test Techniques Used:
    - Specification-based Testing: Public API surface, ``__all__``
      completeness, factory defaults and overrides.
    - Protocol Conformance: FakeClock satisfies ClockPort via
      ``isinstance`` (PEP 544 runtime_checkable).
    - Identity Testing: Re-exported symbols are the *same* objects
      as the originals in their private modules.
"""

from __future__ import annotations

import cosalette._mqtt as _mqtt_mod
import cosalette.testing as testing_mod
from cosalette._clock import ClockPort
from cosalette._settings import MqttSettings, Settings
from cosalette.testing import FakeClock, MockMqttClient, NullMqttClient, make_settings

# ---------------------------------------------------------------------------
# TestPublicAPI — __all__ and importability
# ---------------------------------------------------------------------------


class TestPublicAPI:
    """All expected symbols are importable and listed in ``__all__``."""

    EXPECTED_NAMES = {"FakeClock", "MockMqttClient", "NullMqttClient", "make_settings"}

    def test_all_contains_expected_symbols(self) -> None:
        """``__all__`` matches the documented public API.

        Technique: Specification-based — verifying module contract.
        """
        assert set(testing_mod.__all__) == self.EXPECTED_NAMES

    def test_all_symbols_importable(self) -> None:
        """Every name in ``__all__`` resolves to an attribute.

        Technique: Specification-based — importability check.
        """
        for name in testing_mod.__all__:
            assert hasattr(testing_mod, name), f"{name} not found on module"


# ---------------------------------------------------------------------------
# TestFakeClock
# ---------------------------------------------------------------------------


class TestFakeClock:
    """FakeClock: deterministic test double for ClockPort."""

    def test_default_time_is_zero(self) -> None:
        """Default-constructed FakeClock starts at 0.0.

        Technique: Specification-based — default value.
        """
        clock = FakeClock()

        assert clock.now() == 0.0

    def test_custom_initial_time(self) -> None:
        """FakeClock accepts an initial time via constructor.

        Technique: Specification-based — parameterised construction.
        """
        clock = FakeClock(42.0)

        assert clock.now() == 42.0

    def test_time_can_be_updated(self) -> None:
        """Setting ``_time`` changes the value returned by ``now()``.

        Technique: State-based — mutable test double.
        """
        clock = FakeClock()
        clock._time = 99.5

        assert clock.now() == 99.5

    def test_satisfies_clock_port(self) -> None:
        """FakeClock satisfies ClockPort protocol (PEP 544).

        Technique: Protocol Conformance — runtime_checkable isinstance.
        """
        clock = FakeClock()

        assert isinstance(clock, ClockPort)


# ---------------------------------------------------------------------------
# TestMakeSettings
# ---------------------------------------------------------------------------


class TestMakeSettings:
    """make_settings: factory producing Settings without .env files."""

    def test_returns_settings_instance(self) -> None:
        """Factory returns a Settings object.

        Technique: Specification-based — return type.
        """
        result = make_settings()

        assert isinstance(result, Settings)

    def test_defaults_mqtt_host_localhost(self) -> None:
        """Default Settings has mqtt.host == 'localhost'.

        Technique: Specification-based — sensible defaults.
        """
        result = make_settings()

        assert result.mqtt.host == "localhost"

    def test_defaults_mqtt_port_1883(self) -> None:
        """Default Settings has mqtt.port == 1883.

        Technique: Specification-based — sensible defaults.
        """
        result = make_settings()

        assert result.mqtt.port == 1883

    def test_accepts_overrides(self) -> None:
        """Keyword overrides are forwarded to the Settings constructor.

        Technique: Specification-based — override mechanism.
        """
        custom_mqtt = MqttSettings(host="broker.test", port=8883)

        result = make_settings(mqtt=custom_mqtt)

        assert result.mqtt.host == "broker.test"
        assert result.mqtt.port == 8883


# ---------------------------------------------------------------------------
# TestReExports — identity checks
# ---------------------------------------------------------------------------


class TestReExports:
    """Re-exported symbols are the same objects as the private originals."""

    def test_mock_mqtt_client_identity(self) -> None:
        """MockMqttClient from cosalette.testing is cosalette._mqtt.MockMqttClient.

        Technique: Identity Testing — ``is`` check.
        """
        assert MockMqttClient is _mqtt_mod.MockMqttClient

    def test_null_mqtt_client_identity(self) -> None:
        """NullMqttClient from cosalette.testing is cosalette._mqtt.NullMqttClient.

        Technique: Identity Testing — ``is`` check.
        """
        assert NullMqttClient is _mqtt_mod.NullMqttClient
