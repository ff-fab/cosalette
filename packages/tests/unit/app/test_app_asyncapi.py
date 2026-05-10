"""Tests for App.asyncapi() — canonical AsyncAPI contract generation (cos-bnq).

Test Techniques Used:
    - Specification-based Testing: structural contracts for AsyncAPI output.
    - State-based Testing: payload_model/state_model schema inference.
    - Behavioural Testing: deterministic ordering, contract-version presence.
    - Equivalence Partitioning: device / telemetry / command / empty app.

See Also:
    cos-bnq — Canonical AsyncAPI introspection epic.
"""

from __future__ import annotations

from typing import Annotated, Any

import pytest
from pydantic import BaseModel

from cosalette._app import App
from cosalette._context import DeviceContext
from cosalette.mqtt import Payload

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Module-level models for annotation inference tests
# ---------------------------------------------------------------------------


class _HumidityReading(BaseModel):
    percent: float


# Module-level models used in command schema regression tests
class _SetpointCmd(BaseModel):
    temperature: float
    unit: str


class _LightCmd(BaseModel):
    brightness: int
    color: str


class _InputCmd(BaseModel):
    action: str


class _OutputState(BaseModel):
    result: int


# Module-level model for command return-annotation fallback test
class _CmdResultState(BaseModel):
    value: float


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def empty_app() -> App:
    """App with no registrations."""
    return App(name="myapp", version="1.2.3")


@pytest.fixture
def telemetry_app() -> App:
    """App with a single typed telemetry registration."""
    app = App(name="bridge", version="0.5.0")

    class TempReading(BaseModel):
        celsius: float

    @app.telemetry("temperature", interval=30, state_model=TempReading)
    async def temp() -> dict[str, Any]:
        return {}

    return app


@pytest.fixture
def command_app() -> App:
    """App with a typed command registration."""
    app = App(name="bridge", version="0.5.0")

    class ValveCommand(BaseModel):
        open: bool

    @app.command("valve", payload_model=ValveCommand)
    async def valve_handler(payload: str) -> None:
        pass

    return app


@pytest.fixture
def device_app() -> App:
    """App with a bare device registration."""
    app = App(name="bridge", version="0.5.0")

    @app.device("sensor")
    async def sensor(ctx: DeviceContext) -> None:
        pass

    return app


@pytest.fixture
def mixed_app() -> App:
    """App with device, telemetry, and command registrations."""
    app = App(name="vito2mqtt", version="0.2.0")

    class TempState(BaseModel):
        celsius: float

    class ValveCmd(BaseModel):
        open: bool

    @app.telemetry(
        "temperature", interval=300, state_model=TempState, summary="Room temp"
    )
    async def temp() -> dict[str, Any]:
        return {}

    @app.command("valve", payload_model=ValveCmd, summary="Toggle valve")
    async def valve(payload: str) -> None:
        pass

    @app.device("sensor")
    async def sensor(ctx: DeviceContext) -> None:
        pass

    return app


@pytest.fixture
def annotated_telemetry_app() -> App:
    """Telemetry using return annotation (no explicit state_model)."""
    app = App(name="bridge", version="0.5.0")

    @app.telemetry("humidity", interval=60)
    async def humidity() -> _HumidityReading:
        return _HumidityReading(percent=50.0)

    return app


# ---------------------------------------------------------------------------
# cos-bnq.1 — app.asyncapi() returns deterministic Python dict
# ---------------------------------------------------------------------------


class TestAsyncapiReturnType:
    """app.asyncapi() returns a plain dict without side effects."""

    def test_returns_dict(self, empty_app: App) -> None:
        """Should return a plain dict."""
        result = empty_app.asyncapi()
        assert isinstance(result, dict)

    def test_no_public_yaml_accessor(self, empty_app: App) -> None:
        """The method returns a dict — not a string or YAML object."""
        result = empty_app.asyncapi()
        assert not isinstance(result, str)
        assert not isinstance(result, bytes)

    def test_asyncapi_version_field(self, empty_app: App) -> None:
        """Document must declare asyncapi: 3.0.0."""
        result = empty_app.asyncapi()
        assert result["asyncapi"] == "3.0.0"

    def test_info_contains_app_name(self, empty_app: App) -> None:
        """info.title must equal app.name."""
        result = empty_app.asyncapi()
        assert result["info"]["title"] == "myapp"

    def test_info_contains_app_version(self, empty_app: App) -> None:
        """info.version must equal app.version."""
        result = empty_app.asyncapi()
        assert result["info"]["version"] == "1.2.3"

    def test_empty_app_has_no_channels(self, empty_app: App) -> None:
        """App with no registrations emits no channels key."""
        result = empty_app.asyncapi()
        assert "channels" not in result
        assert "operations" not in result

    def test_idempotent(self, telemetry_app: App) -> None:
        """Calling asyncapi() twice returns equal dicts."""
        first = telemetry_app.asyncapi()
        second = telemetry_app.asyncapi()
        assert first == second


