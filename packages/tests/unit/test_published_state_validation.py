"""Unit tests for state_model validation of published state.

Covers the runtime half of ADR-045's 2026-08-07 amendment: a declared
``state_model`` on ``@app.device`` or ``@app.stream`` validates and normalises
every ``ctx.publish_state()`` payload, because those handler archetypes have no
return value for :func:`normalize_handler_return` to work on.

Three layers are exercised:

1. :func:`validate_state_payload` in isolation (the validation primitive).
2. :class:`DeviceContext.publish_state` (the enforcement point).
3. :func:`build_contexts` / :func:`build_stream_contexts` (the wiring that
   decides *which* registrations get a model installed).

Test Techniques Used:
    - Specification-based Testing: the documented contract of
      ``validate_state_payload`` (validate → normalise → dict-or-wrap) and of
      ``publish_state`` (validate only when a model was declared).
    - Equivalence Partitioning: payload classes (conforming / non-conforming /
      coercible) crossed with model classes (BaseModel / dataclass / TypeAdapter
      -compatible primitive) and with ``state_model`` present vs. absent.
    - Branch/Condition Coverage: every branch of ``validate_state_payload``
      (validation error, non-ValidationError failure, serialisation failure,
      dict result, non-dict result) and the ``if self._state_model is not None``
      guard in ``publish_state``.
    - Error Guessing: the specific defect this epic exists to close — a model
      declared but not enforced — plus the OWASP A03 requirement that the
      rejected payload never appears in the error text.
    - Round-trip Testing: aliases and coercion applied on the way to the wire.
"""

from __future__ import annotations

import asyncio
import json
import warnings
from dataclasses import dataclass
from typing import Any

import pytest
from pydantic import BaseModel, Field, model_serializer

from cosalette._app import App
from cosalette._context import DeviceContext
from cosalette._mqtt import MockMqttClient
from cosalette._runners._contracts import (
    ReturnValidationError,
    validate_state_payload,
)
from cosalette._runners._stream_types import Stream
from cosalette._wiring import build_contexts, build_stream_contexts
from cosalette.testing import FakeClock, make_settings

pytestmark = pytest.mark.unit


# =============================================================================
# Models and fixtures
# =============================================================================


class Reading(BaseModel):
    """Conforming state contract used across the file."""

    sensor: str
    value: float


class AliasedReading(BaseModel):
    """State contract whose wire name differs from the field name."""

    model_config = {"populate_by_name": True}

    celsius: float = Field(serialization_alias="temp_c")


class OptionalReading(BaseModel):
    """State contract with an optional field — the clause D shape change."""

    sensor: str
    brightness: int | None = None


@dataclass(frozen=True, slots=True)
class DataclassReading:
    """Non-pydantic state contract — TypeAdapter handles dataclasses too."""

    sensor: str
    value: float


@pytest.fixture
def mqtt() -> MockMqttClient:
    """Recording MQTT double."""
    return MockMqttClient()


def make_ctx(
    mqtt: MockMqttClient,
    *,
    state_model: type | None = None,
    handler_name: str | None = None,
    name: str = "dev",
) -> DeviceContext:
    """Build a minimal DeviceContext wired to *mqtt*."""
    return DeviceContext(
        name=name,
        settings=make_settings(),
        mqtt=mqtt,
        topic_prefix="testapp",
        shutdown_event=asyncio.Event(),
        adapters={},
        clock=FakeClock(),
        state_model=state_model,
        handler_name=handler_name,
    )


# =============================================================================
# validate_state_payload — the validation primitive
# =============================================================================


