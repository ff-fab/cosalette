"""Unit tests for cosalette typed handler contract layer (ADR-046).

Test Techniques Used:
    - Specification-based Testing: Verify Depends/Payload/Topic/Message API contracts,
      injection plan rules, and validation behavior.
    - Equivalence Partitioning: str passthrough vs. typed JSON parsing; None return vs.
      dict vs. primitive for normalize_return.
    - Error Guessing: Invalid JSON payload, async Depends, missing request context,
      non-optional type with None payload.
    - Integration Testing: End-to-end command dispatch with typed binding via App +
      MockMqttClient.
    - State Transition Testing: Triggered vs. scheduled runs for Annotated[T, Payload()]
      in triggerable telemetry.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import AsyncIterator
from typing import Annotated, Any

import pytest
from pydantic import BaseModel

import cosalette
from cosalette._contracts import (
    PayloadValidationError,
    ReturnValidationError,
    normalize_return,
    parse_payload,
)
from cosalette._injection import (
    build_injection_plan,
    detect_raw_mqtt_params,
    resolve_request_kwargs,
)
from cosalette._json import loads as json_loads
from cosalette._registration import _TelemetryRegistration
from cosalette._runners._telemetry_runner import (
    TelemetryRunner,
    _normalize_telemetry_return,
)
from cosalette._runners._telemetry_types import _TriggerSlot
from cosalette._runners._trigger import TriggerPayload
from cosalette.di import Depends, _DependsMarker
from cosalette.mqtt import Message, Payload, Topic, _PayloadMarker, _TopicMarker
from cosalette.testing import AppHarness, FakeClock, MockMqttClient, make_settings

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Module-level dep callables — must be module-global so get_type_hints()
# can resolve them in PEP 563 (from __future__ import annotations) context.
# ---------------------------------------------------------------------------


def _get_const_val() -> int:
    """Synchronous dep returning a constant."""
    return 42


def _get_device_from_topic(t: Annotated[str, Topic()]) -> str:
    """Dep that reads the Topic() to extract a device segment."""
    return t.split("/")[1]


def _get_prefix() -> str:
    """Dep with no parameters — returns constant for DI provider test."""
    return "prefix"


async def _async_dep() -> str:
    """Async dep — should be rejected by Depends()."""
    return "bad"  # pragma: no cover


async def _async_gen_dep() -> AsyncIterator[str]:
    """Async generator dep — should be rejected by Depends()."""
    yield "bad"  # pragma: no cover


class _SetpointCmd(BaseModel):
    """Pydantic model for typed payload tests."""

    value: float
    unit: str = "celsius"


class _ThermoState(BaseModel):
    """Pydantic model for typed return tests."""

    setpoint: float
    unit: str


@dataclasses.dataclass
class _ReadingDC:
    """Stdlib dataclass for typed payload tests."""

    celsius: float


# ---------------------------------------------------------------------------
# 1. Public API exports
# ---------------------------------------------------------------------------


class TestPublicAPIExports:
    """New typed contract symbols are importable from cosalette and sub-modules.

    Technique: Specification-based — verifying module import contracts.
    """

    def test_depends_importable_from_cosalette(self) -> None:
        """Depends is accessible as cosalette.Depends."""
        assert cosalette.Depends is Depends

    def test_payload_importable_from_cosalette(self) -> None:
        """Payload is accessible as cosalette.Payload."""
        assert cosalette.Payload is Payload

    def test_topic_importable_from_cosalette(self) -> None:
        """Topic is accessible as cosalette.Topic."""
        assert cosalette.Topic is Topic

    def test_message_importable_from_cosalette(self) -> None:
        """Message is accessible as cosalette.Message."""
        assert cosalette.Message is Message

    def test_payload_validation_error_in_cosalette(self) -> None:
        """PayloadValidationError is accessible at top-level."""
        assert cosalette.PayloadValidationError is PayloadValidationError

    def test_return_validation_error_in_cosalette(self) -> None:
        """ReturnValidationError is accessible at top-level."""
        assert cosalette.ReturnValidationError is ReturnValidationError

    def test_depends_importable_from_di_module(self) -> None:
        """from cosalette.di import Depends works."""
        from cosalette.di import Depends as D

        assert D is Depends

    def test_payload_topic_message_importable_from_mqtt_module(self) -> None:
        """from cosalette.mqtt import Payload, Topic, Message works."""
        from cosalette.mqtt import Message as M
        from cosalette.mqtt import Payload as P
        from cosalette.mqtt import Topic as T

        assert P is Payload
        assert T is Topic
        assert M is Message


# ---------------------------------------------------------------------------
# 2. Depends marker
# ---------------------------------------------------------------------------


class TestDependsMarker:
    """Depends() factory and _DependsMarker behaviour.

    Technique: Specification-based + Error Guessing.
    """

    def test_depends_returns_marker_instance(self) -> None:
        """Depends(fn) returns a _DependsMarker wrapping fn."""
        fn = lambda: 42  # noqa: E731

        marker = Depends(fn)

        assert isinstance(marker, _DependsMarker)
        assert marker.dependency is fn

    def test_depends_repr(self) -> None:
        """_DependsMarker has human-readable repr."""
        fn = lambda: 42  # noqa: E731
        marker = Depends(fn)

        assert "Depends" in repr(marker)

    def test_async_depends_raises_typeerror(self) -> None:
        """Async callables are rejected with TypeError.

        Technique: Error Guessing — anticipating unsupported async dep.
        """
        with pytest.raises(TypeError, match="Async"):
            Depends(_async_dep)

    def test_async_gen_depends_raises_typeerror(self) -> None:
        """Async generator callables are rejected with TypeError."""
        with pytest.raises(TypeError, match="Async"):
            Depends(_async_gen_dep)


# ---------------------------------------------------------------------------
# 3. Payload / Topic / Message markers
# ---------------------------------------------------------------------------


class TestMqttMarkers:
    """Payload(), Topic(), and Message value type behaviour.

    Technique: Specification-based.
    """

    def test_payload_returns_marker_instance(self) -> None:
        assert isinstance(Payload(), _PayloadMarker)

    def test_payload_raw_flag_default_false(self) -> None:
        assert Payload().raw is False

    def test_payload_raw_flag_true(self) -> None:
        assert Payload(raw=True).raw is True

    def test_topic_returns_marker_instance(self) -> None:
        assert isinstance(Topic(), _TopicMarker)

    def test_message_is_frozen_dataclass(self) -> None:
        msg = Message(topic="a/b", payload='{"x":1}')
        assert msg.topic == "a/b"
        assert msg.payload == '{"x":1}'

    def test_message_frozen_rejects_mutation(self) -> None:
        from dataclasses import FrozenInstanceError

        msg = Message(topic="t", payload="p")
        with pytest.raises(FrozenInstanceError):
            msg.topic = "other"  # ty: ignore[invalid-assignment]


# ---------------------------------------------------------------------------
# 4. build_injection_plan with Annotated markers
# ---------------------------------------------------------------------------


class TestBuildInjectionPlanAnnotated:
    """build_injection_plan handles Annotated markers correctly.

    Technique: Specification-based + Boundary Value Analysis.
    """

    def test_plain_str_payload_excluded_from_plan(self) -> None:
        """payload: str is excluded (raw MQTT) and NOT in injection plan."""

        async def handler(payload: str) -> None: ...

        plan = build_injection_plan(handler, mqtt_params={"payload"})
        names = [name for name, _ in plan]
        assert "payload" not in names

    def test_generic_framework_param_excluded_unconditionally(self) -> None:
        """events: list[str] is skipped when 'events' is in mqtt_params.

        Technique: Boundary — framework-reserved param with non-str annotation.
        Regression guard: reactor's events parameter must be skipped even
        though its annotation is list[str], not str.
        """

        async def handler(events: list[str]) -> None: ...

        plan = build_injection_plan(handler, mqtt_params={"events"})
        names = [name for name, _ in plan]
        assert "events" not in names

    def test_typed_payload_included_in_plan(self) -> None:
        """payload: SomeModel is NOT excluded — typed binding path.

        Technique: Boundary — payload name with non-str annotation.
        """

        async def handler(payload: _SetpointCmd) -> None: ...

        # detect_raw_mqtt_params sees non-str annotation, excludes from raw set
        raw = detect_raw_mqtt_params(handler)
        plan = build_injection_plan(handler, mqtt_params=raw)
        names = [name for name, _ in plan]
        assert "payload" in names

    def test_annotated_payload_marker_in_plan(self) -> None:
        """Annotated[Model, Payload()] param is preserved in the injection plan."""

        async def handler(cmd: Annotated[_SetpointCmd, Payload()]) -> None: ...

        plan = build_injection_plan(handler)
        assert any(name == "cmd" for name, _ in plan)

    def test_annotated_topic_marker_in_plan(self) -> None:
        """Annotated[str, Topic()] param is preserved in the injection plan."""

        async def handler(full_topic: Annotated[str, Topic()]) -> None: ...

        plan = build_injection_plan(handler)
        assert any(name == "full_topic" for name, _ in plan)

    def test_annotated_depends_in_plan(self) -> None:
        """Annotated[T, Depends(fn)] param is preserved in injection plan."""

        async def handler(
            device_id: Annotated[str, Depends(_get_const_val)],
        ) -> None: ...

        plan = build_injection_plan(handler)
        assert any(name == "device_id" for name, _ in plan)

    def test_message_type_in_plan(self) -> None:
        """message: Message param appears in plan (Message is a concrete type)."""

        async def handler(message: Message) -> None: ...

        plan = build_injection_plan(handler)
        assert any(name == "message" for name, _ in plan)


# ---------------------------------------------------------------------------
# 5. detect_raw_mqtt_params
# ---------------------------------------------------------------------------


class TestDetectRawMqttParams:
    """detect_raw_mqtt_params correctly partitions str vs. typed params.

    Technique: Equivalence Partitioning — str annotation vs. other types.
    """

    def test_plain_str_topic_is_raw(self) -> None:
        async def h(topic: str) -> None: ...

        assert "topic" in detect_raw_mqtt_params(h)

    def test_plain_str_payload_is_raw(self) -> None:
        async def h(payload: str) -> None: ...

        assert "payload" in detect_raw_mqtt_params(h)

    def test_typed_payload_not_raw(self) -> None:
        async def h(payload: _SetpointCmd) -> None: ...

        assert "payload" not in detect_raw_mqtt_params(h)

    def test_annotated_topic_not_raw(self) -> None:
        async def h(topic: Annotated[str, Topic()]) -> None: ...

        assert "topic" not in detect_raw_mqtt_params(h)

    def test_absent_params_not_in_result(self) -> None:
        async def h() -> None: ...

        assert detect_raw_mqtt_params(h) == frozenset()


# ---------------------------------------------------------------------------
# 6. resolve_request_kwargs — Depends resolution
# ---------------------------------------------------------------------------


class TestResolveRequestKwargsDepends:
    """resolve_request_kwargs resolves Depends() callables correctly.

    Technique: Specification-based + Integration (nested deps).
    """

    def test_simple_depends_resolved(self) -> None:
        """Depends(fn) is called and its return value injected."""

        async def handler(n: Annotated[int, Depends(_get_const_val)]) -> None: ...

        plan = build_injection_plan(handler)
        result = resolve_request_kwargs(plan, providers={})
        assert result["n"] == 42

    def test_nested_depends_with_topic(self) -> None:
        """Depends callable may itself declare Annotated[str, Topic()] params.

        Technique: Integration — nested dep that reads Topic().
        """

        async def handler(
            device_id: Annotated[str, Depends(_get_device_from_topic)],
        ) -> None: ...

        plan = build_injection_plan(handler)
        result = resolve_request_kwargs(
            plan, providers={}, topic="myapp/sensor/set", payload="{}"
        )
        assert result["device_id"] == "sensor"

    def test_depends_with_di_providers(self) -> None:
        """Depends callable may work with empty providers when it has no params.

        Technique: Specification-based — Depends with zero-arg callable.
        """

        async def handler(pfx: Annotated[str, Depends(_get_prefix)]) -> None: ...

        plan = build_injection_plan(handler)
        result = resolve_request_kwargs(plan, providers={})
        assert result["pfx"] == "prefix"


# ---------------------------------------------------------------------------
# 7. resolve_request_kwargs — Payload / Topic / Message binding
# ---------------------------------------------------------------------------


class TestResolveRequestKwargsBinding:
    """resolve_request_kwargs handles Payload/Topic/Message markers.

    Technique: Specification-based + Equivalence Partitioning.
    """

    def test_annotated_payload_binds_pydantic_model(self) -> None:
        """Annotated[PydanticModel, Payload()] parses JSON payload."""

        async def handler(cmd: Annotated[_SetpointCmd, Payload()]) -> None: ...

        plan = build_injection_plan(handler)
        result = resolve_request_kwargs(plan, providers={}, payload='{"value": 21.5}')
        assert isinstance(result["cmd"], _SetpointCmd)
        assert result["cmd"].value == 21.5

    def test_annotated_payload_raw_returns_string(self) -> None:
        """Annotated[str, Payload(raw=True)] returns raw payload without parsing."""

        async def handler(raw: Annotated[str, Payload(raw=True)]) -> None: ...

        plan = build_injection_plan(handler)
        result = resolve_request_kwargs(plan, providers={}, payload="OPEN")
        assert result["raw"] == "OPEN"

    def test_annotated_topic_binds_topic_string(self) -> None:
        """Annotated[str, Topic()] injects the full topic string."""

        async def handler(t: Annotated[str, Topic()]) -> None: ...

        plan = build_injection_plan(handler)
        result = resolve_request_kwargs(plan, providers={}, topic="myapp/device/set")
        assert result["t"] == "myapp/device/set"

    def test_message_type_binds_message_object(self) -> None:
        """message: Message injects a Message(topic, payload) instance."""

        async def handler(message: Message) -> None: ...

        plan = build_injection_plan(handler)
        result = resolve_request_kwargs(
            plan, providers={}, topic="app/dev/set", payload='{"x":1}'
        )
        msg = result["message"]
        assert isinstance(msg, Message)
        assert msg.topic == "app/dev/set"
        assert msg.payload == '{"x":1}'

    def test_topic_without_context_raises(self) -> None:
        """Topic() marker without topic raises TypeError.

        Technique: Error Guessing — missing request context.
        """

        async def handler(t: Annotated[str, Topic()]) -> None: ...

        plan = build_injection_plan(handler)
        with pytest.raises(TypeError, match="Topic\\(\\)"):
            resolve_request_kwargs(plan, providers={})

    def test_message_without_context_raises(self) -> None:
        """Message type without request context raises TypeError."""

        async def handler(message: Message) -> None: ...

        plan = build_injection_plan(handler)
        with pytest.raises(TypeError, match="Message"):
            resolve_request_kwargs(plan, providers={})


# ---------------------------------------------------------------------------
# 8. parse_payload — validation backend
# ---------------------------------------------------------------------------


class TestParsePayload:
    """parse_payload correctly parses and validates typed payloads.

    Technique: Equivalence Partitioning + Error Guessing + Boundary Value Analysis.
    """

    def test_str_annotation_returns_raw(self) -> None:
        """str annotation passes through raw payload unchanged."""
        assert parse_payload('{"x":1}', str) == '{"x":1}'

    def test_str_annotation_none_returns_empty(self) -> None:
        """str annotation with None payload returns empty string."""
        assert parse_payload(None, str) == ""

    def test_pydantic_model_valid_json(self) -> None:
        """Valid JSON for Pydantic BaseModel returns model instance."""
        result = parse_payload('{"value": 42.0}', _SetpointCmd)
        assert isinstance(result, _SetpointCmd)
        assert result.value == 42.0

    def test_dataclass_valid_json(self) -> None:
        """Valid JSON for stdlib dataclass returns dataclass instance."""
        result = parse_payload('{"celsius": 21.5}', _ReadingDC)
        assert isinstance(result, _ReadingDC)
        assert result.celsius == 21.5

    def test_invalid_json_raises_payload_validation_error(self) -> None:
        """Invalid JSON raises PayloadValidationError with context.

        Technique: Error Guessing — malformed JSON payload.
        """
        with pytest.raises(PayloadValidationError, match="not valid JSON"):
            parse_payload("{bad json}", _SetpointCmd, param="cmd", handler="h")

    def test_schema_mismatch_raises_payload_validation_error(self) -> None:
        """JSON that doesn't match schema raises PayloadValidationError."""
        with pytest.raises(PayloadValidationError, match="Payload validation failed"):
            parse_payload('{"wrong_field": true}', _SetpointCmd)

    def test_optional_type_accepts_none_payload(self) -> None:
        """T | None annotation returns None for empty/None payload."""
        result = parse_payload(None, _SetpointCmd | None)
        assert result is None

    def test_primitive_int_parsed(self) -> None:
        """Plain int annotation validates a JSON integer."""
        result = parse_payload("42", int)
        assert result == 42

    def test_payload_error_includes_param_name(self) -> None:
        """PayloadValidationError contains param name in message."""
        try:
            parse_payload("not json", _SetpointCmd, param="myCmd")
        except PayloadValidationError as exc:
            assert "myCmd" in str(exc)


