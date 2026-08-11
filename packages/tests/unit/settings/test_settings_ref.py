"""Unit tests for cosalette._settings._ref.

Covers: SettingRef callable behavior, field_name attribute preservation,
setting_ref() factory function, field path compilation, and error handling
for invalid field names.

Test Techniques Used:
    - Specification-based Testing: Verify SettingRef callable behavior,
      field_name preservation, factory function, and error handling.
    - Error Case Testing: Invalid field paths and missing attributes.
    - Equivalence Testing: SettingRef vs lambda behavior comparison.
"""

from __future__ import annotations

from typing import Any

import pytest

from cosalette._settings._ref import SettingRef, _compile_field_accessor, setting_ref
from cosalette.testing import make_settings

pytestmark = pytest.mark.unit


class TestSettingRef:
    """Test the SettingRef class behavior."""

    def test_field_name_attribute_preserved(self) -> None:
        """SettingRef preserves the field_name for introspection."""
        ref = SettingRef("mqtt.reconnect_interval")
        assert ref.field_name == "mqtt.reconnect_interval"

    def test_callable_behavior_works(self) -> None:
        """SettingRef can be called like a lambda to resolve field values."""
        ref = SettingRef("mqtt.reconnect_interval")
        settings = make_settings()

        # Should resolve the same way as direct access
        expected = settings.mqtt.reconnect_interval
        assert ref(settings) == expected

    def test_deep_field_access(self) -> None:
        """SettingRef supports nested field access via dot notation."""
        ref = SettingRef("mqtt.host")
        settings = make_settings()

        expected = settings.mqtt.host
        assert ref(settings) == expected

    def test_single_field_access(self) -> None:
        """SettingRef works with top-level fields (no dots)."""

        # Create a simple object for testing
        class SimpleSettings:
            poll_interval = 10.0

        ref = SettingRef("poll_interval")
        settings = SimpleSettings()

        assert ref(settings) == 10.0

    def test_repr_shows_field_name(self) -> None:
        """SettingRef repr shows the field name for debugging."""
        ref = SettingRef("mqtt.reconnect_interval")
        assert repr(ref) == "SettingRef('mqtt.reconnect_interval')"

    def test_missing_field_raises_attribute_error(self) -> None:
        """SettingRef raises AttributeError for missing fields."""
        ref = SettingRef("nonexistent.field")
        settings = make_settings()

        with pytest.raises(AttributeError, match="Field 'nonexistent.field' not found"):
            ref(settings)

    def test_missing_nested_field_raises_attribute_error(self) -> None:
        """SettingRef raises AttributeError for missing nested fields."""
        ref = SettingRef("mqtt.nonexistent")
        settings = make_settings()

        with pytest.raises(AttributeError, match="Field 'mqtt.nonexistent' not found"):
            ref(settings)


class TestSettingRefFactory:
    """Test the setting_ref() factory function."""

    def test_factory_creates_setting_ref(self) -> None:
        """setting_ref() creates a SettingRef instance."""
        ref = setting_ref("mqtt.reconnect_interval")
        assert isinstance(ref, SettingRef)
        assert ref.field_name == "mqtt.reconnect_interval"

    def test_factory_result_is_callable(self) -> None:
        """setting_ref() result works as a callable."""
        ref = setting_ref("mqtt.reconnect_interval")
        settings = make_settings()

        # Should work like a lambda
        expected = settings.mqtt.reconnect_interval
        assert ref(settings) == expected


class TestFieldAccessorCompilation:
    """Test the internal field accessor compilation logic."""

    def test_valid_field_path_compiles(self) -> None:
        """Valid dot-separated paths compile to working accessors."""
        accessor = _compile_field_accessor("mqtt.host")
        settings = make_settings()

        assert accessor(settings) == settings.mqtt.host

    def test_empty_field_name_raises_error(self) -> None:
        """Empty field_name raises ValueError."""
        with pytest.raises(ValueError, match="field_name cannot be empty"):
            _compile_field_accessor("")

    def test_whitespace_only_field_name_raises_error(self) -> None:
        """Whitespace-only field_name raises ValueError."""
        with pytest.raises(ValueError, match="field_name cannot be empty"):
            _compile_field_accessor("   ")

    def test_empty_segment_raises_error(self) -> None:
        """Field paths with empty segments raise ValueError."""
        with pytest.raises(ValueError, match="Invalid field_name.*empty segment"):
            _compile_field_accessor("mqtt..host")

    def test_leading_dot_raises_error(self) -> None:
        """Field paths with leading dots raise ValueError."""
        with pytest.raises(ValueError, match="Invalid field_name.*empty segment"):
            _compile_field_accessor(".mqtt.host")

    def test_trailing_dot_raises_error(self) -> None:
        """Field paths with trailing dots raise ValueError."""
        with pytest.raises(ValueError, match="Invalid field_name.*empty segment"):
            _compile_field_accessor("mqtt.host.")


class TestBackwardCompatibility:
    """Test that SettingRef maintains backward compatibility."""

    def test_works_as_interval_spec(self) -> None:
        """SettingRef can be used as IntervalSpec (callable behavior)."""
        ref = setting_ref("mqtt.reconnect_interval")
        settings = make_settings()

        # Should work anywhere a lambda would work
        def simulate_interval_usage(interval_spec: Any) -> float:
            """Simulate how the framework uses IntervalSpec."""
            return interval_spec(settings)

        result = simulate_interval_usage(ref)
        assert result == settings.mqtt.reconnect_interval

    def test_works_as_enabled_spec(self) -> None:
        """SettingRef can be used as EnabledSpec for boolean fields."""

        # Create a settings object with a boolean field
        class TestSettings:
            class Features:
                debug_mode = True

            features = Features()

        ref = setting_ref("features.debug_mode")
        settings = TestSettings()

        # Should resolve boolean values
        assert ref(settings) is True

    def test_equivalent_to_lambda(self) -> None:
        """SettingRef produces the same result as equivalent lambda."""
        ref = setting_ref("mqtt.reconnect_interval")

        def lambda_equivalent(s: Any) -> float:
            return s.mqtt.reconnect_interval

        settings = make_settings()

        assert ref(settings) == lambda_equivalent(settings)