# ---------------------------------------------------------------------------
# cos-bnq.5 — x-cosalette-contract-version
# ---------------------------------------------------------------------------


class TestContractVersion:
    """x-cosalette-contract-version is present in every generated document."""

    def test_contract_version_present(self, empty_app: App) -> None:
        """info must include x-cosalette-contract-version."""
        result = empty_app.asyncapi()
        assert "x-cosalette-contract-version" in result["info"]

    def test_contract_version_is_string(self, empty_app: App) -> None:
        """x-cosalette-contract-version value must be a string."""
        result = empty_app.asyncapi()
        assert isinstance(result["info"]["x-cosalette-contract-version"], str)

    def test_contract_version_stable(self, empty_app: App, telemetry_app: App) -> None:
        """Contract version must be same across different app configurations."""
        v1 = empty_app.asyncapi()["info"]["x-cosalette-contract-version"]
        v2 = telemetry_app.asyncapi()["info"]["x-cosalette-contract-version"]
        assert v1 == v2

    def test_contract_version_distinct_from_app_version(self, empty_app: App) -> None:
        """x-cosalette-contract-version is independent from app version."""
        result = empty_app.asyncapi()
        info = result["info"]
        assert info["x-cosalette-contract-version"] != info["version"]


# ---------------------------------------------------------------------------
# cos-bnq.2 — Rich schema content
# ---------------------------------------------------------------------------


class TestTelemetryChannel:
    """Telemetry registrations map to send channels."""

    def test_telemetry_channel_exists(self, telemetry_app: App) -> None:
        """Should emit a 'temperatureState' channel."""
        channels = telemetry_app.asyncapi()["channels"]
        assert "temperatureState" in channels

    def test_telemetry_address(self, telemetry_app: App) -> None:
        """Channel address should be {app}/{name}/state."""
        channel = telemetry_app.asyncapi()["channels"]["temperatureState"]
        assert channel["address"] == "bridge/temperature/state"

    def test_telemetry_operation_is_send(self, telemetry_app: App) -> None:
        """Telemetry operation action should be 'send'."""
        ops = telemetry_app.asyncapi()["operations"]
        assert "publishTemperatureState" in ops
        assert ops["publishTemperatureState"]["action"] == "send"

    def test_telemetry_operation_refs_channel(self, telemetry_app: App) -> None:
        """Operation should $ref the channel."""
        ops = telemetry_app.asyncapi()["operations"]
        assert ops["publishTemperatureState"]["channel"] == {
            "$ref": "#/channels/temperatureState"
        }

    def test_telemetry_archetype_extension(self, telemetry_app: App) -> None:
        """Channel should carry x-cosalette-archetype=telemetry."""
        channel = telemetry_app.asyncapi()["channels"]["temperatureState"]
        assert channel["x-cosalette-archetype"] == "telemetry"

    def test_telemetry_typed_payload(self, telemetry_app: App) -> None:
        """state_model generates a typed payload schema, not bare object."""
        doc = telemetry_app.asyncapi()
        payload = doc["channels"]["temperatureState"]["messages"]["message"]["payload"]
        # TempReading has one property: celsius (float)
        assert payload.get("type") == "object"
        assert "celsius" in payload.get("properties", {})


class TestCommandChannel:
    """Command registrations map to receive channels."""

    def test_command_channel_exists(self, command_app: App) -> None:
        """Should emit a 'valveCommand' channel."""
        channels = command_app.asyncapi()["channels"]
        assert "valveCommand" in channels

    def test_command_address(self, command_app: App) -> None:
        """Channel address should be {app}/{name}/set."""
        channel = command_app.asyncapi()["channels"]["valveCommand"]
        assert channel["address"] == "bridge/valve/set"

    def test_command_operation_is_receive(self, command_app: App) -> None:
        """Command operation action should be 'receive'."""
        ops = command_app.asyncapi()["operations"]
        assert "receiveValveCommand" in ops
        assert ops["receiveValveCommand"]["action"] == "receive"

    def test_command_archetype_extension(self, command_app: App) -> None:
        """Channel should carry x-cosalette-archetype=command."""
        channel = command_app.asyncapi()["channels"]["valveCommand"]
        assert channel["x-cosalette-archetype"] == "command"

    def test_command_typed_payload(self, command_app: App) -> None:
        """payload_model generates a typed payload schema."""
        doc = command_app.asyncapi()
        payload = doc["channels"]["valveCommand"]["messages"]["message"]["payload"]
        assert payload.get("type") == "object"
        assert "open" in payload.get("properties", {})


class TestDeviceChannel:
    """Device registrations map to send channels."""

    def test_device_channel_exists(self, device_app: App) -> None:
        """Should emit a 'sensorState' channel."""
        channels = device_app.asyncapi()["channels"]
        assert "sensorState" in channels

    def test_device_address(self, device_app: App) -> None:
        """Channel address should be {app}/{name}/state."""
        channel = device_app.asyncapi()["channels"]["sensorState"]
        assert channel["address"] == "bridge/sensor/state"

    def test_device_archetype_extension(self, device_app: App) -> None:
        """Channel should carry x-cosalette-archetype=device."""
        channel = device_app.asyncapi()["channels"]["sensorState"]
        assert channel["x-cosalette-archetype"] == "device"

    def test_device_generic_payload(self, device_app: App) -> None:
        """Device without explicit model uses generic object schema."""
        doc = device_app.asyncapi()
        payload = doc["channels"]["sensorState"]["messages"]["message"]["payload"]
        assert payload == {"type": "object"}


