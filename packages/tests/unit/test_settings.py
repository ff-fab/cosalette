"""Unit tests for cosalette._settings — configuration models.

Test Techniques Used:
    - Specification-based Testing: Default values and field
      constraints
    - Boundary Value Analysis: Port range, reconnect interval
    - Environment Override: monkeypatch for env var injection
    - Validation Error: pydantic constraint violations
"""

from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from cosalette._settings import (
    LoggingSettings,
    MqttSettings,
    Settings,
)


class TestMqttSettingsDefaults:
    """Verify all MQTT default values.

    Technique: Specification-based Testing.
    """

    def test_host_defaults_to_localhost(self) -> None:
        """Default host is localhost."""
        s = MqttSettings()
        assert s.host == "localhost"

    def test_port_defaults_to_1883(self) -> None:
        """Default port is standard MQTT port."""
        s = MqttSettings()
        assert s.port == 1883

    def test_username_defaults_to_none(self) -> None:
        """Default username is None (no auth)."""
        s = MqttSettings()
        assert s.username is None

    def test_password_defaults_to_none(self) -> None:
        """Default password is None (no auth)."""
        s = MqttSettings()
        assert s.password is None

    def test_client_id_defaults_to_empty(self) -> None:
        """Default client_id is empty string."""
        s = MqttSettings()
        assert s.client_id == ""

    def test_reconnect_interval_defaults_to_5(self) -> None:
        """Default reconnect interval is 5 seconds."""
        s = MqttSettings()
        assert s.reconnect_interval == 5.0

    def test_reconnect_max_interval_defaults_to_300(self) -> None:
        """Default reconnect max interval is 300 seconds (5 minutes)."""
        s = MqttSettings()
        assert s.reconnect_max_interval == 300.0

    def test_qos_defaults_to_1(self) -> None:
        """Default QoS is 1 (at-least-once)."""
        s = MqttSettings()
        assert s.qos == 1

    def test_topic_prefix_defaults_to_empty(self) -> None:
        """Default topic_prefix is empty string."""
        s = MqttSettings()
        assert s.topic_prefix == ""


class TestMqttSettingsValidation:
    """Field constraint validation for MqttSettings.

    Technique: Boundary Value Analysis.
    """

    def test_port_zero_is_invalid(self) -> None:
        """Port 0 is below minimum (ge=1)."""
        with pytest.raises(ValidationError):
            MqttSettings(port=0)

    def test_port_65536_is_invalid(self) -> None:
        """Port 65536 is above maximum (le=65535)."""
        with pytest.raises(ValidationError):
            MqttSettings(port=65536)

    def test_port_1_is_valid(self) -> None:
        """Port 1 is the minimum valid port."""
        s = MqttSettings(port=1)
        assert s.port == 1

    def test_port_65535_is_valid(self) -> None:
        """Port 65535 is the maximum valid port."""
        s = MqttSettings(port=65535)
        assert s.port == 65535

    def test_reconnect_interval_zero_is_invalid(self) -> None:
        """Reconnect interval must be > 0."""
        with pytest.raises(ValidationError):
            MqttSettings(reconnect_interval=0)

    def test_reconnect_interval_negative_is_invalid(
        self,
    ) -> None:
        """Negative reconnect interval is rejected."""
        with pytest.raises(ValidationError):
            MqttSettings(reconnect_interval=-1.0)

    def test_reconnect_max_interval_zero_is_invalid(self) -> None:
        """Max reconnect interval must be > 0."""
        with pytest.raises(ValidationError):
            MqttSettings(reconnect_max_interval=0)

    def test_reconnect_max_interval_negative_is_invalid(self) -> None:
        """Negative max reconnect interval is rejected."""
        with pytest.raises(ValidationError):
            MqttSettings(reconnect_max_interval=-1.0)

    def test_qos_3_is_invalid(self) -> None:
        """QoS 3 is not a valid MQTT QoS level."""
        with pytest.raises(ValidationError):
            MqttSettings(qos=3)  # type: ignore[arg-type]


class TestMqttSettingsSecretStr:
    """SecretStr handling for password field.

    Technique: Specification-based Testing.
    """

    def test_password_is_secret_str(self) -> None:
        """Password field stores a SecretStr."""
        s = MqttSettings(password="hunter2")
        assert isinstance(s.password, SecretStr)

    def test_password_str_does_not_expose_value(
        self,
    ) -> None:
        """str() representation hides the secret."""
        s = MqttSettings(password="hunter2")
        assert s.password is not None
        assert "hunter2" not in str(s.password)

    def test_password_get_secret_value(self) -> None:
        """get_secret_value() reveals the actual password."""
        s = MqttSettings(password="hunter2")
        assert s.password is not None
        assert s.password.get_secret_value() == "hunter2"


