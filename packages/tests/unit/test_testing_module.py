"""Unit tests for cosalette.testing — public test-support utilities.

Test Techniques Used:
    - Specification-based Testing: Public API surface, ``__all__``
      completeness, factory defaults and overrides.
    - Protocol Conformance: FakeClock satisfies ClockPort via
      ``isinstance`` (PEP 544 runtime_checkable).
    - Identity Testing: Re-exported symbols are the *same* objects
      as the originals in their private modules.
    - Fixture Injection: Plugin-registered fixtures are automatically
      available without local definitions.
"""

from __future__ import annotations

import cosalette._mqtt as _mqtt_mod
import cosalette.testing as testing_mod
from cosalette._clock import ClockPort
from cosalette._context import DeviceContext
from cosalette._settings import MqttSettings, Settings
from cosalette.testing import (
    AppHarness,
    FakeClock,
    MockMqttClient,
    NullMqttClient,
    make_settings,
)

# ---------------------------------------------------------------------------
# TestPublicAPI — __all__ and importability
# ---------------------------------------------------------------------------


class TestPublicAPI:
    """All expected symbols are importable and listed in ``__all__``."""

    EXPECTED_NAMES = {
        "AppHarness",
        "FakeClock",
        "MockMqttClient",
        "NullMqttClient",
        "make_settings",
    }

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


# ---------------------------------------------------------------------------
# TestAppHarness
# ---------------------------------------------------------------------------


class TestAppHarness:
    """AppHarness: one-liner test setup wrapping App with test doubles."""

    def test_create_returns_harness_instance(self) -> None:
        """``create()`` returns an AppHarness instance.

        Technique: Specification-based — return type.
        """
        harness = AppHarness.create()

        assert isinstance(harness, AppHarness)

    def test_create_defaults_name_and_version(self) -> None:
        """Default harness uses name='testapp' and version='1.0.0'.

        Technique: Specification-based — default values.
        """
        harness = AppHarness.create()

        assert harness.app._name == "testapp"
        assert harness.app._version == "1.0.0"

    def test_create_custom_name_version(self) -> None:
        """Custom name and version are forwarded to App.

        Technique: Specification-based — parameterised construction.
        """
        harness = AppHarness.create(name="mybridge", version="2.3.0")

        assert harness.app._name == "mybridge"
        assert harness.app._version == "2.3.0"

    def test_create_settings_overrides(self) -> None:
        """Settings overrides are forwarded to make_settings.

        Technique: Specification-based — override mechanism.
        """
        custom_mqtt = MqttSettings(host="custom.broker", port=8883)

        harness = AppHarness.create(mqtt=custom_mqtt)

        assert harness.settings.mqtt.host == "custom.broker"
        assert harness.settings.mqtt.port == 8883

    def test_mqtt_is_mock_instance(self) -> None:
        """Harness mqtt field is a MockMqttClient.

        Technique: Specification-based — correct double type.
        """
        harness = AppHarness.create()

        assert isinstance(harness.mqtt, MockMqttClient)

    def test_clock_is_fake_instance(self) -> None:
        """Harness clock field is a FakeClock.

        Technique: Specification-based — correct double type.
        """
        harness = AppHarness.create()

        assert isinstance(harness.clock, FakeClock)

    def test_shutdown_event_initially_not_set(self) -> None:
        """Shutdown event is not set on a fresh harness.

        Technique: Specification-based — initial state.
        """
        harness = AppHarness.create()

        assert not harness.shutdown_event.is_set()

    def test_trigger_shutdown_sets_event(self) -> None:
        """``trigger_shutdown()`` sets the shutdown event.

        Technique: State-based — method side-effect.
        """
        harness = AppHarness.create()

        harness.trigger_shutdown()

        assert harness.shutdown_event.is_set()

    def test_create_dry_run_mode(self) -> None:
        """``create(dry_run=True)`` sets App dry_run flag.

        Technique: Specification-based — dry_run forwarding.
        """
        harness = AppHarness.create(dry_run=True)
        assert harness.app._dry_run is True

    async def test_run_executes_device(self) -> None:
        """``run()`` drives the App lifecycle, executing registered devices.

        Technique: Integration — verify end-to-end device execution via
        the harness's ``run()`` method.
        """
        import asyncio

        harness = AppHarness.create()
        device_called = asyncio.Event()

        @harness.app.device("probe")
        async def probe(ctx):  # type: ignore[no-untyped-def]
            device_called.set()
            harness.trigger_shutdown()

        await asyncio.wait_for(harness.run(), timeout=5.0)

        assert device_called.is_set()


# ---------------------------------------------------------------------------
# TestPytestPlugin — plugin-registered fixtures
# ---------------------------------------------------------------------------


class TestPytestPlugin:
    """Fixtures auto-registered by cosalette.testing._plugin.

    These tests accept plugin-provided fixtures directly as parameters,
    confirming that ``conftest.py`` registration works correctly.

    Technique: Fixture Injection — verify plugin auto-registration.
    """

    def test_mock_mqtt_fixture_returns_mock(self, mock_mqtt: MockMqttClient) -> None:
        """``mock_mqtt`` fixture yields a MockMqttClient instance.

        Technique: Specification-based — return type from plugin fixture.
        """
        assert isinstance(mock_mqtt, MockMqttClient)

    def test_fake_clock_fixture_returns_fake(self, fake_clock: FakeClock) -> None:
        """``fake_clock`` fixture yields a FakeClock instance.

        Technique: Specification-based — return type from plugin fixture.
        """
        assert isinstance(fake_clock, FakeClock)

    def test_device_context_fixture_returns_context(
        self, device_context: DeviceContext
    ) -> None:
        """``device_context`` fixture yields a DeviceContext instance.

        Technique: Specification-based — return type from plugin fixture.
        """
        assert isinstance(device_context, DeviceContext)

    def test_device_context_has_test_defaults(
        self, device_context: DeviceContext
    ) -> None:
        """device_context has expected name and topic_prefix defaults.

        Technique: Specification-based — verifying default values.
        """
        assert device_context.name == "test_device"
        assert device_context._topic_prefix == "test"

    def test_device_context_uses_mock_mqtt(self, device_context: DeviceContext) -> None:
        """device_context's MQTT port is a MockMqttClient.

        Technique: Specification-based — correct double wiring.
        """
        assert isinstance(device_context._mqtt, MockMqttClient)

    def test_fixtures_are_fresh_per_test(self, mock_mqtt: MockMqttClient) -> None:
        """Each test gets a fresh MockMqttClient with empty state.

        Technique: Specification-based — per-test isolation.
        """
        assert mock_mqtt.published == []