class TestValidateStatePayloadAccepts:
    """Conforming payloads pass and come back normalised.

    Technique: Specification-based Testing — validate → dump_python(mode="json")
    round trip. Equivalence Partitioning over model kinds.
    """

    def test_conforming_dict_returns_normalised_dict(self) -> None:
        """A payload matching the model is returned as a JSON-mode dict."""
        # Arrange
        payload: dict[str, object] = {"sensor": "a", "value": 3.5}

        # Act
        result = validate_state_payload(payload, Reading)

        # Assert
        assert result == {"sensor": "a", "value": 3.5}

    def test_dataclass_model_is_supported(self) -> None:
        """A frozen dataclass works as a state_model, not just a BaseModel."""
        result = validate_state_payload({"sensor": "a", "value": 1.0}, DataclassReading)

        assert result == {"sensor": "a", "value": 1.0}

    def test_int_is_coerced_to_declared_float(self) -> None:
        """Validation normalises: an int for a float field becomes a float.

        Technique: Round-trip Testing — the wire form follows the model, not the
        caller's literal.
        """
        result = validate_state_payload({"sensor": "a", "value": 3}, Reading)

        assert result["value"] == 3.0
        assert isinstance(result["value"], float)

    def test_aliased_model_serialises_by_field_name(self) -> None:
        """Serialisation uses field names, not serialization_alias.

        ``dump_python`` is called without ``by_alias``, matching what
        ``normalize_return`` does for telemetry/command returns. Pinned here so
        the two publish paths cannot drift apart silently.
        """
        result = validate_state_payload({"celsius": 21.5}, AliasedReading)

        assert result == {"celsius": 21.5}
        assert "temp_c" not in result

    def test_extra_key_is_dropped_by_the_model(self) -> None:
        """Keys the model does not declare do not reach the wire.

        This is the substantive difference from publishing the raw dict.
        """
        result = validate_state_payload(
            {"sensor": "a", "value": 1.0, "debug_token": "secret"}, Reading
        )

        assert result == {"sensor": "a", "value": 1.0}
        assert "debug_token" not in result

    @pytest.mark.parametrize(
        ("model", "payload", "expected"),
        [
            (Reading, {"sensor": "a", "value": 0.0}, {"sensor": "a", "value": 0.0}),
            (Reading, {"sensor": "", "value": -1.5}, {"sensor": "", "value": -1.5}),
            (
                DataclassReading,
                {"sensor": "z", "value": 1e308},
                {"sensor": "z", "value": 1e308},
            ),
        ],
        ids=["zero", "empty_str_and_negative", "large_float"],
    )
    def test_boundary_values_pass(
        self, model: type, payload: dict[str, object], expected: dict[str, object]
    ) -> None:
        """Boundary values inside the contract are accepted unchanged.

        Technique: Boundary Value Analysis.
        """
        assert validate_state_payload(payload, model) == expected


class TestExcludeNoneOnPublishedState:
    """ADR-068 clause D: ``None`` values are omitted, not published as ``null``.

    This is a wire-format change in 0.9.0 — a key a 0.8.x app published as
    ``null`` is now absent — accepted deliberately so that all four publishing
    archetypes emit one shape and the conditional-key idiom keeps working.

    Technique: Specification-based Testing on clause D; Equivalence
    Partitioning over the three ways an optional field can arrive (omitted /
    explicitly ``None`` / present).
    """

    def test_omitted_optional_field_is_absent_from_the_payload(self) -> None:
        """The key the caller left out does not come back as ``null``."""
        # Arrange
        payload: dict[str, object] = {"sensor": "a"}

        # Act
        result = validate_state_payload(payload, OptionalReading)

        # Assert
        assert result == {"sensor": "a"}
        assert "brightness" not in result

    def test_explicit_none_is_also_omitted(self) -> None:
        """``exclude_none`` is unconditional: an explicit ``None`` is dropped too.

        The cost of clause D — publishing a deliberate ``null`` through a
        ``state_model`` is no longer possible.
        """
        # Arrange
        payload: dict[str, object] = {"sensor": "a", "brightness": None}

        # Act
        result = validate_state_payload(payload, OptionalReading)

        # Assert
        assert result == {"sensor": "a"}

    def test_present_optional_field_is_published(self) -> None:
        """A supplied optional value is unaffected."""
        # Arrange / Act
        result = validate_state_payload(
            {"sensor": "a", "brightness": 7}, OptionalReading
        )

        # Assert
        assert result == {"sensor": "a", "brightness": 7}

    def test_required_fields_are_unaffected(self) -> None:
        """A model with no optional fields dumps exactly as it did in 0.8.x.

        Technique: Back-to-Back — the negative control for the shape change.
        """
        # Arrange / Act
        result = validate_state_payload({"sensor": "a", "value": 1.5}, Reading)

        # Assert
        assert result == {"sensor": "a", "value": 1.5}

    async def test_device_publish_state_omits_none_on_the_wire(
        self, mqtt: MockMqttClient
    ) -> None:
        """End to end: the retained device payload carries no ``null`` key."""
        # Arrange
        ctx = make_ctx(mqtt, state_model=OptionalReading)

        # Act
        await ctx.publish_state({"sensor": "a"})

        # Assert
        assert json.loads(mqtt.published[0][1]) == {"sensor": "a"}