class TestLoggingSettingsDefaults:
    """Verify all logging default values.

    Technique: Specification-based Testing.
    """

    def test_level_defaults_to_info(self) -> None:
        """Default log level is INFO."""
        s = LoggingSettings()
        assert s.level == "INFO"

    def test_format_defaults_to_json(self) -> None:
        """Default format is json for container environments."""
        s = LoggingSettings()
        assert s.format == "json"

    def test_file_defaults_to_none(self) -> None:
        """Default file is None (stderr only)."""
        s = LoggingSettings()
        assert s.file is None

    def test_backup_count_defaults_to_3(self) -> None:
        """Default backup count is 3."""
        s = LoggingSettings()
        assert s.backup_count == 3


class TestLoggingSettingsValidation:
    """Field constraint validation for LoggingSettings.

    Technique: Boundary Value Analysis.
    """

    def test_invalid_level_rejected(self) -> None:
        """An unrecognized level string is rejected."""
        with pytest.raises(ValidationError):
            LoggingSettings(level="TRACE")  # type: ignore[arg-type]

    def test_invalid_format_rejected(self) -> None:
        """An unrecognized format string is rejected."""
        with pytest.raises(ValidationError):
            LoggingSettings(format="yaml")  # type: ignore[arg-type]

    def test_negative_backup_count_rejected(self) -> None:
        """Negative backup_count is rejected (ge=0)."""
        with pytest.raises(ValidationError):
            LoggingSettings(backup_count=-1)


class TestSettingsDefaults:
    """Verify root Settings default values.

    Technique: Specification-based Testing.
    """

    def test_mqtt_is_mqtt_settings_instance(self) -> None:
        """Default mqtt is an MqttSettings instance."""
        s = Settings(_env_file=None)
        assert isinstance(s.mqtt, MqttSettings)

    def test_logging_is_logging_settings_instance(
        self,
    ) -> None:
        """Default logging is a LoggingSettings instance."""
        s = Settings(_env_file=None)
        assert isinstance(s.logging, LoggingSettings)

    def test_mqtt_defaults_propagate(self) -> None:
        """Nested MQTT defaults are preserved."""
        s = Settings(_env_file=None)
        assert s.mqtt.host == "localhost"
        assert s.mqtt.port == 1883

    def test_logging_defaults_propagate(self) -> None:
        """Nested logging defaults are preserved."""
        s = Settings(_env_file=None)
        assert s.logging.level == "INFO"
        assert s.logging.format == "json"


class TestSettingsEnvOverride:
    """Environment variable override for Settings.

    Technique: Environment Override via monkeypatch.
    """

    def test_mqtt_host_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """MQTT__HOST env var overrides default host."""
        monkeypatch.setenv("MQTT__HOST", "broker.test")
        s = Settings(_env_file=None)
        assert s.mqtt.host == "broker.test"

    def test_logging_level_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """LOGGING__LEVEL env var overrides default level."""
        monkeypatch.setenv("LOGGING__LEVEL", "DEBUG")
        s = Settings(_env_file=None)
        assert s.logging.level == "DEBUG"

    def test_mqtt_port_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """MQTT__PORT env var overrides default port."""
        monkeypatch.setenv("MQTT__PORT", "8883")
        s = Settings(_env_file=None)
        assert s.mqtt.port == 8883

    def test_mqtt_password_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """MQTT__PASSWORD env var sets SecretStr password."""
        monkeypatch.setenv("MQTT__PASSWORD", "s3cret")
        s = Settings(_env_file=None)
        assert s.mqtt.password is not None
        assert s.mqtt.password.get_secret_value() == "s3cret"


class TestSettingsNestedDelimiter:
    """Verify __ delimiter works for nested env vars.

    Technique: Environment Override via monkeypatch.
    """

    def test_double_underscore_delimiter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Double-underscore delimiter maps to nested fields."""
        monkeypatch.setenv("MQTT__HOST", "nested.test")
        monkeypatch.setenv("LOGGING__FORMAT", "text")
        s = Settings(_env_file=None)
        assert s.mqtt.host == "nested.test"
        assert s.logging.format == "text"
