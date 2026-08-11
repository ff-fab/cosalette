"""Integration tests — typed command payload and typed telemetry return.

Validates Pydantic model deserialization for command payloads and
serialization for telemetry return values.

See Also:
    ADR-046 — Typed handler contracts.
    ADR-007 — Testing strategy (integration layer).
"""

from __future__ import annotations

import asyncio
import json
from typing import Annotated

import pytest
from pydantic import BaseModel

from cosalette.mqtt import Payload
from cosalette.testing import AppHarness

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Pydantic model helpers
# ---------------------------------------------------------------------------


class _SetpointCmd(BaseModel):
    """Pydantic model for the thermostat setpoint command payload."""

    value: float
    unit: str = "celsius"


class _ThermoState(BaseModel):
    """Pydantic model for the thermostat state reply."""

    setpoint: float
    unit: str
    accepted: bool


# ---------------------------------------------------------------------------
# TestTypedCommandPayload — Pydantic model payload
# ---------------------------------------------------------------------------


class TestTypedCommandPayload:
    """Typed command payload: Pydantic model deserialization + response.

    Technique:
        - Integration Testing: call_command dispatches JSON through the
          typed binding layer (ADR-046) into a Pydantic model.
        - State-based Testing: returned model is serialized and published.

    See Also:
        ADR-046 — Typed handler contracts.
    """

    @pytest.mark.parametrize(
        "payload,expected_value,expected_unit",
        [
            ({"value": 21.5, "unit": "celsius"}, 21.5, "celsius"),
            ({"value": 19.0}, 19.0, "celsius"),  # default field applied
            ({"value": 25.0, "unit": "fahrenheit"}, 25.0, "fahrenheit"),  # non-default
        ],
    )
    async def test_pydantic_payload_deserialized_and_response_published(
        self,
        payload: dict[str, object],
        expected_value: float,
        expected_unit: str,
    ) -> None:
        """JSON payload is deserialized to Pydantic model; model returned as state.

        Covers: full payload, default-field omission, and non-default unit.

        Technique: Equivalence Partitioning + Integration Testing +
        State-based Testing.
        """
        harness = AppHarness.create()
        received_cmd: _SetpointCmd | None = None

        @harness.app.command("thermostat")
        async def thermostat_cmd(
            cmd: Annotated[_SetpointCmd, Payload()],
        ) -> dict[str, object]:
            nonlocal received_cmd
            received_cmd = cmd
            return {"setpoint": cmd.value, "unit": cmd.unit}

        await harness.call_command("thermostat", payload)

        assert received_cmd is not None
        assert isinstance(received_cmd, _SetpointCmd)
        assert received_cmd.value == expected_value
        assert received_cmd.unit == expected_unit

        msgs = harness.messages_for("testapp/thermostat/state")
        assert len(msgs) == 1
        response = json.loads(msgs[0][0])
        assert response["setpoint"] == expected_value
        assert response["unit"] == expected_unit


# ---------------------------------------------------------------------------
# TestTypedTelemetryReturn — Pydantic model return
# ---------------------------------------------------------------------------


class TestTypedTelemetryReturn:
    """Typed telemetry return: Pydantic model is serialized on publish.

    Technique:
        - Integration Testing: full lifecycle via AppHarness.
        - State-based Testing: model returned by handler appears as JSON on
          the state topic.

    See Also:
        ADR-046 — Typed handler contracts.
    """

    async def test_pydantic_return_serialized_to_state_topic(self) -> None:
        """Telemetry returning a Pydantic model is serialized to JSON on publish.

        Technique: Integration Testing + State-based Testing.
        """
        harness = AppHarness.create()

        @harness.app.telemetry("thermo", interval=0.01)
        async def thermo() -> _ThermoState:
            return _ThermoState(setpoint=22.0, unit="celsius", accepted=True)

        async def _shutdown() -> None:
            while not harness.messages_for("testapp/thermo/state"):
                await asyncio.sleep(0)
            harness.trigger_shutdown()

        _task = asyncio.create_task(_shutdown())
        try:
            await asyncio.wait_for(harness.run(), timeout=5.0)
        finally:
            _task.cancel()
            await asyncio.gather(_task, return_exceptions=True)

        msgs = harness.messages_for("testapp/thermo/state")
        assert len(msgs) >= 1
        payload = json.loads(msgs[0][0])
        assert payload["setpoint"] == 22.0
        assert payload["unit"] == "celsius"
        assert payload["accepted"] is True