class TestAnnotationFallback:
    """Handler return annotation is used when no explicit model is set."""

    def test_annotation_schema_generated(self, annotated_telemetry_app: App) -> None:
        """Return annotation on handler generates typed payload."""
        doc = annotated_telemetry_app.asyncapi()
        payload = doc["channels"]["humidityState"]["messages"]["message"]["payload"]
        # HumidityReading dataclass has percent: float
        assert payload.get("type") == "object"
        assert "percent" in payload.get("properties", {})


class TestExplicitModelWins:
    """Explicit decorator metadata wins over annotation inference."""

    def test_explicit_state_model_wins(self) -> None:
        """state_model on @telemetry overrides handler return annotation."""
        app = App(name="test", version="0.1.0")

        class ExplicitModel(BaseModel):
            value: int

        class AnnotationModel(BaseModel):
            other_field: str

        @app.telemetry("sensor", interval=10, state_model=ExplicitModel)
        async def sensor() -> AnnotationModel:
            return AnnotationModel(other_field="x")

        doc = app.asyncapi()
        payload = doc["channels"]["sensorState"]["messages"]["message"]["payload"]
        # ExplicitModel has 'value', AnnotationModel has 'other_field'
        assert "value" in payload.get("properties", {})
        assert "other_field" not in payload.get("properties", {})

    def test_explicit_payload_model_wins(self) -> None:
        """payload_model on @command overrides handler return annotation."""
        app = App(name="test", version="0.1.0")

        class ExplicitCmd(BaseModel):
            speed: int

        class AnnotationReturn(BaseModel):
            status: str

        @app.command("motor", payload_model=ExplicitCmd)
        async def motor(payload: str) -> AnnotationReturn:
            return AnnotationReturn(status="ok")

        doc = app.asyncapi()
        payload = doc["channels"]["motorCommand"]["messages"]["message"]["payload"]
        assert "speed" in payload.get("properties", {})
        assert "status" not in payload.get("properties", {})


class TestMetadataFields:
    """Tags, summary, behavior, effects appear in channels and operations."""

    def test_summary_in_channel(self, mixed_app: App) -> None:
        """x-cosalette-summary is set on channel when summary provided."""
        doc = mixed_app.asyncapi()
        channel = doc["channels"]["temperatureState"]
        assert channel.get("x-cosalette-summary") == "Room temp"

    def test_summary_in_operation(self, mixed_app: App) -> None:
        """summary also appears on operation when provided."""
        doc = mixed_app.asyncapi()
        op = doc["operations"]["publishTemperatureState"]
        assert op.get("summary") == "Room temp"

    def test_tags_in_channel(self) -> None:
        """tags tuple on registration appears as list in channel.

        Verifies the tags path through build_app_asyncapi by injecting a
        synthetic registration directly (tags is not yet exposed on all
        decorator signatures but is present on _DeviceRegistration).
        """
        app = App(name="test", version="0.1.0")

        # Register a bare device (no tags) — then monkey-patch tags onto the reg
        @app.device("cpu")
        async def cpu_handler(ctx: DeviceContext) -> None:
            pass

        from dataclasses import replace

        app._devices[0] = replace(app._devices[0], tags=("hardware", "metrics"))

        doc = app.asyncapi()
        channel = doc["channels"]["cpuState"]
        assert "tags" in channel
        tag_names = [t["name"] for t in channel["tags"]]
        assert "hardware" in tag_names
        assert "metrics" in tag_names

    def test_behavior_in_channel(self) -> None:
        """behavior list appears in x-cosalette-behavior extension."""
        app = App(name="test", version="0.1.0")

        @app.device("fan", behavior=["Reports fan speed every 5s"])
        async def fan(ctx: DeviceContext) -> None:
            pass

        doc = app.asyncapi()
        channel = doc["channels"]["fanState"]
        assert channel.get("x-cosalette-behavior") == ["Reports fan speed every 5s"]

    def test_effects_in_channel(self) -> None:
        """effects list appears in x-cosalette-effects extension."""
        app = App(name="test", version="0.1.0")

        @app.command("relay", effects=["Opens/closes relay"])
        async def relay(payload: str) -> None:
            pass

        doc = app.asyncapi()
        channel = doc["channels"]["relayCommand"]
        assert channel.get("x-cosalette-effects") == ["Opens/closes relay"]


