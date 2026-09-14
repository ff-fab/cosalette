"""Unit tests for MqttSettings MQTT 5 protocol and expiry fields.

Test Techniques Used:
    - Decision Table Testing: protocol_version x message_expiry_interval matrix
    - Boundary Value Analysis: expiry bounds (3, 2**32-1)
    - Equivalence Partitioning: valid/invalid protocol versions
    - Error Guessing: integer coercion, explicit default rejection
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from cosalette._settings import MqttSettings, Settings

pytestmark = pytest.mark.unit


class TestMqttSettingsProtocolExpiryDefaults:
    """Default values for the MQTT 5 protocol/expiry fields.

    Technique: Specification-based Testing.
    """

    def test_protocol_version_defaults_to_3_1_1(self) -> None:
        """Default protocol_version is MQTT 3.1.1 (backward compatible)."""
        s = MqttSettings()
        assert s.protocol_version == "3.1.1"

    def test_message_expiry_interval_defaults_to_86400(self) -> None:
        """Default message_expiry_interval is 86400 seconds (24h)."""
        s = MqttSettings()
        assert s.message_expiry_interval == 86400


class TestMqttSettingsProtocolVersionValidation:
    """Equivalence partitioning and BVA for protocol_version.

    Technique: Equivalence Partitioning, Boundary Value Analysis.
    """

    @pytest.mark.parametrize("value", ["3.1.1", "5"])
    def test_protocol_version_valid_literal_accepted(self, value: str) -> None:
        """Both supported literal protocol strings are accepted."""
        s = MqttSettings(protocol_version=value)
        assert s.protocol_version == value

    def test_protocol_version_integer_5_coerced_to_string(self) -> None:
        """Integer 5 (as parsed from TOML/JSON config) coerces to '5'.

        Technique: Error Guessing — config-file integer vs. string literal.
        """
        s = MqttSettings(protocol_version=5)
        assert s.protocol_version == "5"

    def test_protocol_version_float_5_rejected(self) -> None:
        """Floating-point 5.0 is not a valid protocol-version coercion.

        Technique: Equivalence Partitioning -- integer and float inputs are
        distinct config-value classes even when numerically equal.
        """
        with pytest.raises(ValidationError, match="integer 5"):
            MqttSettings(protocol_version=5.0)

    @pytest.mark.parametrize(
        "value",
        ["null", "", "4", 0, 2, 3, 2**32 - 1, 2**32],
        ids=[
            "string-null",
            "empty-string",
            "string-4",
            "int-0",
            "int-2",
            "int-3",
            "int-2**32-1",
            "int-2**32",
        ],
    )
    def test_protocol_version_invalid_value_rejected(self, value: object) -> None:
        """Values outside the {'3.1.1', '5'} literal set are rejected.

        Technique: Boundary Value Analysis — extreme integers and near-miss
        strings/ints around the one integer that coerces (5). Note ``3`` is
        deliberately *not* coerced to ``'3.1.1'`` — only ``5`` is special-cased.
        """
        with pytest.raises(ValidationError):
            MqttSettings(protocol_version=value)


class TestMqttSettingsMessageExpiryIntervalValidation:
    """Boundary Value Analysis for message_expiry_interval bounds.

    Values are combined with protocol_version='5' so the cross-field
    "requires MQTT 5" rule (tested separately below) does not mask the
    field's own ge/le boundaries.

    Technique: Boundary Value Analysis.
    """

    @pytest.mark.parametrize(
        "value", [3, 2**32 - 1], ids=["lower-bound-3", "upper-bound-2**32-1"]
    )
    def test_message_expiry_interval_boundary_valid(self, value: int) -> None:
        """3 and 4294967295 (2**32-1) are the min/max accepted values."""
        s = MqttSettings(protocol_version="5", message_expiry_interval=value)
        assert s.message_expiry_interval == value

    @pytest.mark.parametrize(
        "value",
        [None, "", 0, 2, 2**32],
        ids=["none", "empty-string", "int-0", "int-2", "int-2**32"],
    )
    def test_message_expiry_interval_boundary_invalid(self, value: object) -> None:
        """Below the ge=3 floor, above the le=2**32-1 ceiling, or a
        non-numeric value is rejected.

        Technique: Boundary Value Analysis + Error Guessing (None/'' inputs).
        """
        with pytest.raises(ValidationError):
            MqttSettings(
                protocol_version="5",
                message_expiry_interval=value,  # ty: ignore[invalid-argument-type]
            )


class TestMqttSettingsProtocolExpiryDecisionTable:
    """protocol_version x message_expiry_interval decision table.

    Technique: Decision Table Testing.
    """

    def test_explicit_message_expiry_interval_under_3_1_1_rejected(self) -> None:
        """Explicit message_expiry_interval requires protocol_version='5'."""
        with pytest.raises(ValidationError, match="requires protocol_version='5'"):
            MqttSettings(protocol_version="3.1.1", message_expiry_interval=3600)

    def test_explicit_default_message_expiry_interval_under_3_1_1_rejected(
        self,
    ) -> None:
        """Explicitly passing the default value (86400) is still "explicitly
        set" and is rejected under 3.1.1 — being equal to the default does
        not make it a no-op.

        Technique: Error Guessing — explicit-default is a common mistaken
        assumption that passing the default value is equivalent to omitting it.
        """
        with pytest.raises(ValidationError, match="requires protocol_version='5'"):
            MqttSettings(protocol_version="3.1.1", message_expiry_interval=86400)

    def test_implicit_default_message_expiry_interval_under_3_1_1_accepted(
        self,
    ) -> None:
        """Omitting message_expiry_interval entirely is fine under 3.1.1 —
        the cross-field validator only fires when the field was explicitly
        set (``model_fields_set``), not for the implicit default.
        """
        s = MqttSettings(protocol_version="3.1.1")
        assert s.message_expiry_interval == 86400

    def test_message_expiry_interval_with_protocol_5_accepted(self) -> None:
        """message_expiry_interval is honored once protocol_version='5'."""
        s = MqttSettings(protocol_version="5", message_expiry_interval=3600)
        assert s.protocol_version == "5"
        assert s.message_expiry_interval == 3600


class TestMqttSettingsValidateAssignment:
    """validate_assignment=True re-runs validators on attribute assignment.

    Technique: State Transition Testing.
    """

    def test_assigning_message_expiry_interval_under_3_1_1_rejected(self) -> None:
        """Assigning message_expiry_interval post-construction fails the same
        cross-field check as construction time, given protocol_version stays
        at the default '3.1.1'.
        """
        s = MqttSettings()
        with pytest.raises(ValidationError, match="requires protocol_version='5'"):
            s.message_expiry_interval = 3600

    def test_assigning_protocol_version_back_to_3_1_1_with_expiry_set_rejected(
        self,
    ) -> None:
        """Switching protocol_version from '5' back to '3.1.1' after
        message_expiry_interval was explicitly set is rejected — it would
        otherwise silently orphan the expiry setting under 3.1.1.
        """
        s = MqttSettings(protocol_version="5", message_expiry_interval=3600)
        with pytest.raises(ValidationError, match="requires protocol_version='5'"):
            s.protocol_version = "3.1.1"


class TestMqttSettingsProtocolExpiryEnvOverride:
    """Environment variable override for the MQTT 5 protocol/expiry fields.

    Technique: Environment Override via monkeypatch.
    """

    def test_protocol_version_and_expiry_from_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """MQTT__PROTOCOL_VERSION and MQTT__MESSAGE_EXPIRY_INTERVAL env vars
        set both nested fields together.
        """
        monkeypatch.setenv("MQTT__PROTOCOL_VERSION", "5")
        monkeypatch.setenv("MQTT__MESSAGE_EXPIRY_INTERVAL", "3600")
        s = Settings(_env_file=None)
        assert s.mqtt.protocol_version == "5"
        assert s.mqtt.message_expiry_interval == 3600


class TestMqttSettingsProtocolExpiryConfigFile:
    """Config-file loading for MQTT 5 protocol and expiry settings.

    Technique: Round-trip Testing -- TOML values are parsed through the
    production config source and validated by the nested MqttSettings model.
    """

    def test_protocol_version_and_expiry_from_toml_config_file(
        self,
        tmp_path: Path,
    ) -> None:
        """A TOML integer protocol version is coerced while expiry is loaded."""
        config_file = tmp_path / "mqtt.toml"
        config_file.write_text(
            "[mqtt]\nprotocol_version = 5\nmessage_expiry_interval = 3600\n",
            encoding="utf-8",
        )

        settings = Settings(_env_file=None, _config_file=config_file)

        assert settings.mqtt.protocol_version == "5"
        assert settings.mqtt.message_expiry_interval == 3600
