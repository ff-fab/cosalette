"""Unit tests for ADR-068 state_model= return-value enforcement.

Covers the ``@app.telemetry`` / ``@app.command`` half of the *one rule for
state_model* guarantee: a declared contract validates the handler return
value, a non-conforming plain ``dict`` is rejected rather than republished,
and the rejection reaches ``{prefix}/{name}/error`` with the state publish
suppressed.

Test Techniques Used:
    - Specification-based Testing: the documented ``normalize_return``
      contract (fast path → validate_python fallback → dict-or-wrap) and the
      runner contract (error topic published, state topic silent).
    - Equivalence Partitioning: return-value classes (model instance /
      conforming dict / non-conforming dict / dict with extra keys) crossed
      with the two value-returning archetypes (telemetry, command).
    - Error Guessing: the exact defect ADR-068 exists to close — a plain
      dict that only triggers a swallowed Pydantic serializer warning.
    - Back-to-Back (negative control) Testing: a conforming dict must
      serialise byte-identically to the pre-``warnings="error"`` output, so
      the fail-closed change cannot silently reshape valid payloads, and the
      clause C / clause D output shapes are compared against each other.
    - Branch/Condition Coverage: the EAFP fast path taken (model instance)
      versus the ``validate_python`` fallback taken (plain dict).
"""

from __future__ import annotations

import asyncio
import json
import unittest.mock

import pytest
from pydantic import TypeAdapter

from cosalette._runners._contracts import (
    ReturnValidationError,
    _get_adapter,
    normalize_return,
    validate_state_payload,
)
from cosalette.testing import AppHarness
from tests.fixtures.state_models import (
    OptionalReading,
    Reading,
    production_warning_filters,
)

pytestmark = pytest.mark.unit


async def _shutdown_when_published(harness: AppHarness, topic: str) -> None:
    """Shut the harness down once *topic* has a message, or after a bounded wait.

    The bound matters: a regression that publishes to the *state* topic instead
    must fail on the assertion, not hang the suite.
    """
    for _ in range(10_000):
        if harness.messages_for(topic):
            break
        await asyncio.sleep(0)
    harness.trigger_shutdown()


# =============================================================================
# normalize_return — the fail-closed fast path (clause B)
# =============================================================================


class TestNormalizeReturnFailsClosed:
    """A non-conforming plain dict can no longer ride the dump_python fast path.

    ADR-068 clause B: ``warnings="error"`` promotes
    ``PydanticSerializationUnexpectedValue`` to ``PydanticSerializationError``,
    so the existing ``except Exception:`` routes the value through
    ``validate_python``.

    Technique: Equivalence Partitioning + Error Guessing.
    """

    def test_non_conforming_dict_raises_return_validation_error(self) -> None:
        """A dict missing a required field is rejected, not republished."""
        # Arrange / Act / Assert
        with production_warning_filters(), pytest.raises(ReturnValidationError):
            normalize_return({"sensor": "a"}, Reading, handler="rx")

    def test_non_conforming_dict_error_does_not_echo_the_payload(self) -> None:
        """ADR-061 / OWASP A03: the rejected value never appears in the message."""
        # Arrange / Act
        with production_warning_filters(), pytest.raises(ReturnValidationError) as exc:
            normalize_return({"sensor": "secret-serial"}, Reading, handler="rx")

        # Assert
        assert "secret-serial" not in str(exc.value)

    def test_conforming_dict_is_accepted(self) -> None:
        """A dict that matches the model still publishes."""
        # Arrange / Act
        with production_warning_filters():
            result = normalize_return({"sensor": "a", "value": 1.5}, Reading)

        # Assert
        assert result == {"sensor": "a", "value": 1.5}

    def test_extra_keys_are_dropped_by_validation(self) -> None:
        """Keys not on the model are dropped — validation working, loudly."""
        # Arrange / Act
        with production_warning_filters():
            result = normalize_return(
                {"sensor": "a", "value": 1.5, "stray": 9}, Reading
            )

        # Assert
        assert result == {"sensor": "a", "value": 1.5}

    def test_model_instance_skips_validate_python(self) -> None:
        """The fast path stays free for genuine model instances (ADR-013/021)."""
        # Arrange
        _get_adapter(Reading)  # warm the cache so patching cannot affect it

        # Act
        with unittest.mock.patch.object(
            TypeAdapter, "validate_python", autospec=True
        ) as spy:
            result = normalize_return(Reading(sensor="a", value=1.5), Reading)

        # Assert
        assert spy.call_count == 0
        assert result == {"sensor": "a", "value": 1.5}


class TestConformingDictNegativeControl:
    """Guardrail: fail-closed must not reshape a payload that already conforms.

    ``warnings="error"`` also promotes unrelated Pydantic serializer warnings,
    so a conforming dict now travels the ``validate_python`` fallback instead
    of the fast path.  Its serialised bytes must be unchanged.

    Technique: Back-to-Back Testing — new path output compared against the
    pre-change ``dump_python(value, mode="json")`` expression.
    """

    def test_conforming_dict_output_is_byte_identical_to_unguarded_dump(
        self,
    ) -> None:
        """The 0.8.x expression and the 0.9.0 path agree byte for byte."""
        # Arrange — the pre-change fast path, warning and all.
        payload: dict[str, object] = {"sensor": "a", "value": 1.5}
        with pytest.warns(UserWarning):
            legacy = _get_adapter(Reading).dump_python(payload, mode="json")

        # Act
        with production_warning_filters():
            current = normalize_return(payload, Reading)

        # Assert
        assert json.dumps(current, sort_keys=True) == json.dumps(legacy, sort_keys=True)

    def test_model_instance_output_is_byte_identical_to_unguarded_dump(self) -> None:
        """The fast-path case is untouched by the change."""
        # Arrange
        model = Reading(sensor="a", value=1.5)
        legacy = _get_adapter(Reading).dump_python(model, mode="json")

        # Act
        current = normalize_return(model, Reading)

        # Assert
        assert json.dumps(current, sort_keys=True) == json.dumps(legacy, sort_keys=True)