class TestComponents:
    """JSON Schema $defs are promoted to components/schemas."""

    def test_nested_model_defs_in_components(self) -> None:
        """Nested Pydantic models (with $defs) appear in components/schemas."""
        app = App(name="test", version="0.1.0")

        class Inner(BaseModel):
            x: float

        class Outer(BaseModel):
            inner: Inner
            label: str

        @app.telemetry("data", interval=5, state_model=Outer)
        async def data() -> dict[str, Any]:
            return {}

        doc = app.asyncapi()
        # Outer references Inner via $defs; these should surface in components
        assert "components" in doc
        assert "schemas" in doc["components"]
        # Inner should be promoted
        assert "Inner" in doc["components"]["schemas"]


# ---------------------------------------------------------------------------
# cos-bnq.4 — Deterministic ordering
# ---------------------------------------------------------------------------


class TestDeterministicOutput:
    """Channels, operations, and component schemas are sorted alphabetically."""

    def test_channels_sorted(self, mixed_app: App) -> None:
        """Channels dict keys are in alphabetical order."""
        channels = list(mixed_app.asyncapi()["channels"].keys())
        assert channels == sorted(channels)

    def test_operations_sorted(self, mixed_app: App) -> None:
        """Operations dict keys are in alphabetical order."""
        ops = list(mixed_app.asyncapi()["operations"].keys())
        assert ops == sorted(ops)

    def test_component_schemas_sorted(self) -> None:
        """Component schema keys are in alphabetical order."""
        app = App(name="test", version="0.1.0")

        class B(BaseModel):
            v: int

        class A(BaseModel):
            nested: B

        @app.telemetry("x", interval=5, state_model=A)
        async def x() -> dict[str, Any]:
            return {}

        components = app.asyncapi().get("components", {}).get("schemas", {})
        keys = list(components.keys())
        assert keys == sorted(keys)

    def test_repeated_calls_stable(self, mixed_app: App) -> None:
        """Output is stable across multiple calls (no ordering drift)."""
        first = mixed_app.asyncapi()
        second = mixed_app.asyncapi()
        assert list(first["channels"].keys()) == list(second["channels"].keys())
        assert list(first["operations"].keys()) == list(second["operations"].keys())


# ---------------------------------------------------------------------------
# cos-bnq.3 — CLI dump path uses canonical builder (integration smoke)
# ---------------------------------------------------------------------------


class TestCliDumpIntegration:
    """CLI dump command produces AsyncAPI from app.asyncapi() canonical builder."""

    def test_cli_dump_includes_contract_version(self, mixed_app: App) -> None:
        """CLI dump output includes x-cosalette-contract-version in info."""
        from unittest.mock import patch

        import yaml
        from typer.testing import CliRunner

        from cosalette._schema._cli import schema_app

        runner = CliRunner()
        with patch("cosalette._schema._cli._import_app", return_value=mixed_app):
            result = runner.invoke(schema_app, ["dump", "--app", "dummy:app"])

        assert result.exit_code == 0
        doc = yaml.safe_load(result.stdout)
        assert "x-cosalette-contract-version" in doc["info"]

    def test_cli_dump_includes_typed_payload(self, mixed_app: App) -> None:
        """CLI dump output includes typed payload schema properties."""
        from unittest.mock import patch

        import yaml
        from typer.testing import CliRunner

        from cosalette._schema._cli import schema_app

        runner = CliRunner()
        with patch("cosalette._schema._cli._import_app", return_value=mixed_app):
            result = runner.invoke(schema_app, ["dump", "--app", "dummy:app"])

        assert result.exit_code == 0
        doc = yaml.safe_load(result.stdout)
        temp_channel = doc["channels"]["temperatureState"]
        payload = temp_channel["messages"]["message"]["payload"]
        # TempState has celsius property
        assert "celsius" in payload.get("properties", {})


# ---------------------------------------------------------------------------
# cos-bnq.3 — MCP manifest path uses canonical builder
# ---------------------------------------------------------------------------


class TestMcpManifestIntegration:
    """MCP cosalette_manifest returns AsyncAPI JSON from app.asyncapi()."""

    def test_mcp_manifest_returns_asyncapi(self, mixed_app: App) -> None:
        """cosalette_manifest tool should return AsyncAPI document, not snapshot."""
        import json
        from unittest.mock import MagicMock, patch

        from cosalette._mcp._introspect import register_introspect_tools

        mcp = MagicMock()
        tools: dict[str, Any] = {}

        def capture_tool() -> Any:
            def decorator(fn: Any) -> Any:
                tools[fn.__name__] = fn
                return fn

            return decorator

        mcp.tool = capture_tool
        register_introspect_tools(mcp)

        with patch(
            "cosalette._mcp._introspect._import_app",
            return_value=(mixed_app, None),
        ):
            manifest_fn = tools["cosalette_manifest"]
            result = manifest_fn("dummy:app")

        doc = json.loads(result)
        # Must be AsyncAPI, not a snapshot
        assert "asyncapi" in doc
        assert doc["asyncapi"] == "3.0.0"
        assert "x-cosalette-contract-version" in doc["info"]
        assert "channels" in doc
        assert "operations" in doc


# ---------------------------------------------------------------------------
# cos-bnq — Command schema precedence regression tests
# ---------------------------------------------------------------------------