# ---------------------------------------------------------------------------
# 9. normalize_return — return serialisation
# ---------------------------------------------------------------------------


class TestNormalizeReturn:
    """normalize_return converts typed handler returns to publish-ready dicts.

    Technique: Equivalence Partitioning — None, dict, BaseModel, primitive, list.
    """

    def test_none_returns_none(self) -> None:
        """None suppresses publish."""
        assert normalize_return(None, None) is None

    def test_dict_returned_as_is(self) -> None:
        """Plain dict is returned unchanged."""
        d = {"x": 1}
        assert normalize_return(d, None) is d

    def test_pydantic_model_serialised_to_dict(self) -> None:
        """BaseModel is serialised via TypeAdapter.dump_python to a dict."""
        model = _ThermoState(setpoint=21.5, unit="celsius")
        result = normalize_return(model, _ThermoState)
        assert result == {"setpoint": 21.5, "unit": "celsius"}

    def test_dataclass_serialised_to_dict(self) -> None:
        """Stdlib dataclass is serialised to dict via TypeAdapter."""
        dc = _ReadingDC(celsius=20.0)
        result = normalize_return(dc, _ReadingDC)
        assert result == {"celsius": 20.0}

    def test_int_wrapped_as_value_key(self) -> None:
        """int primitive is wrapped as {'value': <int>}."""
        assert normalize_return(42, int) == {"value": 42}

    def test_float_wrapped_as_value_key(self) -> None:
        """float primitive is wrapped as {'value': <float>}."""
        result = normalize_return(3.14, float)
        assert result == {"value": 3.14}

    def test_list_wrapped_as_value_key(self) -> None:
        """list is wrapped as {'value': [...]}."""
        result = normalize_return([1, 2, 3], list)
        assert result == {"value": [1, 2, 3]}

    def test_no_annotation_dict_returned_as_is(self) -> None:
        """Dict with no annotation returns unchanged."""
        d = {"celsius": 21.5}
        assert normalize_return(d, None) == {"celsius": 21.5}

    def test_no_annotation_primitive_wrapped(self) -> None:
        """Primitive with no annotation is still wrapped."""
        assert normalize_return(99, None) == {"value": 99}