class TestValidateStatePayloadRejects:
    """Non-conforming payloads raise ReturnValidationError.

    Technique: Error Guessing — the exact defect closes, plus the
    OWASP A03 requirement on error text.
    """

    def test_missing_field_raises(self) -> None:
        """A payload missing a required field raises ReturnValidationError."""
        with pytest.raises(ReturnValidationError):
            validate_state_payload({"sensor": "a"}, Reading)

    def test_wrong_type_raises(self) -> None:
        """A payload with an uncoercible value raises ReturnValidationError."""
        with pytest.raises(ReturnValidationError):
            validate_state_payload({"sensor": "a", "value": "not-a-number"}, Reading)

    def test_error_names_the_field_the_model_and_the_handler(self) -> None:
        """The message identifies field, model, and handler."""
        # Arrange / Act
        with pytest.raises(ReturnValidationError) as excinfo:
            validate_state_payload({"sensor": "a"}, Reading, handler="pkg.mod.rx")

        # Assert
        message = str(excinfo.value)
        assert "value" in message, message
        assert "Reading" in message, message
        assert "pkg.mod.rx" in message, message

    def test_error_names_every_failing_field(self) -> None:
        """All offending field paths appear, not just the first."""
        with pytest.raises(ReturnValidationError) as excinfo:
            validate_state_payload({}, Reading)

        message = str(excinfo.value)
        assert "sensor" in message
        assert "value" in message

    def test_error_omits_handler_context_when_not_supplied(self) -> None:
        """Without a handler name the message carries no empty 'in handler' clause."""
        with pytest.raises(ReturnValidationError) as excinfo:
            validate_state_payload({"sensor": "a"}, Reading)

        assert "in handler" not in str(excinfo.value)

    def test_error_does_not_echo_the_rejected_payload(self) -> None:
        """Rejected values never appear in the message (OWASP A03).

        Technique: Error Guessing — log-injection / secret-leak guard.
        """
        with pytest.raises(ReturnValidationError) as excinfo:
            validate_state_payload(
                {"sensor": "<script>alert(1)</script>", "value": "SUPERSECRET"},
                Reading,
            )

        message = str(excinfo.value)
        assert "SUPERSECRET" not in message
        assert "<script>" not in message

    def test_error_exposes_the_pydantic_cause(self) -> None:
        """The originating ValidationError is retained for callers that want it."""
        with pytest.raises(ReturnValidationError) as excinfo:
            validate_state_payload({"sensor": "a"}, Reading)

        assert excinfo.value.__cause__ is not None

    def test_error_carries_the_handler_attribute(self) -> None:
        """handler= is stored on the exception, not only in the message."""
        with pytest.raises(ReturnValidationError) as excinfo:
            validate_state_payload({"sensor": "a"}, Reading, handler="pkg.mod.rx")

        assert excinfo.value.handler == "pkg.mod.rx"