class TestCommandSchemaPreference:
    """Regression tests for command input payload schema inference priority.

    Guards against the bug where state_model silently overrode payload_model
    for command channel payloads when both were provided.
    """

    def test_payload_model_wins_over_state_model(self) -> None:
        """Command with both payload_model and state_model uses payload_model.

        Regression: previously state_model or payload_model ... evaluated
        state_model first, so state_model incorrectly overrode payload_model
        for command channels.
        """
        app = App(name="test", version="0.1.0")

        @app.command("gate", payload_model=_InputCmd, state_model=_OutputState)
        async def gate(payload: str) -> None:
            pass

        doc = app.asyncapi()
        payload = doc["channels"]["gateCommand"]["messages"]["message"]["payload"]
        # payload_model (_InputCmd) must be used — not state_model (_OutputState)
        assert "action" in payload.get("properties", {})
        assert "result" not in payload.get("properties", {})

    def test_command_infers_typed_payload_from_named_param(self) -> None:
        """Command infers payload schema from `payload: SomeModel` annotation.

        When payload_model is not set but the handler has a `payload: T`
        parameter with a non-str type, the schema is inferred from T.
        """
        app = App(name="test", version="0.1.0")

        @app.command("setpoint")
        async def setpoint(payload: _SetpointCmd) -> None:
            pass

        doc = app.asyncapi()
        payload_schema = doc["channels"]["setpointCommand"]["messages"]["message"][
            "payload"
        ]
        assert payload_schema.get("type") == "object"
        assert "temperature" in payload_schema.get("properties", {})
        assert "unit" in payload_schema.get("properties", {})

    def test_command_infers_typed_payload_from_annotated_marker(self) -> None:
        """Command infers payload schema from Annotated[T, Payload()] annotation."""
        app = App(name="test", version="0.1.0")

        @app.command("light")
        async def light(cmd: Annotated[_LightCmd, Payload()]) -> None:
            pass

        doc = app.asyncapi()
        payload_schema = doc["channels"]["lightCommand"]["messages"]["message"][
            "payload"
        ]
        assert payload_schema.get("type") == "object"
        assert "brightness" in payload_schema.get("properties", {})
        assert "color" in payload_schema.get("properties", {})

    def test_command_with_only_str_payload_uses_generic_object(self) -> None:
        """Command with `payload: str` (raw) falls back to generic object schema."""
        app = App(name="test", version="0.1.0")

        @app.command("raw")
        async def raw_cmd(payload: str) -> None:
            pass

        doc = app.asyncapi()
        payload_schema = doc["channels"]["rawCommand"]["messages"]["message"]["payload"]
        assert payload_schema == {"type": "object"}


# ---------------------------------------------------------------------------
# cos-bnq — Regression: nested $ref must point to components/schemas
# ---------------------------------------------------------------------------


class TestNestedSchemaRefs:
    """Nested model $refs point to #/components/schemas, not dangling #/$defs."""

    def test_nested_payload_ref_points_to_components(self) -> None:
        """Payload $ref for nested model must resolve inside components/schemas.

        Regression: TypeAdapter.json_schema() without ref_template generated
        ``$ref: "#/$defs/Inner"`` but _extract_defs moved definitions to
        ``components.schemas``, leaving the payload ref dangling.
        """
        app = App(name="test", version="0.1.0")

        class Inner(BaseModel):
            x: float

        class Outer(BaseModel):
            inner: Inner
            label: str

        @app.telemetry("data", interval=5, state_model=Outer)
        async def data() -> dict[str, Any]:
            return {}

        doc = app.asyncapi()
        payload = doc["channels"]["dataState"]["messages"]["message"]["payload"]

        # The payload itself should reference Inner via components/schemas
        props = payload.get("properties", {})
        inner_ref = props.get("inner", {})
        assert "$ref" in inner_ref, "inner field should be a $ref"
        assert inner_ref["$ref"] == "#/components/schemas/Inner", (
            f"ref should point to #/components/schemas/Inner, got {inner_ref['$ref']!r}"
        )

        # And Inner must be present in components.schemas
        assert "Inner" in doc["components"]["schemas"], (
            "Inner must be promoted to components.schemas"
        )

    def test_no_dangling_defs_in_payload(self) -> None:
        """Payload schema must not contain a $defs key after promotion."""
        app = App(name="test", version="0.1.0")

        class SubModel(BaseModel):
            value: int

        class TopModel(BaseModel):
            sub: SubModel

        @app.telemetry("metrics", interval=10, state_model=TopModel)
        async def metrics() -> dict[str, Any]:
            return {}

        doc = app.asyncapi()
        payload = doc["channels"]["metricsState"]["messages"]["message"]["payload"]
        assert "$defs" not in payload, (
            "$defs must be extracted from payload and placed in components/schemas"
        )


# ---------------------------------------------------------------------------
# cos-bnq — Regression: Router prefix produces valid channel IDs
# ---------------------------------------------------------------------------