# =============================================================================
# Runner wiring — error topic published, state topic suppressed
# =============================================================================


class TestTelemetryRejectionRouting:
    """A rejected telemetry return reaches the error topic, not the state topic.

    Technique: Specification-based Testing over the telemetry publish
    pipeline; Error Guessing on the retained-topic blast radius.
    """

    async def test_non_conforming_return_publishes_error_and_no_state(self) -> None:
        """ReturnValidationError → {prefix}/{name}/error, state stays silent."""
        # Arrange — annotation and state_model agree, so no clause-F warning;
        # the handler simply returns a dict that does not match them.
        harness = AppHarness.create()

        @harness.app.telemetry("sensor", interval=0.01, state_model=Reading)
        async def sensor() -> Reading:
            return {"sensor": "a"}  # ty: ignore[invalid-return-type]

        async def _shutdown() -> None:
            await _shutdown_when_published(harness, "testapp/sensor/error")

        # Act
        with production_warning_filters():
            task = asyncio.create_task(_shutdown())
            await asyncio.wait_for(harness.run(), timeout=5.0)
            await task

        # Assert
        assert harness.messages_for("testapp/sensor/error")
        assert harness.messages_for("testapp/sensor/state") == []


class TestCommandRejectionRouting:
    """A rejected command return reaches the error topic, not the state topic.

    Technique: Specification-based Testing over ``run_command``'s error
    pipeline; Branch Coverage on the publish-suppressed branch.
    """

    async def test_non_conforming_return_publishes_error_and_no_state(self) -> None:
        """ReturnValidationError → {prefix}/{name}/error, state stays silent."""
        # Arrange
        harness = AppHarness.create()

        @harness.app.command("thermostat", state_model=Reading)
        async def thermostat() -> Reading:
            return {"sensor": "a"}  # ty: ignore[invalid-return-type]

        async def _drive() -> None:
            await harness.inject_command("thermostat", {})
            await _shutdown_when_published(harness, "testapp/thermostat/error")

        # Act
        with production_warning_filters():
            task = asyncio.create_task(_drive())
            await asyncio.wait_for(harness.run(), timeout=5.0)
            await task

        # Assert
        assert harness.messages_for("testapp/thermostat/error")
        assert harness.messages_for("testapp/thermostat/state") == []

    async def test_conforming_return_still_publishes_state(self) -> None:
        """The happy path is unaffected — a conforming dict reaches the wire."""
        # Arrange
        harness = AppHarness.create()

        @harness.app.command("thermostat", state_model=Reading)
        async def thermostat() -> Reading:
            return {"sensor": "a", "value": 1.5}  # ty: ignore[invalid-return-type]

        async def _drive() -> None:
            await harness.inject_command("thermostat", {})
            await _shutdown_when_published(harness, "testapp/thermostat/state")

        # Act
        with production_warning_filters():
            task = asyncio.create_task(_drive())
            await asyncio.wait_for(harness.run(), timeout=5.0)
            await task

        # Assert
        msgs = harness.messages_for("testapp/thermostat/state")
        assert json.loads(msgs[0][0]) == {"sensor": "a", "value": 1.5}
        assert harness.messages_for("testapp/thermostat/error") == []


class TestExcludeNoneOnNormalizeReturn:
    """ADR-068 clause C: a validated return dumps with ``exclude_none=True``.

    Validation fills an omitted optional field with ``None``; without
    ``exclude_none`` that would turn the conditional-key idiom into an explicit
    ``null`` on a retained topic.  Clause D applies the same rule to
    ``validate_state_payload``, so telemetry, command, device and stream all
    publish one shape.

    Technique: Specification-based Testing on clause C; Back-to-Back Testing
    against ``validate_state_payload`` for the cross-archetype parity claim.
    """

    def test_omitted_optional_field_is_absent_after_validation(self) -> None:
        """The fallback path must not null-fill what the handler left out."""
        # Arrange / Act
        with production_warning_filters():
            result = normalize_return({"sensor": "a"}, OptionalReading)

        # Assert
        assert result == {"sensor": "a"}

    def test_present_optional_field_survives(self) -> None:
        """A supplied optional value is published unchanged."""
        # Arrange / Act
        with production_warning_filters():
            result = normalize_return({"sensor": "a", "brightness": 7}, OptionalReading)

        # Assert
        assert result == {"sensor": "a", "brightness": 7}

    def test_model_instance_fast_path_still_null_fills(self) -> None:
        """Clause C covers the validated dump only — the fast path is untouched.

        A handler returning a model instance already made the ``None``
        explicit, and keeping the fast path allocation-free matters more
        (ADR-013 / ADR-021) than a shape that the handler chose itself.
        """
        # Arrange / Act
        result = normalize_return(OptionalReading(sensor="a"), OptionalReading)

        # Assert
        assert result == {"sensor": "a", "brightness": None}

    def test_telemetry_and_device_paths_agree_on_shape(self) -> None:
        """The one-output-shape claim: clause C and clause D produce the same dict."""
        # Arrange
        payload: dict[str, object] = {"sensor": "a"}

        # Act
        with production_warning_filters():
            from_return = normalize_return(payload, OptionalReading)
        from_publish = validate_state_payload(payload, OptionalReading)

        # Assert
        assert from_return == from_publish