class TestValidateStatePayloadNonModelTargets:
    """Behaviour for state_model values that are not dict-shaped models.

    Technique: Branch Coverage — the non-dict return branch and the
    non-ValidationError failure branch.
    """

    def test_non_dict_result_is_wrapped_under_value(self) -> None:
        """A model that serialises to a scalar yields {"value": ...}.

        Mirrors ``normalize_return``'s wrapping rule so the published payload is
        always a JSON object.
        """

        class ScalarSerialised(BaseModel):
            celsius: float

            @model_serializer
            def _ser(self) -> float:
                return self.celsius

        result = validate_state_payload({"celsius": 3}, ScalarSerialised)

        assert result == {"value": 3.0}

    def test_unadaptable_model_raises_return_validation_error(self) -> None:
        """A state_model TypeAdapter cannot handle fails loudly, not silently.

        Technique: Error Guessing — a misconfigured decorator must not degrade
        into unvalidated publishing.
        """

        class Unadaptable:
            """A class TypeAdapter cannot build a schema for."""

            __slots__ = ("x",)

            def __init__(self, x: object) -> None:
                self.x = x

        with pytest.raises(ReturnValidationError):
            validate_state_payload({"x": 1}, Unadaptable)

    def test_unadaptable_error_names_the_model_and_handler(self) -> None:
        """The non-ValidationError branch also identifies model and handler."""

        class Unadaptable:
            __slots__ = ("x",)

            def __init__(self, x: object) -> None:
                self.x = x

        with pytest.raises(ReturnValidationError) as excinfo:
            validate_state_payload({"x": 1}, Unadaptable, handler="pkg.mod.rx")

        message = str(excinfo.value)
        assert "Unadaptable" in message
        assert "pkg.mod.rx" in message

    def test_unnamed_annotation_falls_back_to_repr(self) -> None:
        """An annotation without __name__ is labelled by repr, not crashed on."""
        with pytest.raises(ReturnValidationError) as excinfo:
            validate_state_payload({"sensor": "a"}, Reading | None)  # type: ignore[arg-type]

        # The union has no __name__; the label must still be non-empty.
        assert "Published state" in str(excinfo.value)


class TestValidateStatePayloadClosesTheDumpOnlyLoophole:
    """Regression guard for the design trap behind this issue.

    An unguarded ``dump_python`` on a BaseModel adapter accepts a plain dict
    and only emits a Pydantic serializer *warning*, which would have left
    ``state_model`` non-load-bearing.  ``validate_state_payload`` has always
    validated first; since ADR-068 clause B ``normalize_return`` closes the
    same hole from the other side with ``warnings="error"``, so both
    publishing paths now reject the identical payload.

    Technique: Error Guessing — pin the behaviour that makes the feature real.
    """

    def test_dump_python_alone_would_not_have_rejected_the_payload(self) -> None:
        """Prove the loophole exists, so the guards below have meaning."""
        from cosalette._runners._contracts import _get_adapter

        adapter = _get_adapter(Reading)

        # dump_python on a non-conforming dict does NOT raise.
        with pytest.warns(UserWarning):
            leaked = adapter.dump_python({"sensor": "a"}, mode="json")

        assert leaked == {"sensor": "a"}

    def test_validate_state_payload_rejects_what_dump_python_allows(self) -> None:
        """validate_state_payload validates first, so the same input raises."""
        with pytest.raises(ReturnValidationError):
            validate_state_payload({"sensor": "a"}, Reading)

    def test_normalize_return_also_rejects_what_dump_python_allows(self) -> None:
        """ADR-068 clause B: the telemetry/command path fails closed too.

        Run under production warning filters — this suite's
        ``filterwarnings = ["error"]`` would make the pre-0.9.0 fast path
        appear to reject the payload on its own.
        """
        from cosalette._runners._contracts import normalize_return

        with (
            warnings.catch_warnings(),
            pytest.raises(ReturnValidationError),
        ):
            warnings.simplefilter("always")
            normalize_return({"sensor": "a"}, Reading)


