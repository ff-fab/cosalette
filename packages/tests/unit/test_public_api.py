"""Unit tests for the cosalette top-level public API surface.

Test Techniques Used:
    - Specification-based Testing: ``__all__`` completeness against the
      documented public API contract.
    - Importability: Every name in ``__all__`` resolves to a real object
      via ``getattr``.

See Also:
    ADR-006 — Hexagonal architecture (public exports).
"""

from __future__ import annotations

import cosalette


class TestCosalettePublicAPI:
    """All expected symbols are importable and listed in ``__all__``."""

    EXPECTED_NAMES = {
        # Version
        "__version__",
        # App
        "App",
        "AppContext",
        "DeviceContext",
        "LifespanFunc",
        # Clock
        "ClockPort",
        "SystemClock",
        # Logging
        "JsonFormatter",
        "configure_logging",
        # MQTT
        "MessageCallback",
        "MockMqttClient",
        "MqttClient",
        "MqttLifecycle",
        "MqttMessageHandler",
        "MqttPort",
        "NullMqttClient",
        "WillConfig",
        # Errors
        "ErrorPayload",
        "ErrorPublisher",
        "build_error_payload",
        # Health
        "DeviceStatus",
        "HeartbeatPayload",
        "HealthReporter",
        "build_will_config",
        # Settings
        "LoggingSettings",
        "MqttSettings",
        "Settings",
    }

    def test_all_contains_expected_symbols(self) -> None:
        """``__all__`` matches the documented public API exactly.

        Technique: Specification-based — verifying module contract.
        """
        assert set(cosalette.__all__) == self.EXPECTED_NAMES

    def test_all_symbols_importable(self) -> None:
        """Every name in ``__all__`` resolves to an attribute on the module.

        Technique: Specification-based — importability check.
        """
        for name in cosalette.__all__:
            obj = getattr(cosalette, name, None)
            assert obj is not None, f"{name!r} listed in __all__ but not importable"
