"""Unit tests for cosalette._schema._enforcement — Schema enforcement types.

Test Techniques Used:
- Specification-based Testing: Verifying type contracts and defaults
- Equivalence Partitioning: Valid/invalid enforcement modes
- Error Guessing: Edge cases in violation formatting
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import pytest
from pydantic import ValidationError

from cosalette._schema import (
    ChannelSchema,
    EnforcementConfig,
    SchemaRegistry,
)
from cosalette._schema._enforcement import (
    SchemaViolation,
    SchemaViolationError,
    _validate_registrations,
    load_and_validate_schema,
)
from cosalette._settings import SchemaSettings, Settings

pytestmark = pytest.mark.unit


class TestSchemaSettings:
    def test_defaults(self) -> None:
        s = SchemaSettings()
        assert s.enforcement == "off"
        assert s.path is None

    def test_enforcement_strict(self) -> None:
        s = SchemaSettings(enforcement="strict")
        assert s.enforcement == "strict"

    def test_invalid_enforcement_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SchemaSettings(enforcement="invalid")  # ty: ignore[invalid-argument-type]

    def test_settings_includes_schema(self) -> None:
        s = Settings()
        assert s.schema_.enforcement == "off"
        assert s.schema_.path is None

    def test_settings_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SCHEMA__ENFORCEMENT", "strict")
        monkeypatch.setenv("SCHEMA__PATH", "/etc/cosalette/schema.yaml")
        s = Settings()
        assert s.schema_.enforcement == "strict"
        assert s.schema_.path == "/etc/cosalette/schema.yaml"


class TestSchemaViolation:
    def test_construction(self) -> None:
        v = SchemaViolation(
            category="missing_channel",
            message="Missing channel 'temperatureState'",
            channel_name="temperatureState",
        )
        assert v.category == "missing_channel"
        assert v.message == "Missing channel 'temperatureState'"
        assert v.channel_name == "temperatureState"

    def test_default_channel_name_is_none(self) -> None:
        v = SchemaViolation(category="scope_violation", message="test")
        assert v.channel_name is None


class TestSchemaViolationError:
    def test_single_violation_str(self) -> None:
        v = SchemaViolation(category="missing_channel", message="Missing 'temp'")
        err = SchemaViolationError(violations=[v])
        assert "1 violation" in str(err)
        assert "Missing 'temp'" in str(err)

    def test_multiple_violations_str(self) -> None:
        v1 = SchemaViolation(category="missing_channel", message="Missing 'a'")
        v2 = SchemaViolation(category="scope_violation", message="Missing 'b'")
        err = SchemaViolationError(violations=[v1, v2])
        result = str(err)
        assert "2 violation" in result
        assert "Missing 'a'" in result
        assert "Missing 'b'" in result


def _make_registry(
    channels: dict[str, ChannelSchema] | None = None,
    device_names: frozenset[str] | None = None,
    app_name: str = "testapp",
) -> SchemaRegistry:
    """Helper to build minimal SchemaRegistry for tests."""
    ch = channels or {}
    return SchemaRegistry(
        app_name=app_name,
        app_version="1.0.0",
        asyncapi_version="3.0.0",
        enforcement=EnforcementConfig(mode="strict"),
        channels=ch,
        operations={},
        component_schemas={},
        device_names=device_names if device_names is not None else frozenset(),
    )


def _make_channel(
    address: str,
    address_template: str | None = None,
    scope: str | None = None,
    direction: Literal["send", "receive", "both"] = "send",
) -> ChannelSchema:
    """Helper to build minimal ChannelSchema for tests."""
    return ChannelSchema(
        address=address,
        address_template=address_template or address,
        direction=direction,
        scope=scope,
    )


class TestValidateRegistrations:
    """Tests for _validate_registrations.

    Test Techniques:
    - Specification-based: verifying validation contracts
    - Equivalence Partitioning: matching/non-matching registrations
    - Decision Table: combinations of registered names vs schema expectations
    """

    def test_empty_schema_no_violations(self) -> None:
        registry = _make_registry()
        result = _validate_registrations(frozenset(), registry)
        assert result == []

    def test_matching_device_no_violations(self) -> None:
        registry = _make_registry(
            channels={
                "tempState": _make_channel(
                    address="testapp/temperature/state",
                    address_template="{appName}/{deviceName}/state",
                )
            },
            device_names=frozenset({"temperature"}),
        )
        result = _validate_registrations(frozenset({"temperature"}), registry)
        assert result == []

    def test_missing_device_produces_violation(self) -> None:
        registry = _make_registry(
            channels={
                "tempState": _make_channel(
                    address="testapp/temperature/state",
                    address_template="{appName}/{deviceName}/state",
                )
            },
            device_names=frozenset({"temperature"}),
        )
        result = _validate_registrations(frozenset(), registry)
        assert len(result) == 1
        assert result[0].category == "missing_channel"
        assert "temperature" in result[0].message

    def test_scope_all_apps_mandatory_channel_violation(self) -> None:
        registry = _make_registry(
            channels={
                "appDiag": _make_channel(
                    address="testapp/diagnostics",
                    address_template="{appName}/diagnostics",
                    scope="all_apps",
                )
            },
        )
        result = _validate_registrations(frozenset(), registry)
        assert len(result) == 1
        assert result[0].category == "scope_violation"
        assert "appDiag" in result[0].message

    def test_scope_all_apps_status_auto_wired_skipped(self) -> None:
        """Framework auto-wires status — should not produce violations."""
        registry = _make_registry(
            channels={
                "appStatus": _make_channel(
                    address="testapp/status",
                    address_template="{appName}/status",
                    scope="all_apps",
                )
            },
        )
        result = _validate_registrations(frozenset(), registry)
        assert result == []

    def test_scope_all_apps_availability_auto_wired_skipped(self) -> None:
        registry = _make_registry(
            channels={
                "appAvail": _make_channel(
                    address="testapp/availability",
                    address_template="{appName}/availability",
                    scope="all_apps",
                )
            },
        )
        result = _validate_registrations(frozenset(), registry)
        assert result == []

    def test_scope_all_apps_status_template_auto_wired_skipped(self) -> None:
        """Network schema: address uses {appName} placeholder, not resolved prefix."""
        registry = _make_registry(
            channels={
                "appStatus": _make_channel(
                    address="{appName}/status",
                    address_template="{appName}/status",
                    scope="all_apps",
                )
            },
        )
        result = _validate_registrations(frozenset(), registry)
        assert result == []

    def test_scope_all_apps_availability_template_auto_wired_skipped(self) -> None:
        """Network schema: availability with {appName} placeholder."""
        registry = _make_registry(
            channels={
                "appAvail": _make_channel(
                    address="{appName}/availability",
                    address_template="{appName}/availability",
                    scope="all_apps",
                )
            },
        )
        result = _validate_registrations(frozenset(), registry)
        assert result == []

    def test_multiple_violations_sorted(self) -> None:
        registry = _make_registry(
            channels={
                "tempState": _make_channel(
                    address="testapp/temp/state",
                    address_template="{appName}/{deviceName}/state",
                ),
                "humState": _make_channel(
                    address="testapp/hum/state",
                    address_template="{appName}/{deviceName}/state",
                ),
            },
            device_names=frozenset({"temp", "hum"}),
        )
        result = _validate_registrations(frozenset(), registry)
        assert len(result) == 2
        # Sorted order
        assert "hum" in result[0].message
        assert "temp" in result[1].message

    def test_extra_registrations_not_in_schema_ignored(self) -> None:
        """Extra app registrations beyond schema are fine — schema is minimum spec."""
        registry = _make_registry(
            channels={
                "tempState": _make_channel(
                    address="testapp/temperature/state",
                    address_template="{appName}/{deviceName}/state",
                )
            },
            device_names=frozenset({"temperature"}),
        )
        # App registers temperature AND humidity — humidity not in schema, no problem
        registered = frozenset({"temperature", "humidity"})
        result = _validate_registrations(registered, registry)
        assert result == []


@pytest.fixture
def schemas_dir() -> Path:
    return Path(__file__).parent.parent / "fixtures" / "schemas"


class TestLoadAndValidateSchema:
    """Integration tests for load_and_validate_schema.

    Test Techniques:
    - Decision Table: enforcement mode × schema presence × violations
    - Specification-based: verifying the load-filter-validate pipeline
    """

    async def test_off_mode_returns_none(self) -> None:
        settings = Settings()  # default enforcement="off"
        result = await load_and_validate_schema(frozenset(), settings, "testapp")
        assert result is None

    async def test_no_path_returns_none(self) -> None:
        settings = Settings(schema=SchemaSettings(enforcement="warn"))  # ty: ignore[unknown-argument]
        result = await load_and_validate_schema(frozenset(), settings, "testapp")
        assert result is None

    async def test_warn_mode_logs_violations(
        self, schemas_dir: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        settings = Settings(
            schema=SchemaSettings(  # ty: ignore[unknown-argument]
                enforcement="warn",
                path=str(schemas_dir / "enforcement_basic.yaml"),
            )
        )
        # enforcement_basic schema expects "temperature" device
        result = await load_and_validate_schema(frozenset(), settings, "vito2mqtt")
        assert result is not None
        assert "Schema violation" in caplog.text

    async def test_warn_mode_returns_registry(self, schemas_dir: Path) -> None:
        settings = Settings(
            schema=SchemaSettings(  # ty: ignore[unknown-argument]
                enforcement="warn",
                path=str(schemas_dir / "enforcement_basic.yaml"),
            )
        )
        result = await load_and_validate_schema(
            frozenset({"temperature"}), settings, "vito2mqtt"
        )
        assert result is not None
        assert result.app_name == "vito2mqtt"

    async def test_strict_mode_raises_on_violations(self, schemas_dir: Path) -> None:
        settings = Settings(
            schema=SchemaSettings(  # ty: ignore[unknown-argument]
                enforcement="strict",
                path=str(schemas_dir / "enforcement_basic.yaml"),
            )
        )
        with pytest.raises(SchemaViolationError) as exc_info:
            await load_and_validate_schema(frozenset(), settings, "vito2mqtt")
        assert len(exc_info.value.violations) > 0

    async def test_strict_mode_passes_with_matching_registrations(
        self, schemas_dir: Path
    ) -> None:
        settings = Settings(
            schema=SchemaSettings(  # ty: ignore[unknown-argument]
                enforcement="strict",
                path=str(schemas_dir / "enforcement_basic.yaml"),
            )
        )
        result = await load_and_validate_schema(
            frozenset({"temperature"}), settings, "vito2mqtt"
        )
        assert result is not None

    async def test_network_schema_filters_to_app(self, schemas_dir: Path) -> None:
        settings = Settings(
            schema=SchemaSettings(  # ty: ignore[unknown-argument]
                enforcement="warn",
                path=str(schemas_dir / "network_basic.yaml"),
            )
        )
        result = await load_and_validate_schema(
            frozenset({"temperature"}), settings, "vito2mqtt"
        )
        assert result is not None
        # Network schema filtered to vito2mqtt's channels only
        assert result.app_name == "vito2mqtt"

    async def test_network_schema_auto_wired_no_false_violation(
        self, schemas_dir: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Network schema {appName}/status must not produce a scope_violation."""
        settings = Settings(
            schema=SchemaSettings(  # ty: ignore[unknown-argument]
                enforcement="warn",
                path=str(schemas_dir / "network_basic.yaml"),
            )
        )
        # Register all expected devices so only auto-wired channels remain
        await load_and_validate_schema(
            frozenset({"temperature", "valve"}), settings, "vito2mqtt"
        )
        assert "scope_violation" not in caplog.text
        assert "appStatus" not in caplog.text

    async def test_load_bad_path_raises_config_error(self) -> None:
        """Invalid schema path should raise without leaking filesystem details."""
        settings = Settings(
            schema=SchemaSettings(  # ty: ignore[unknown-argument]
                enforcement="strict",
                path="/nonexistent/schema.yaml",
            )
        )
        with pytest.raises(SchemaViolationError, match="SCHEMA__PATH"):
            await load_and_validate_schema(frozenset(), settings, "testapp")