# =============================================================================
# DeviceContext.publish_state — the enforcement point
# =============================================================================


class TestPublishStateWithModel:
    """publish_state validates when a state_model was declared.

    Technique: Specification-based Testing — the documented publish contract
    (retained, qos=1, validated payload).
    """

    async def test_conforming_payload_is_published(self, mqtt: MockMqttClient) -> None:
        """A conforming payload reaches the state topic retained at qos 1."""
        # Arrange
        ctx = make_ctx(mqtt, state_model=Reading)

        # Act
        await ctx.publish_state({"sensor": "a", "value": 3.5})

        # Assert
        assert len(mqtt.published) == 1
        topic, payload, retain, qos = mqtt.published[0]
        assert topic == "testapp/dev/state"
        assert json.loads(payload) == {"sensor": "a", "value": 3.5}
        assert retain is True
        assert qos == 1

    async def test_payload_is_normalised_before_publishing(
        self, mqtt: MockMqttClient
    ) -> None:
        """The wire payload is the model's form, not the caller's literal."""
        ctx = make_ctx(mqtt, state_model=Reading)

        await ctx.publish_state({"sensor": "a", "value": 3, "extra": "dropped"})

        assert json.loads(mqtt.published[0][1]) == {"sensor": "a", "value": 3.0}

    async def test_non_conforming_payload_raises(self, mqtt: MockMqttClient) -> None:
        """A non-conforming payload raises ReturnValidationError."""
        ctx = make_ctx(mqtt, state_model=Reading, handler_name="pkg.mod.rx")

        with pytest.raises(ReturnValidationError, match="pkg.mod.rx"):
            await ctx.publish_state({"sensor": "a"})

    async def test_nothing_is_published_when_validation_fails(
        self, mqtt: MockMqttClient
    ) -> None:
        """Validation happens before the publish — no partial state on the wire.

        Technique: State Transition Testing — the failure path must not mutate
        the retained topic.
        """
        ctx = make_ctx(mqtt, state_model=Reading)

        with pytest.raises(ReturnValidationError):
            await ctx.publish_state({"sensor": "a"})

        assert mqtt.published == []

    async def test_retain_false_is_still_honoured(self, mqtt: MockMqttClient) -> None:
        """Validation does not override the caller's retain flag."""
        ctx = make_ctx(mqtt, state_model=Reading)

        await ctx.publish_state({"sensor": "a", "value": 1.0}, retain=False)

        assert mqtt.published[0][2] is False


class TestPublishStateWithoutModel:
    """state_model=None skips validation entirely.

    Technique: Branch Coverage — the ``if self._state_model is not None`` guard.
    Equivalence Partitioning — payloads that would fail *any* model.
    """

    async def test_arbitrary_payload_is_published_unchanged(
        self, mqtt: MockMqttClient
    ) -> None:
        """Without a model the payload goes out exactly as supplied."""
        # Arrange
        ctx = make_ctx(mqtt)
        payload: dict[str, object] = {"whatever": 1, "nested": {"a": [1, 2]}}

        # Act
        await ctx.publish_state(payload)

        # Assert
        assert json.loads(mqtt.published[0][1]) == payload

    async def test_payload_that_no_model_would_accept_still_publishes(
        self, mqtt: MockMqttClient
    ) -> None:
        """The unvalidated path is preserved for existing handlers."""
        ctx = make_ctx(mqtt)

        await ctx.publish_state({"sensor": "a"})  # would fail Reading

        assert json.loads(mqtt.published[0][1]) == {"sensor": "a"}

    async def test_no_type_adapter_is_built(self, mqtt: MockMqttClient) -> None:
        """No adapter is constructed when no model was declared (no new cost).

        Technique: Specification-based Testing — the documented zero-cost claim.
        """
        from unittest.mock import patch

        ctx = make_ctx(mqtt)

        with patch("cosalette._runners._contracts._get_adapter") as get_adapter:
            await ctx.publish_state({"anything": True})

        get_adapter.assert_not_called()

    async def test_empty_payload_is_published(self, mqtt: MockMqttClient) -> None:
        """An empty dict is a valid unvalidated payload.

        Technique: Boundary Value Analysis.
        """
        ctx = make_ctx(mqtt)

        await ctx.publish_state({})

        assert json.loads(mqtt.published[0][1]) == {}


