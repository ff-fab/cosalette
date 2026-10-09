"""Unit tests for cosalette._schema._enforcement — Schema enforcement types.

Test Techniques Used:
- Specification-based Testing: Verifying type contracts and defaults
- Equivalence Partitioning: Valid/invalid enforcement modes
- Error Guessing: Edge cases in violation formatting; a missing [schema]
  extra reported as a bad schema path (cos-c1jb.1)
- Decision Table: enforcement x path x on_publish x {yaml, jsonschema}
  installed -> startup outcome (cos-c1jb.2, cos-c1jb.5)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Literal

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


def _write_schema(path: Path, *, on_publish: bool) -> Path:
    """Write a one-channel ``vito2mqtt`` schema to *path* and return it.

    The content is JSON, which is also valid YAML, so one document serves
    both the ``.json`` and the ``.yaml`` branch of the loader.
    """
    doc = {
        "asyncapi": "3.0.0",
        "info": {"title": "vito2mqtt", "version": "0.2.0"},
        "x-cosalette-enforcement": {"mode": "warn", "on_publish": on_publish},
        "channels": {
            "temperatureState": {
                "address": "vito2mqtt/temperature/state",
                "x-cosalette-archetype": "telemetry",
                "messages": {
                    "reading": {
                        "payload": {
                            "type": "object",
                            "required": ["temperature"],
                            "properties": {"temperature": {"type": "number"}},
                        }
                    }
                },
            }
        },
    }
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


@pytest.fixture
def schemas_dir() -> Path:
    return Path(__file__).parent.parent.parent / "fixtures" / "schemas"


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
        settings = Settings(schema=SchemaSettings(enforcement="warn"))
        result = await load_and_validate_schema(frozenset(), settings, "testapp")
        assert result is None

    async def test_warn_mode_logs_violations(
        self, schemas_dir: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        settings = Settings(
            schema=SchemaSettings(
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
            schema=SchemaSettings(
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
            schema=SchemaSettings(
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
            schema=SchemaSettings(
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
            schema=SchemaSettings(
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
            schema=SchemaSettings(
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
            schema=SchemaSettings(
                enforcement="strict",
                path="/nonexistent/schema.yaml",
            )
        )
        with pytest.raises(SchemaViolationError, match="SCHEMA__PATH"):
            await load_and_validate_schema(frozenset(), settings, "testapp")


class TestSchemaLoadFailureAtStartup:
    """A schema that cannot load fails startup with an actionable message.

    Test Techniques Used:
        - Equivalence Partitioning: missing ``[schema]`` extra vs. unparsable
          schema file.
        - Error Guessing: the install hint used to be swallowed into a
          misleading ``check SCHEMA__PATH`` violation (cos-c1jb.1).
    """

    @pytest.mark.parametrize(
        ("missing_module", "on_publish"), [("yaml", False), ("jsonschema", True)]
    )
    async def test_missing_schema_extra_reports_install_hint(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        missing_module: str,
        on_publish: bool,
    ) -> None:
        """A dependency the configuration needs names itself and the extra."""
        # Arrange
        from cosalette.testing import AppHarness

        monkeypatch.setitem(sys.modules, missing_module, None)
        path = _write_schema(tmp_path / "schema.yaml", on_publish=on_publish)
        schema = SchemaSettings(enforcement="warn", path=str(path))
        harness = AppHarness.create(name="vito2mqtt", schema=schema)
        harness.trigger_shutdown()

        # Act / Assert
        with pytest.raises(
            ImportError,
            match=rf"missing: {missing_module}.*pip install cosalette\[schema\]",
        ):
            await harness.run()

    async def test_unparsable_schema_reports_path_setting(self, tmp_path: Path) -> None:
        """A file that is not YAML keeps the existing SCHEMA__PATH message."""
        # Arrange
        bad = tmp_path / "schema.yaml"
        bad.write_text("asyncapi: [unclosed\n")
        settings = Settings(schema=SchemaSettings(enforcement="warn", path=str(bad)))

        # Act / Assert
        with pytest.raises(SchemaViolationError, match="SCHEMA__PATH"):
            await load_and_validate_schema(frozenset(), settings, "testapp")


class TestOptionalDependenciesPerConfiguration:
    """Startup requires only the optional dependencies the configuration uses.

    PyYAML is needed only for a YAML schema file; jsonschema only when
    ``x-cosalette-enforcement.on_publish`` is true (cos-c1jb.2). A ``.json``
    schema file needs neither (cos-c1jb.5).

    Test Techniques Used:
        - Decision Table: enforcement x path x on_publish x installed
          {yaml, jsonschema} -> starts or fails with the missing module's
          install hint. Modules are blocked with ``sys.modules[name] = None``.
    """

    @pytest.mark.parametrize(
        ("enforcement", "suffix", "on_publish", "blocked", "missing"),
        [
            ("off", ".yaml", True, ("yaml", "jsonschema"), None),
            ("warn", None, True, ("yaml", "jsonschema"), None),
            ("warn", ".yaml", False, ("jsonschema",), None),
            ("strict", ".yaml", False, ("yaml",), "yaml"),
            ("warn", ".yaml", True, ("jsonschema",), "jsonschema"),
            ("warn", ".yaml", True, (), None),
            ("warn", ".json", False, ("yaml", "jsonschema"), None),
            ("strict", ".JSON", True, ("yaml", "jsonschema"), "jsonschema"),
        ],
        ids=[
            "off-no-check",
            "no-path-no-check",
            "yaml-default-without-jsonschema-starts",
            "yaml-without-pyyaml-fails",
            "on-publish-without-jsonschema-fails",
            "on-publish-with-jsonschema-starts",
            "json-without-either-starts",
            "json-on-publish-without-jsonschema-fails",
        ],
    )
    async def test_startup_outcome(  # noqa: PLR0913 - one argument per table column
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        enforcement: Literal["off", "warn", "strict"],
        suffix: str | None,
        on_publish: bool,
        blocked: tuple[str, ...],
        missing: str | None,
    ) -> None:
        # Arrange
        path = None
        if suffix is not None:
            path = _write_schema(tmp_path / f"schema{suffix}", on_publish=on_publish)
        settings = Settings(
            schema=SchemaSettings(
                enforcement=enforcement, path=None if path is None else str(path)
            )
        )
        for name in blocked:
            monkeypatch.setitem(sys.modules, name, None)
        registered = frozenset({"temperature"})

        # Act / Assert
        if missing is not None:
            with pytest.raises(ImportError, match=rf"missing: {missing}\)"):
                await load_and_validate_schema(registered, settings, "vito2mqtt")
            return
        result = await load_and_validate_schema(registered, settings, "vito2mqtt")
        if enforcement == "off" or path is None:
            assert result is None
        else:
            assert result is not None
            assert result.enforcement.on_publish is on_publish

    async def test_on_publish_with_jsonschema_validates_payloads(
        self, tmp_path: Path
    ) -> None:
        """With jsonschema installed, on_publish still validates payloads.

        Technique: Specification-based — the loaded registry drives the
        publish-time validator, which rejects a payload missing a field.
        """
        # Arrange
        from cosalette._schema._validator import PayloadValidator

        path = _write_schema(tmp_path / "schema.json", on_publish=True)
        settings = Settings(schema=SchemaSettings(enforcement="warn", path=str(path)))
        registry = await load_and_validate_schema(
            frozenset({"temperature"}), settings, "vito2mqtt"
        )
        assert registry is not None
        validator = PayloadValidator(registry)

        # Act
        issues = validator.validate("vito2mqtt/temperature/state", {"temp": 1})

        # Assert
        assert [i.channel_name for i in issues] == ["temperatureState"]
        valid = validator.validate("vito2mqtt/temperature/state", {"temperature": 1})
        assert valid == []


class TestNetworkFilterUsesIdentityNotPrefix:
    """ADR-072: the network-level slice is selected by identity, not by prefix.

    ``App._run_async`` used to hand ``load_and_validate_schema`` the resolved
    MQTT topic prefix. ``filter_for_app`` matches on ``channel.app_name``
    (``x-cosalette-app``), so an app whose ``mqtt.topic_prefix`` differed from
    its name filtered its network schema down to zero channels — and an empty
    registry produces zero violations, so strict enforcement silently stopped
    enforcing anything. This is the *opposite*-direction sibling of the
    discovery bug: an address used where an identity was required.

    Test Techniques Used:
        - Equivalence Partitioning: prefix == app name / prefix != app name /
          multi-segment prefix.
        - Specification-based Testing: ADR-072 identity-vs-address split at the
          `App._run_async` call site.
        - Error Guessing: the failure mode is silence (no violation raised),
          so the assertion is that strict mode still *raises*.
    """

    @staticmethod
    async def _run(topic_prefix: str | None, schema_path: Path) -> None:
        """Boot a `vito2mqtt` app with no registrations under *topic_prefix*."""
        from cosalette._settings import MqttSettings
        from cosalette.testing import AppHarness

        overrides: dict[str, Any] = {
            "schema": SchemaSettings(enforcement="strict", path=str(schema_path)),
        }
        if topic_prefix is not None:
            overrides["mqtt"] = MqttSettings(topic_prefix=topic_prefix)
        harness = AppHarness.create(name="vito2mqtt", **overrides)
        harness.trigger_shutdown()
        await harness.run()

    @pytest.mark.parametrize(
        "topic_prefix",
        [None, "vito2mqtt", "house", "house/vito"],
        ids=["unset", "same_as_name", "single_segment", "multi_segment"],
    )
    async def test_strict_enforcement_still_fires_under_any_prefix(
        self, topic_prefix: str | None, schemas_dir: Path
    ) -> None:
        """A network schema expecting devices the app never registers must raise.

        Technique: Equivalence Partitioning over the prefix-vs-identity
        relationship; the schema and registrations are held constant so the
        prefix is the only variable.
        """
        # Arrange / Act / Assert
        with pytest.raises(SchemaViolationError) as exc_info:
            await self._run(topic_prefix, schemas_dir / "network_basic.yaml")

        messages = " ".join(v.message for v in exc_info.value.violations)
        assert "temperature" in messages, messages
        # airthings2mqtt is a *different* app in the same network document and
        # must stay filtered out — identity filtering, not "no filtering".
        assert "airquality" not in messages, messages