class TestRouterChannelIds:
    """Router prefix names (with slashes) produce JSON-Pointer-safe channel IDs."""

    def test_router_channel_id_has_no_slash(self) -> None:
        """Channel ID must not contain '/' when router prefix adds a slash segment."""
        import cosalette

        router = cosalette.Router(prefix="sensors")

        @router.telemetry("temperature", interval=30)
        async def read_temp() -> dict[str, Any]:
            return {}

        app = App(name="bridge", version="0.1.0")
        app.include_router(router)

        doc = app.asyncapi()
        for ch_id in doc.get("channels", {}):
            assert "/" not in ch_id, (
                f"Channel ID {ch_id!r} contains '/' — invalid JSON Pointer path"
            )

    def test_router_channel_id_is_camel_cased(self) -> None:
        """sensors/temperature → sensorsTemperatureState (camelCase)."""
        import cosalette

        router = cosalette.Router(prefix="sensors")

        @router.telemetry("temperature", interval=30)
        async def read_temp() -> dict[str, Any]:
            return {}

        app = App(name="bridge", version="0.1.0")
        app.include_router(router)

        doc = app.asyncapi()
        assert "sensorsTemperatureState" in doc["channels"], (
            "Expected channel ID 'sensorsTemperatureState' for prefix/name registration"
        )

    def test_router_channel_address_preserves_slashes(self) -> None:
        """MQTT address retains full path: bridge/sensors/temperature/state."""
        import cosalette

        router = cosalette.Router(prefix="sensors")

        @router.telemetry("temperature", interval=30)
        async def read_temp() -> dict[str, Any]:
            return {}

        app = App(name="bridge", version="0.1.0")
        app.include_router(router)

        doc = app.asyncapi()
        ch = doc["channels"]["sensorsTemperatureState"]
        assert ch["address"] == "bridge/sensors/temperature/state"

    def test_router_operation_ref_matches_channel_id(self) -> None:
        """Operation $ref must match the normalised channel ID (no dangling ref)."""
        import cosalette

        router = cosalette.Router(prefix="actuators")

        @router.command("relay")
        async def relay_cmd(payload: str) -> None:
            pass

        app = App(name="home", version="0.1.0")
        app.include_router(router)

        doc = app.asyncapi()
        # Channel ID must be valid (no slash)
        assert "actuatorsRelayCommand" in doc["channels"]
        # Operation $ref must point to the correct channel ID
        op = doc["operations"]["receiveActuatorsRelayCommand"]
        assert op["channel"] == {"$ref": "#/channels/actuatorsRelayCommand"}


# ---------------------------------------------------------------------------
# cos-bnq — Regression: root registration address omits device segment
# ---------------------------------------------------------------------------


class TestRootRegistrationAddress:
    """Root registrations (name=None) emit {app}/state or {app}/set addresses."""

    def test_root_telemetry_address_omits_name(self) -> None:
        """Root telemetry publishes to {app}/state, not {app}/{fn_name}/state."""
        app = App(name="bridge", version="0.1.0")

        @app.telemetry(interval=10)
        async def read_sensor() -> dict[str, Any]:
            return {}

        doc = app.asyncapi()
        # Find the channel — its ID is based on the function name
        channels = doc["channels"]
        assert len(channels) == 1
        ch = next(iter(channels.values()))
        assert ch["address"] == "bridge/state", (
            f"Root telemetry address should be 'bridge/state', got {ch['address']!r}"
        )

    def test_root_command_address_omits_name(self) -> None:
        """Root command subscribes to {app}/set, not {app}/{fn_name}/set."""
        app = App(name="bridge", version="0.1.0")

        @app.command()
        async def handle_cmd(payload: str) -> None:
            pass

        doc = app.asyncapi()
        channels = doc["channels"]
        assert len(channels) == 1
        ch = next(iter(channels.values()))
        assert ch["address"] == "bridge/set", (
            f"Root command address should be 'bridge/set', got {ch['address']!r}"
        )


# ---------------------------------------------------------------------------
# cos-bnq — Regression: command state output channel
# ---------------------------------------------------------------------------