class TestPublishStateScope:
    """Only the static state topic is validated.

    Technique: Specification-based Testing — the documented escape hatches.
    """

    async def test_raw_publish_is_not_validated(self, mqtt: MockMqttClient) -> None:
        """ctx.publish() bypasses state_model by design."""
        ctx = make_ctx(mqtt, state_model=Reading)

        await ctx.publish("diagnostic", json.dumps({"not": "a reading"}))

        assert len(mqtt.published) == 1
        assert json.loads(mqtt.published[0][1]) == {"not": "a reading"}

    async def test_sub_entity_publish_state_is_not_validated(
        self, mqtt: MockMqttClient
    ) -> None:
        """ctx.sub_entity(...).publish_state() bypasses state_model by design."""
        ctx = make_ctx(mqtt, state_model=Reading)

        async with ctx.sub_entity("child") as sub:
            await sub.publish_state({"not": "a reading"})

        published = [
            (topic, payload)
            for topic, payload, _retain, _qos in mqtt.published
            if topic == "testapp/dev/child/state" and payload
        ]
        assert len(published) == 1
        assert json.loads(published[0][1]) == {"not": "a reading"}


# =============================================================================
# Wiring — which registrations get a model installed
# =============================================================================


class TestDeviceWiring:
    """build_contexts installs state_model from device registrations.

    Technique: Specification-based Testing — the wiring contract documented on
    build_contexts. Branch Coverage — device vs. non-device registration.
    """

    @staticmethod
    def _contexts(app: App, mqtt: MockMqttClient) -> dict[str, DeviceContext]:
        return build_contexts(
            [*app.devices, *app.telemetry_registrations, *app.commands],
            make_settings(),
            mqtt,
            "testapp",
            asyncio.Event(),
            {},
            FakeClock(),
        )

    async def test_device_state_model_reaches_publish_state(
        self, mqtt: MockMqttClient
    ) -> None:
        """A declared device state_model validates at runtime (BREAKING 0.6.0)."""
        # Arrange
        app = App(name="testapp", version="1.0.0")

        @app.device("valve", state_model=Reading)
        async def valve(ctx: DeviceContext) -> Any:
            yield

        ctx = self._contexts(app, mqtt)["valve"]

        # Act / Assert
        with pytest.raises(ReturnValidationError, match="Reading"):
            await ctx.publish_state({"sensor": "a"})

    async def test_device_without_state_model_is_unvalidated(
        self, mqtt: MockMqttClient
    ) -> None:
        """Handlers that never declared a model keep publishing unvalidated."""
        app = App(name="testapp", version="1.0.0")

        @app.device("valve")
        async def valve(ctx: DeviceContext) -> Any:
            yield

        ctx = self._contexts(app, mqtt)["valve"]
        await ctx.publish_state({"sensor": "a"})

        assert json.loads(mqtt.published[0][1]) == {"sensor": "a"}

    async def test_device_handler_name_is_in_the_error(
        self, mqtt: MockMqttClient
    ) -> None:
        """The failing handler is identifiable from the message alone."""
        app = App(name="testapp", version="1.0.0")

        @app.device("valve", state_model=Reading)
        async def valve(ctx: DeviceContext) -> Any:
            yield

        ctx = self._contexts(app, mqtt)["valve"]

        with pytest.raises(ReturnValidationError) as excinfo:
            await ctx.publish_state({"sensor": "a"})

        assert "valve" in str(excinfo.value)

    async def test_telemetry_context_gets_no_state_model(
        self, mqtt: MockMqttClient
    ) -> None:
        """Telemetry's state_model validates the *return value*, not publishes.

        Re-validating in publish_state would double-check the same contract, so
        build_contexts deliberately installs nothing for telemetry.
        """
        app = App(name="testapp", version="1.0.0")

        @app.telemetry("climate", interval=30, state_model=Reading)
        async def climate():
            return {"sensor": "a", "value": 1.0}

        ctx = self._contexts(app, mqtt)["climate"]

        # A payload Reading would reject publishes fine through this context.
        await ctx.publish_state({"unrelated": True})

        assert json.loads(mqtt.published[0][1]) == {"unrelated": True}

    async def test_command_context_gets_no_state_model(
        self, mqtt: MockMqttClient
    ) -> None:
        """Same reasoning as telemetry: the command runner validates the return."""
        app = App(name="testapp", version="1.0.0")

        @app.command("valve", state_model=Reading)
        async def valve():
            return {"sensor": "a", "value": 1.0}

        ctx = self._contexts(app, mqtt)["valve"]
        await ctx.publish_state({"unrelated": True})

        assert json.loads(mqtt.published[0][1]) == {"unrelated": True}