# ---------------------------------------------------------------------------
# 10. Integration — typed command payload binding via App
# ---------------------------------------------------------------------------


class TestTypedCommandBinding:
    """End-to-end typed command payload binding through App dispatch.

    Technique: Integration Testing — full dispatch path with MockMqttClient.
    """

    async def test_typed_payload_model_received(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """payload: PydanticModel receives parsed model instance on command.

        Technique: Specification-based — typed payload binding.
        """
        app = cosalette.App("testapp")
        received: list[Any] = []

        @app.command("thermostat")
        async def handle(payload: _SetpointCmd) -> None:
            received.append(payload)

        shutdown = asyncio.Event()

        async def run() -> None:
            await asyncio.sleep(0.05)
            await mock_mqtt.deliver("testapp/thermostat/set", '{"value": 22.0}')
            await asyncio.sleep(0.05)
            shutdown.set()

        asyncio.create_task(run())
        await asyncio.wait_for(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        assert len(received) == 1
        assert isinstance(received[0], _SetpointCmd)
        assert received[0].value == 22.0

    async def test_annotated_payload_marker_binding(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Annotated[Model, Payload()] works on non-payload-named param."""
        app = cosalette.App("testapp")
        received: list[Any] = []

        @app.command("valve")
        async def handle(cmd: Annotated[_SetpointCmd, Payload()]) -> None:
            received.append(cmd)

        shutdown = asyncio.Event()

        async def run() -> None:
            await asyncio.sleep(0.05)
            await mock_mqtt.deliver("testapp/valve/set", '{"value": 19.0}')
            await asyncio.sleep(0.05)
            shutdown.set()

        asyncio.create_task(run())
        await asyncio.wait_for(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        assert received[0].value == 19.0

    async def test_annotated_topic_binding(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Annotated[str, Topic()] receives the full MQTT topic string."""
        app = cosalette.App("testapp")
        topics_seen: list[str] = []

        @app.command("sensor")
        async def handle(t: Annotated[str, Topic()]) -> None:
            topics_seen.append(t)

        shutdown = asyncio.Event()

        async def run() -> None:
            await asyncio.sleep(0.05)
            await mock_mqtt.deliver("testapp/sensor/set", "{}")
            await asyncio.sleep(0.05)
            shutdown.set()

        asyncio.create_task(run())
        await asyncio.wait_for(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        assert topics_seen == ["testapp/sensor/set"]

    async def test_message_type_binding(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """message: Message receives Message(topic, payload) instance."""
        app = cosalette.App("testapp")
        messages_seen: list[Message] = []

        @app.command("pump")
        async def handle(message: Message) -> None:
            messages_seen.append(message)

        shutdown = asyncio.Event()

        async def run() -> None:
            await asyncio.sleep(0.05)
            await mock_mqtt.deliver("testapp/pump/set", '{"speed": 5}')
            await asyncio.sleep(0.05)
            shutdown.set()

        asyncio.create_task(run())
        await asyncio.wait_for(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        assert len(messages_seen) == 1
        assert messages_seen[0].topic == "testapp/pump/set"
        assert messages_seen[0].payload == '{"speed": 5}'

    async def test_invalid_payload_publishes_error(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Invalid JSON payload for typed binding is caught and published as error.

        Technique: Error Guessing — malformed payload goes through error publisher.
        """
        app = cosalette.App("testapp")

        @app.command("ctrl")
        async def handle(cmd: Annotated[_SetpointCmd, Payload()]) -> None:
            pass  # should not be reached

        shutdown = asyncio.Event()

        async def run() -> None:
            await asyncio.sleep(0.05)
            await mock_mqtt.deliver("testapp/ctrl/set", "not-json!")
            await asyncio.sleep(0.05)
            shutdown.set()

        asyncio.create_task(run())
        await asyncio.wait_for(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        # Error should be published to the error topic
        error_msgs = [
            msg for topic, msg, _retain, _qos in mock_mqtt.published if "error" in topic
        ]
        assert len(error_msgs) >= 1

    async def test_typed_return_serialised(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Command returning a BaseModel publishes it as a dict via TypeAdapter.

        Technique: Integration — typed return normalisation path.
        """
        app = cosalette.App("testapp")

        @app.command("heater")
        async def handle() -> _ThermoState:
            return _ThermoState(setpoint=20.0, unit="celsius")

        shutdown = asyncio.Event()

        async def run() -> None:
            await asyncio.sleep(0.05)
            await mock_mqtt.deliver("testapp/heater/set", "{}")
            await asyncio.sleep(0.05)
            shutdown.set()

        asyncio.create_task(run())
        await asyncio.wait_for(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        state_msgs = [
            msg
            for topic, msg, _retain, _qos in mock_mqtt.published
            if topic.endswith("/state")
        ]
        assert len(state_msgs) == 1
        payload = json_loads(state_msgs[0])
        assert payload == {"setpoint": 20.0, "unit": "celsius"}


# ---------------------------------------------------------------------------
# 11. Integration — typed triggerable telemetry
# ---------------------------------------------------------------------------


class TestTypedTriggerablePayload:
    """Annotated[T | None, Payload()] in triggerable telemetry handlers.

    Technique: State Transition Testing — triggered vs. scheduled runs.
    """

    def test_find_trigger_kwarg_detects_annotated_payload(self) -> None:
        """_find_trigger_kwarg detects Annotated[T, Payload()] params.

        Technique: Specification-based — internal trigger detection.
        """

        async def handler(cmd: Annotated[_SetpointCmd | None, Payload()]) -> None: ...

        plan = build_injection_plan(handler)
        info = TelemetryRunner._find_trigger_kwarg(plan)
        assert info is not None
        name, annotation = info
        assert name == "cmd"

    def test_find_trigger_kwarg_detects_trigger_payload(self) -> None:
        """_find_trigger_kwarg still detects legacy TriggerPayload params."""

        async def handler(trigger: TriggerPayload) -> None: ...

        plan = build_injection_plan(handler)
        info = TelemetryRunner._find_trigger_kwarg(plan)
        assert info is not None
        name, annotation = info
        assert name == "trigger"
        assert annotation is TriggerPayload

    def test_update_trigger_kwargs_typed_on_trigger(self) -> None:
        """_update_trigger_kwargs parses trigger payload into typed model.

        Technique: State Transition — triggered run path.
        """
        slot = _TriggerSlot(event=asyncio.Event())
        slot.arm('{"value": 25.0}')

        async def handler(cmd: Annotated[_SetpointCmd | None, Payload()]) -> None: ...

        plan = build_injection_plan(handler)
        info = TelemetryRunner._find_trigger_kwarg(plan)
        assert info is not None
        kwargs: dict[str, Any] = {}
        TelemetryRunner._update_trigger_kwargs(slot, info, kwargs)

        assert isinstance(kwargs["cmd"], _SetpointCmd)
        assert kwargs["cmd"].value == 25.0

    def test_update_trigger_kwargs_typed_on_scheduled(self) -> None:
        """_update_trigger_kwargs binds None for optional type on scheduled run.

        Technique: State Transition — scheduled (no-trigger) run path.
        """
        slot = _TriggerSlot(event=asyncio.Event())
        # event NOT set → scheduled run

        async def handler(cmd: Annotated[_SetpointCmd | None, Payload()]) -> None: ...

        plan = build_injection_plan(handler)
        info = TelemetryRunner._find_trigger_kwarg(plan)
        assert info is not None
        kwargs: dict[str, Any] = {}
        TelemetryRunner._update_trigger_kwargs(slot, info, kwargs)

        assert kwargs["cmd"] is None

    def test_update_trigger_kwargs_legacy_trigger_payload(self) -> None:
        """_update_trigger_kwargs with TriggerPayload type works as before."""
        slot = _TriggerSlot(event=asyncio.Event())
        slot.arm('{"key": "val"}')

        async def handler(trigger: TriggerPayload) -> None: ...

        plan = build_injection_plan(handler)
        info = TelemetryRunner._find_trigger_kwarg(plan)
        assert info is not None
        kwargs: dict[str, Any] = {}
        TelemetryRunner._update_trigger_kwargs(slot, info, kwargs)

        assert isinstance(kwargs["trigger"], TriggerPayload)
        assert kwargs["trigger"].is_triggered is True


# ---------------------------------------------------------------------------
# 12. Telemetry return normalisation helper
# ---------------------------------------------------------------------------


class TestNormalizeTelemetryReturn:
    """_normalize_telemetry_return uses return annotation > state_model.

    Technique: Specification-based — priority ordering of annotation sources.
    """

    def _make_reg(
        self,
        func: Any,
        state_model: type | None = None,
    ) -> _TelemetryRegistration:
        from cosalette._injection import build_injection_plan

        return _TelemetryRegistration(
            name="test",
            func=func,
            injection_plan=build_injection_plan(func),
            interval=60.0,
            state_model=state_model,
        )

    def test_uses_return_annotation(self) -> None:
        """Return annotation drives serialisation."""

        async def handler() -> _ThermoState:  # pragma: no cover
            raise AssertionError("unreachable")

        reg = self._make_reg(handler)
        model = _ThermoState(setpoint=21.0, unit="celsius")
        result = _normalize_telemetry_return(reg, model)
        assert result == {"setpoint": 21.0, "unit": "celsius"}

    def test_state_model_fallback(self) -> None:
        """state_model is used when return annotation is absent."""

        async def handler():  # no annotation
            ...  # pragma: no cover

        reg = self._make_reg(handler, state_model=_ThermoState)
        model = _ThermoState(setpoint=19.0, unit="celsius")
        result = _normalize_telemetry_return(reg, model)
        assert result == {"setpoint": 19.0, "unit": "celsius"}

    def test_plain_dict_returned_unchanged(self) -> None:
        """Existing dict-returning handlers continue to work unchanged."""

        async def handler() -> dict[str, float]:  # pragma: no cover
            raise AssertionError("unreachable")

        reg = self._make_reg(handler)
        result = _normalize_telemetry_return(reg, {"celsius": 21.5})
        assert result == {"celsius": 21.5}

    def test_none_result_suppresses_publish(self) -> None:
        """None handler result → normalize_return → None."""

        async def handler() -> _ThermoState | None:  # pragma: no cover
            raise AssertionError("unreachable")

        reg = self._make_reg(handler)
        assert _normalize_telemetry_return(reg, None) is None


# ---------------------------------------------------------------------------
# 13. Telemetry error pipeline — validation errors are routed, not re-raised
# ---------------------------------------------------------------------------


class TestTypedValidationErrorRouting:
    """Typed trigger/return validation errors route through the error pipeline.

    Technique: Integration Testing — real App._run_async lifecycle.
    These cover the three NEEDS_REVISION findings:
    - Invalid typed trigger payload before _attempt_with_retry.
    - Invalid typed return after _attempt_with_retry reports success.
    - Group telemetry invalid typed return.
    """

    async def test_invalid_typed_trigger_payload_publishes_error(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """PayloadValidationError from bad trigger payload is published as error.

        The runner must not crash — the loop continues until shutdown.

        Technique: Error Guessing — malformed trigger payload for typed binding.
        """
        app = cosalette.App("testapp")

        @app.telemetry("sensor", interval=3600, triggerable=True)
        async def sensor(
            cmd: Annotated[_SetpointCmd | None, Payload()],
        ) -> dict[str, object]:
            return {"v": 1}

        shutdown = asyncio.Event()

        async def run() -> None:
            await asyncio.sleep(0.02)
            # Deliver bad trigger payload → PayloadValidationError in
            # _update_trigger_kwargs
            await mock_mqtt.deliver("testapp/sensor/set", "not-json!")
            await asyncio.sleep(0.1)
            shutdown.set()

        asyncio.create_task(run())
        await asyncio.wait_for(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        error_msgs = mock_mqtt.get_messages_for("testapp/error")
        assert len(error_msgs) == 1

    async def test_invalid_typed_return_publishes_error(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """ReturnValidationError from bad typed return is published as error.

        The runner must not crash — the loop continues until shutdown.

        Technique: Error Guessing — handler returns object that fails TypeAdapter.
        """
        import unittest.mock

        app = cosalette.App("testapp")
        call_count = 0
        enough = asyncio.Event()

        @app.telemetry("sensor", interval=0.01)
        async def sensor() -> _ThermoState:
            nonlocal call_count
            call_count += 1
            # Return a plain dict missing required fields — will fail serialisation
            # on pydantic strict validation; we patch normalize_return to raise.
            enough.set()
            return {"bad": True}  # ty: ignore[invalid-return-type]

        shutdown = asyncio.Event()

        async def run() -> None:
            await enough.wait()
            await asyncio.sleep(0.05)
            shutdown.set()

        with unittest.mock.patch(
            "cosalette._runners._telemetry_runner._normalize_telemetry_return",
            side_effect=cosalette.ReturnValidationError("bad return"),
        ):
            asyncio.create_task(run())
            await asyncio.wait_for(
                app._run_async(
                    settings=make_settings(),
                    shutdown_event=shutdown,
                    mqtt=mock_mqtt,
                    clock=fake_clock,
                ),
                timeout=5.0,
            )

        error_msgs = mock_mqtt.get_messages_for("testapp/error")
        assert len(error_msgs) >= 1

    async def test_group_invalid_typed_return_publishes_error(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """ReturnValidationError in a group handler routes through error pipeline.

        The group scheduler must continue running other handlers.

        Technique: Error Guessing + Integration — group path.
        """
        app = cosalette.App("testapp")
        a_called = asyncio.Event()
        b_called = asyncio.Event()

        @app.telemetry("sensor_a", interval=0.01, group="g")
        async def sensor_a() -> _ThermoState:
            a_called.set()
            # Return an int — pydantic cannot coerce to _ThermoState
            # → ReturnValidationError
            return 42  # ty: ignore[invalid-return-type]

        @app.telemetry("sensor_b", interval=0.01, group="g")
        async def sensor_b() -> dict[str, object]:
            b_called.set()
            return {"ok": True}

        shutdown = asyncio.Event()

        async def run() -> None:
            await asyncio.gather(a_called.wait(), b_called.wait())
            await asyncio.sleep(0.05)
            shutdown.set()

        asyncio.create_task(run())
        await asyncio.wait_for(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        # sensor_b must have published (group scheduler continued)
        b_msgs = mock_mqtt.get_messages_for("testapp/sensor_b/state")
        assert len(b_msgs) >= 1
        # Error should have been published for sensor_a
        error_msgs = mock_mqtt.get_messages_for("testapp/error")
        assert len(error_msgs) >= 1


# ---------------------------------------------------------------------------
# 14. detect_raw_mqtt_params — string annotation fallback
# ---------------------------------------------------------------------------


class TestDetectRawMqttParamsFallback:
    """detect_raw_mqtt_params handles PEP 563 string annotations on hints failure.

    Technique: Regression Testing — when get_type_hints() fails because
    another annotation is unresolvable, payload: str must still be raw.
    """

    def test_get_type_hints_failure_str_payload_is_raw(self) -> None:
        """payload: str is detected as raw even when get_type_hints() fails.

        Regression: postponed annotations store 'str' as a string literal.
        When another annotation is unresolvable, hints={} and the fallback
        annotation from the signature may be the string 'str', not str.
        """

        async def handler(payload: str, other: int) -> None: ...

        # Inject an unresolvable annotation for 'other' so get_type_hints() fails,
        # and simulate PEP 563 string annotation for 'payload'.
        handler.__annotations__ = {
            "payload": "str",
            "other": "_UnresolvableClassThatDoesNotExist",
        }

        raw = detect_raw_mqtt_params(handler)
        assert "payload" in raw


# ---------------------------------------------------------------------------
# 15. Regression — typed return normalization failure side-effects
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class _ReactorState:
    """State object used to track reactor invocations in regression tests."""

    _pending_events: list[str] = dataclasses.field(default_factory=list)

    def drain_events(self) -> list[str]:
        events = self._pending_events.copy()
        self._pending_events.clear()
        return events

    def mark(self, tag: str) -> None:
        self._pending_events.append(tag)


@pytest.mark.asyncio
class TestTypedReturnNormalizationSideEffects:
    """Return-normalization failures must not trigger post-success side-effects.

    Regression tests for the finding: when _handle_telemetry_outcome encounters
    a normalization error it routes through _handle_telemetry_error and returns
    early, but the original callers still dispatched reactors and recorded
    circuit-breaker success.  The fix adds an ``ok`` flag to the return tuple
    so callers can gate those operations correctly.

    Technique: State Transition Testing + Error Guessing.
    """

    async def test_single_invalid_typed_return_does_not_dispatch_reactor(
        self,
    ) -> None:
        """Single telemetry: reactor must not run when return normalization fails.

        Technique: Error Guessing — handler returns a type that fails TypeAdapter.
        """
        import unittest.mock

        reactor_calls: list[str] = []
        harness = AppHarness.create()

        @harness.app.state
        def _state() -> _ReactorState:
            return _ReactorState()

        @harness.app.react(_ReactorState)
        async def _reactor(events: list[str]) -> None:
            reactor_calls.extend(events)

        handler_called = asyncio.Event()

        @harness.app.telemetry("sensor", interval=5.0)
        async def sensor(state: _ReactorState) -> _ThermoState:
            state.mark("ran")
            handler_called.set()
            return {"bad": True}  # ty: ignore[invalid-return-type]

        async def _shutdown() -> None:
            await handler_called.wait()
            await asyncio.sleep(0.05)
            harness.trigger_shutdown()

        with unittest.mock.patch(
            "cosalette._runners._telemetry_runner._normalize_telemetry_return",
            side_effect=cosalette.ReturnValidationError("bad return"),
        ):
            asyncio.create_task(_shutdown())
            await asyncio.wait_for(harness.run(), timeout=2.0)

        # Error must be published
        error_msgs = harness.mqtt.get_messages_for("testapp/error")
        assert len(error_msgs) >= 1
        # Reactor must NOT have been dispatched
        assert reactor_calls == []

    async def test_group_invalid_typed_return_does_not_dispatch_reactor(
        self,
    ) -> None:
        """Group telemetry: failing handler skips reactor dispatch; sibling runs.

        With normalization failure, dispatch_reactors must not be called for the
        failing handler.  The sibling handler (which succeeds) still dispatches.
        Because FakeClock advances instantly, the group fires N batches during the
        test window.  Per batch: sensor_a fails (0 dispatches) + sensor_b succeeds
        (1 dispatch) = 1 dispatch.  Without the fix it would be 2 dispatches per
        batch.  Asserting dispatch_count == len(sensor_b_publishes) verifies that
        only sensor_b's success triggers dispatch, not sensor_a's failure.

        Technique: Error Guessing + Integration — group scheduler path.
        """
        import unittest.mock

        dispatch_count: list[int] = [0]
        harness = AppHarness.create()

        # Register a reactor so the reactors list is non-empty (otherwise
        # _dispatch_telemetry_reactors returns early before reaching dispatch_reactors).
        @harness.app.state
        def _state() -> _ReactorState:
            return _ReactorState()

        @harness.app.react(_ReactorState)
        async def _reactor(events: list[str]) -> None:
            pass  # dispatch count is tracked via the spy, not this callback

        a_called = asyncio.Event()
        b_called = asyncio.Event()

        @harness.app.telemetry("sensor_a", interval=5.0, group="g")
        async def sensor_a() -> _ThermoState:
            a_called.set()
            return {"bad": True}  # ty: ignore[invalid-return-type]

        @harness.app.telemetry("sensor_b", interval=5.0, group="g")
        async def sensor_b() -> dict[str, object]:
            b_called.set()
            return {"ok": True}

        async def _shutdown() -> None:
            await asyncio.gather(a_called.wait(), b_called.wait())
            await asyncio.sleep(0)  # single event-loop yield, then shut down
            harness.trigger_shutdown()

        # Patch normalize to fail for sensor_a only
        from cosalette._runners._telemetry_runner import (
            _normalize_telemetry_return as _orig_normalize,
        )

        def _patched_normalize(reg: Any, result: Any) -> Any:
            if reg.name == "sensor_a":
                raise cosalette.ReturnValidationError("bad return")
            return _orig_normalize(reg, result)

        async def _spy_dispatch(*args: Any) -> None:
            dispatch_count[0] += 1

        with (
            unittest.mock.patch(
                "cosalette._runners._telemetry_runner._normalize_telemetry_return",
                side_effect=_patched_normalize,
            ),
            unittest.mock.patch(
                "cosalette._reactors.dispatch_reactors",
                side_effect=_spy_dispatch,
            ),
        ):
            asyncio.create_task(_shutdown())
            await asyncio.wait_for(harness.run(), timeout=2.0)

        # sensor_b must have published successfully
        b_msgs = harness.mqtt.get_messages_for("testapp/sensor_b/state")
        assert len(b_msgs) >= 1

        # Error must have been published for sensor_a
        error_msgs = harness.mqtt.get_messages_for("testapp/error")
        assert len(error_msgs) >= 1

        # Per batch: sensor_a fails (0 dispatches) + sensor_b succeeds (1 dispatch).
        # So total dispatches must equal total sensor_b publishes.  If sensor_a's
        # failure were incorrectly triggering dispatch, the count would be 2× that.
        assert dispatch_count[0] == len(b_msgs)

    async def test_single_invalid_typed_return_does_not_record_cb_success(
        self,
    ) -> None:
        """Circuit breaker must not record success on return-normalization failure.

        Start the CB with 2 pre-existing consecutive failures.  After normalization
        failure, record_success must NOT be called — consecutive_failures stays at 2.
        With the old buggy code, record_success() would reset it to 0.

        Technique: State Transition Testing.
        """
        import unittest.mock

        from cosalette._retry import CircuitBreaker

        cb = CircuitBreaker(threshold=5)
        cb.record_failure()
        cb.record_failure()
        assert cb.consecutive_failures == 2

        harness = AppHarness.create()
        handler_called = asyncio.Event()

        @harness.app.telemetry("sensor", interval=5.0, circuit_breaker=cb)
        async def sensor() -> _ThermoState:
            handler_called.set()
            return {"bad": True}  # ty: ignore[invalid-return-type]

        async def _shutdown() -> None:
            await handler_called.wait()
            await asyncio.sleep(0.05)
            harness.trigger_shutdown()

        with unittest.mock.patch(
            "cosalette._runners._telemetry_runner._normalize_telemetry_return",
            side_effect=cosalette.ReturnValidationError("bad return"),
        ):
            asyncio.create_task(_shutdown())
            await asyncio.wait_for(harness.run(), timeout=2.0)

        # record_success resets consecutive_failures to 0; if it was called
        # (the bug), this assertion fails.
        assert cb.consecutive_failures == 2


# ---------------------------------------------------------------------------
# 16. Payload convention guard — no binding when payload=None (finding #3)
# ---------------------------------------------------------------------------


class TestPayloadConventionGuard:
    """payload: SomeModel convention only activates when payload is not None.

    Finding #3: with the ``and payload is not None`` guard added to
    ``_resolve_request_single``, the shorthand payload convention no longer
    fires in scheduled contexts (where the resolver is called without an
    MQTT payload).  The parameter falls through to DI resolution and raises
    ``TypeError`` when the type is not provided.

    Technique: Specification-based + Boundary Value Analysis.
    """

    def test_payload_convention_inactive_in_scheduled_context(self) -> None:
        """payload: SomeModel with payload=None falls through to DI, raising TypeError.

        In scheduled (non-triggered) periodic/telemetry contexts the runner
        calls resolve_request_kwargs without a payload.  The convention guard
        ensures the parameter is not silently bound to a failed JSON parse;
        instead DI raises TypeError because SomeModel is not a registered
        provider.  This documents the correct error mode.

        Technique: Error Guessing — exercises the boundary where the convention
        was previously incorrectly active.
        """

        async def handler(payload: _SetpointCmd) -> None: ...

        plan = build_injection_plan(handler)
        # payload=None → guard fires → DI lookup → TypeError (not in providers)
        with pytest.raises(TypeError):
            resolve_request_kwargs(plan, providers={}, topic=None, payload=None)

    def test_payload_convention_active_when_payload_present(self) -> None:
        """payload: SomeModel with a JSON payload activates typed binding.

        This is the positive case — command and triggered-telemetry contexts
        pass the raw MQTT payload string so the convention fires as expected.

        Technique: Specification-based — happy path for shorthand convention.
        """

        async def handler(payload: _SetpointCmd) -> None: ...

        plan = build_injection_plan(handler)
        result = resolve_request_kwargs(plan, providers={}, payload='{"value": 7.5}')
        assert isinstance(result["payload"], _SetpointCmd)
        assert result["payload"].value == 7.5

    def test_empty_string_payload_triggers_parse_not_bypassed(self) -> None:
        """An empty MQTT body ("") is treated as real input, not as absent payload.

        Before the ``if raw is not None`` fix in parse_payload, ``""`` was
        falsy and short-circuited to None, silently ignoring a legitimate
        (albeit unusual) empty message.  After the fix, JSON parsing is
        attempted — empty string is not valid JSON → PayloadValidationError.

        Technique: Error Guessing — the "" edge case for MQTT payloads.
        """

        async def handler(payload: _SetpointCmd) -> None: ...

        plan = build_injection_plan(handler)
        # Empty string payload — convention fires (not None), JSON parse fails
        with pytest.raises(PayloadValidationError):
            resolve_request_kwargs(plan, providers={}, payload="")


# ---------------------------------------------------------------------------
# 17. Depends() in periodic context — works after resolve_kwargs migration (#4)
# ---------------------------------------------------------------------------


class TestDependsInPeriodicContext:
    """Depends() resolves correctly in periodic handler contexts.

    Before the fix (finding #1), the periodic runner used ``resolve_kwargs``
    which has no Annotated-marker handling — any ``Depends()`` parameter would
    raise ``TypeError``.  After migrating to ``resolve_request_kwargs`` all
    seven non-command call sites support ``Depends`` natively.

    Technique: Regression Testing — verifies the fix to finding #1/#4.
    """

    @pytest.mark.asyncio
    async def test_depends_in_periodic_resolved_via_tick(self) -> None:
        """Depends(fn) in a @app.periodic handler resolves at each tick.

        Uses AppHarness.tick_periodic() which calls resolve_request_kwargs
        directly (without topic/payload) — the same path as production.

        Technique: Integration — exercises _harness.tick_periodic → _injection
        → Depends resolution chain.
        """
        collected: list[int] = []
        harness = AppHarness.create()

        @harness.app.periodic("heartbeat", interval=60.0)
        async def heartbeat(n: Annotated[int, Depends(_get_const_val)]) -> None:
            collected.append(n)

        await harness.tick_periodic("heartbeat")

        assert collected == [42]

    @pytest.mark.asyncio
    async def test_depends_with_zero_arg_callable_in_periodic(self) -> None:
        """Depends(fn) with a zero-arg callable resolves in periodic context.

        Technique: Integration — verifies that the DI provider map passed by
        the harness is threaded through Depends resolution.
        """
        collected: list[str] = []
        harness = AppHarness.create()

        @harness.app.periodic("probe", interval=60.0)
        async def probe(suffix: Annotated[str, Depends(_get_prefix)]) -> None:
            collected.append(suffix)

        await harness.tick_periodic("probe")

        assert collected == ["prefix"]


# ---------------------------------------------------------------------------
# 18. Dual trigger params — first-match-wins (finding #12)
# ---------------------------------------------------------------------------


class TestDualTriggerParams:
    """_find_trigger_kwarg first-match-wins when both TriggerPayload and
    Annotated[T, Payload()] appear in the same handler.

    Finding #12: document that the function returns on the first matching
    parameter, so handler declaration order determines which wins.

    Technique: Specification-based + Boundary Value Analysis.
    """

    def test_trigger_payload_wins_when_declared_first(self) -> None:
        """TriggerPayload before Annotated[T, Payload()] → TriggerPayload returned.

        Declares ``trigger: TriggerPayload`` first in the signature; since
        _find_trigger_kwarg iterates the plan in order, the legacy type wins.

        Technique: Specification-based — first-match semantics.
        """

        async def handler(
            trigger: TriggerPayload,
            cmd: Annotated[_SetpointCmd | None, Payload()],
        ) -> None: ...

        plan = build_injection_plan(handler)
        info = TelemetryRunner._find_trigger_kwarg(plan)
        assert info is not None
        name, annotation = info
        assert name == "trigger"
        assert annotation is TriggerPayload

    def test_annotated_payload_wins_when_declared_first(self) -> None:
        """Annotated[T, Payload()] before TriggerPayload → Payload annotation returned.

        First-match semantics: whichever trigger parameter appears earliest
        in the handler signature is used.

        Technique: Specification-based — order dependency.
        """

        async def handler(
            cmd: Annotated[_SetpointCmd | None, Payload()],
            trigger: TriggerPayload,
        ) -> None: ...

        plan = build_injection_plan(handler)
        info = TelemetryRunner._find_trigger_kwarg(plan)
        assert info is not None
        name, annotation = info
        assert name == "cmd"
        # annotation is the Annotated form, not TriggerPayload
        assert annotation is not TriggerPayload