class TestCommandStateOutput:
    """Commands emit a distinct /state send-channel when output type is known."""

    def test_command_with_state_model_emits_state_channel(self) -> None:
        """@command(state_model=X) emits both /set (Command) and /state channels."""
        app = App(name="test", version="0.1.0")

        class InputPayload(BaseModel):
            action: str

        class OutputState(BaseModel):
            result: int

        @app.command("gate", payload_model=InputPayload, state_model=OutputState)
        async def gate(payload: str) -> None:
            pass

        doc = app.asyncapi()
        assert "gateCommand" in doc["channels"], "Input /set channel must exist"
        assert "gateState" in doc["channels"], (
            "State output /state channel must be emitted when state_model is set"
        )

    def test_command_set_channel_uses_payload_model(self) -> None:
        """/set channel payload uses payload_model, not state_model."""
        app = App(name="test", version="0.1.0")

        class InputPayload(BaseModel):
            action: str

        class OutputState(BaseModel):
            result: int

        @app.command("gate", payload_model=InputPayload, state_model=OutputState)
        async def gate(payload: str) -> None:
            pass

        doc = app.asyncapi()
        set_payload = doc["channels"]["gateCommand"]["messages"]["message"]["payload"]
        assert "action" in set_payload.get("properties", {}), (
            "/set payload should use payload_model (action field)"
        )
        assert "result" not in set_payload.get("properties", {}), (
            "/set payload must NOT use state_model"
        )

    def test_command_state_channel_uses_state_model(self) -> None:
        """/state channel payload uses state_model."""
        app = App(name="test", version="0.1.0")

        class InputPayload(BaseModel):
            action: str

        class OutputState(BaseModel):
            result: int

        @app.command("gate", payload_model=InputPayload, state_model=OutputState)
        async def gate(payload: str) -> None:
            pass

        doc = app.asyncapi()
        state_payload = doc["channels"]["gateState"]["messages"]["message"]["payload"]
        assert "result" in state_payload.get("properties", {}), (
            "/state payload should use state_model (result field)"
        )
        assert "action" not in state_payload.get("properties", {}), (
            "/state payload must NOT use payload_model"
        )

    def test_command_state_channel_action_is_send(self) -> None:
        """The state output operation must use 'send' action."""
        app = App(name="test", version="0.1.0")

        class OutputState(BaseModel):
            result: int

        @app.command("gate", state_model=OutputState)
        async def gate(payload: str) -> None:
            pass

        doc = app.asyncapi()
        op = doc["operations"]["publishGateState"]
        assert op["action"] == "send"

    def test_command_state_channel_archetype_is_command(self) -> None:
        """The state channel x-cosalette-archetype must be 'command'."""
        app = App(name="test", version="0.1.0")

        class OutputState(BaseModel):
            result: int

        @app.command("gate", state_model=OutputState)
        async def gate(payload: str) -> None:
            pass

        doc = app.asyncapi()
        assert doc["channels"]["gateState"]["x-cosalette-archetype"] == "command"

    def test_void_command_emits_no_state_channel(self) -> None:
        """Command with -> None return and no state_model must NOT emit state."""
        app = App(name="test", version="0.1.0")

        @app.command("raw")
        async def raw_cmd(payload: str) -> None:
            pass

        doc = app.asyncapi()
        assert "rawState" not in doc.get("channels", {}), (
            "Void command must not emit a state channel"
        )

    def test_command_state_channel_address(self) -> None:
        """State output channel address is {app}/{name}/state."""
        app = App(name="test", version="0.1.0")

        class OutputState(BaseModel):
            result: int

        @app.command("gate", state_model=OutputState)
        async def gate(payload: str) -> None:
            pass

        doc = app.asyncapi()
        assert doc["channels"]["gateState"]["address"] == "test/gate/state"

    def test_command_return_annotation_fallback_for_state(self) -> None:
        """Return annotation (no explicit state_model) triggers state channel."""
        app = App(name="test", version="0.1.0")

        @app.command("measure")
        async def measure_cmd(payload: str) -> _CmdResultState:
            return _CmdResultState(value=0.0)

        doc = app.asyncapi()
        assert "measureState" in doc["channels"], (
            "Return annotation should trigger state channel emission"
        )
        state_payload = doc["channels"]["measureState"]["messages"]["message"][
            "payload"
        ]
        assert "value" in state_payload.get("properties", {})


# ---------------------------------------------------------------------------
# cos-bnq — Regression: same-name telemetry + command state collision
# ---------------------------------------------------------------------------