class TestStreamWiring:
    """build_stream_contexts installs state_model from stream registrations.

    Technique: Specification-based Testing — the wiring contract.
    """

    @staticmethod
    def _contexts(app: App, mqtt: MockMqttClient) -> dict[str, DeviceContext]:
        return build_stream_contexts(
            list(app.stream_registrations),
            make_settings(),
            mqtt,
            "testapp",
            asyncio.Event(),
            {},
            FakeClock(),
        )

    async def test_stream_state_model_reaches_publish_state(
        self, mqtt: MockMqttClient
    ) -> None:
        """A declared stream state_model validates at runtime."""
        app = App(name="testapp", version="1.0.0")

        @app.stream("rx", state_model=Reading)
        async def rx(stream: Stream[Reading], ctx: DeviceContext) -> Any:
            async for _ in stream:
                yield

        ctx = self._contexts(app, mqtt)["rx"]

        with pytest.raises(ReturnValidationError, match="Reading"):
            await ctx.publish_state({"sensor": "a"})

    async def test_stream_conforming_payload_publishes(
        self, mqtt: MockMqttClient
    ) -> None:
        """A conforming payload is published normalised."""
        app = App(name="testapp", version="1.0.0")

        @app.stream("rx", state_model=Reading)
        async def rx(stream: Stream[Reading], ctx: DeviceContext) -> Any:
            async for _ in stream:
                yield

        ctx = self._contexts(app, mqtt)["rx"]
        await ctx.publish_state({"sensor": "a", "value": 3})

        assert json.loads(mqtt.published[0][1]) == {"sensor": "a", "value": 3.0}

    async def test_stream_without_state_model_is_unvalidated(
        self, mqtt: MockMqttClient
    ) -> None:
        """state_model=None on a stream skips validation entirely."""
        app = App(name="testapp", version="1.0.0")

        @app.stream("rx")
        async def rx(stream: Stream[Reading], ctx: DeviceContext) -> Any:
            async for _ in stream:
                yield

        ctx = self._contexts(app, mqtt)["rx"]
        await ctx.publish_state({"sensor": "a"})

        assert json.loads(mqtt.published[0][1]) == {"sensor": "a"}

    async def test_stream_handler_name_is_in_the_error(
        self, mqtt: MockMqttClient
    ) -> None:
        """The failing stream handler is identifiable from the message."""
        app = App(name="testapp", version="1.0.0")

        @app.stream("rx", state_model=Reading)
        async def rx(stream: Stream[Reading], ctx: DeviceContext) -> Any:
            async for _ in stream:
                yield

        ctx = self._contexts(app, mqtt)["rx"]

        with pytest.raises(ReturnValidationError) as excinfo:
            await ctx.publish_state({"sensor": "a"})

        assert "rx" in str(excinfo.value)