class TestSameNameTelemetryCommandCollision:
    """Same-name telemetry + command must not overwrite the telemetry state channel.

    Supported registration shape: both a telemetry and a command share the same
    name (e.g. ``"status"``).  The command emits a ``/set`` input channel AND
    a ``/state`` output channel.  The ``/state`` output must not clobber the
    telemetry's ``/state`` channel.
    """

    def test_both_channels_present(self) -> None:
        """statusCommand and statusState both exist when names collide."""
        app = App(name="test", version="0.1.0")

        class TelModel(BaseModel):
            celsius: float

        class CmdModel(BaseModel):
            result: int

        @app.telemetry("status", interval=30, state_model=TelModel)
        async def status_tel() -> dict[str, Any]:
            return {}

        @app.command("status", state_model=CmdModel)
        async def status_cmd(payload: str) -> None:
            pass

        doc = app.asyncapi()
        assert "statusCommand" in doc["channels"], "Command /set channel must exist"
        assert "statusState" in doc["channels"], "State channel must exist"

    def test_different_schemas_produces_oneof(self) -> None:
        """When telemetry and command state schemas differ, channel uses oneOf."""
        app = App(name="test", version="0.1.0")

        class TelModel(BaseModel):
            celsius: float

        class CmdModel(BaseModel):
            result: int

        @app.telemetry("status", interval=30, state_model=TelModel)
        async def status_tel() -> dict[str, Any]:
            return {}

        @app.command("status", state_model=CmdModel)
        async def status_cmd(payload: str) -> None:
            pass

        doc = app.asyncapi()
        payload = doc["channels"]["statusState"]["messages"]["message"]["payload"]
        assert "oneOf" in payload, (
            "Differing schemas must be merged with oneOf, not overwritten"
        )
        all_props: dict[str, Any] = {}
        for schema in payload["oneOf"]:
            all_props.update(schema.get("properties", {}))
        assert "celsius" in all_props, "Telemetry schema (celsius) must appear in oneOf"
        assert "result" in all_props, (
            "Command state schema (result) must appear in oneOf"
        )

    def test_identical_schemas_keeps_single_schema(self) -> None:
        """Identical telemetry and command state schemas: no oneOf emitted."""
        app = App(name="test", version="0.1.0")

        class SharedModel(BaseModel):
            value: float

        @app.telemetry("meter", interval=30, state_model=SharedModel)
        async def meter_tel() -> dict[str, Any]:
            return {}

        @app.command("meter", state_model=SharedModel)
        async def meter_cmd(payload: str) -> None:
            pass

        doc = app.asyncapi()
        payload = doc["channels"]["meterState"]["messages"]["message"]["payload"]
        assert "oneOf" not in payload, "Identical schemas must not produce oneOf"
        assert "value" in payload.get("properties", {}), (
            "Shared schema property must be preserved"
        )

    def test_telemetry_archetype_preserved_on_collision(self) -> None:
        """Existing telemetry archetype is not clobbered by command state."""
        app = App(name="test", version="0.1.0")

        class TelModel(BaseModel):
            celsius: float

        class CmdModel(BaseModel):
            result: int

        @app.telemetry("status", interval=30, state_model=TelModel)
        async def status_tel() -> dict[str, Any]:
            return {}

        @app.command("status", state_model=CmdModel)
        async def status_cmd(payload: str) -> None:
            pass

        doc = app.asyncapi()
        channel = doc["channels"]["statusState"]
        assert channel["x-cosalette-archetype"] == "telemetry", (
            "Telemetry archetype must be preserved; command state must not overwrite it"
        )


# ---------------------------------------------------------------------------
# cos-bnq — Regression: router-prefixed root registrations
# ---------------------------------------------------------------------------


class TestRouterPrefixedRootAddress:
    """Root registrations under a Router prefix include the prefix in the address.

    A ``Router(prefix="sensors")`` root telemetry/command handler occupies
    ``{app}/sensors/state`` or ``{app}/sensors/set``, not the bare
    ``{app}/state`` / ``{app}/set``.

    The ``is_root`` flag is monkey-patched onto the registration (same
    technique as :class:`TestMetadataFields`) because Python nested functions
    have a longer ``__qualname__`` than their ``__name__``, which prevents
    the Router from auto-detecting root status in test scope.  In production
    the handler would be a module-level function whose ``__qualname__ ==
    __name__``, satisfying the Router's root detection heuristic.
    """

    def test_prefixed_root_telemetry_address(self) -> None:
        """Root telemetry under Router(prefix='sensors') emits {app}/sensors/state."""
        from dataclasses import replace as dc_replace

        import cosalette

        router = cosalette.Router(prefix="sensors")

        @router.telemetry("status", interval=30)
        async def status_tel() -> dict[str, Any]:
            return {}

        app = App(name="bridge", version="0.1.0")
        app.include_router(router)
        # Simulate module-level root detection: name="sensors/status", is_root=True
        app._telemetry[0] = dc_replace(app._telemetry[0], is_root=True)

        doc = app.asyncapi()
        ch = doc["channels"]["sensorsStatusState"]
        assert ch["address"] == "bridge/sensors/state", (
            f"Expected 'bridge/sensors/state', got {ch['address']!r}"
        )

    def test_prefixed_root_command_address(self) -> None:
        """Root command under Router(prefix='sensors') → {app}/sensors/set."""
        from dataclasses import replace as dc_replace

        import cosalette

        router = cosalette.Router(prefix="sensors")

        @router.command("ctrl")
        async def ctrl_cmd(payload: str) -> None:
            pass

        app = App(name="bridge", version="0.1.0")
        app.include_router(router)
        # Simulate module-level root detection: name="sensors/ctrl", is_root=True
        app._commands[0] = dc_replace(app._commands[0], is_root=True)

        doc = app.asyncapi()
        ch = doc["channels"]["sensorsCtrlCommand"]
        assert ch["address"] == "bridge/sensors/set", (
            f"Expected 'bridge/sensors/set', got {ch['address']!r}"
        )

    def test_bare_root_address_unchanged(self) -> None:
        """Root registration without any prefix still emits {app}/state."""
        app = App(name="bridge", version="0.1.0")

        @app.telemetry(interval=10)
        async def read_sensor() -> dict[str, Any]:
            return {}

        doc = app.asyncapi()
        channels = doc["channels"]
        assert len(channels) == 1
        ch = next(iter(channels.values()))
        assert ch["address"] == "bridge/state", (
            f"Expected 'bridge/state', got {ch['address']!r}"
        )
