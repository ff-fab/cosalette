"""Tests for device/stream publishes that fail because MQTT is not connected.

A broker outage must not end an ``@app.device`` or ``@app.stream`` generator:
their contexts drop a not-connected publish at ``DEBUG`` and return, so the
handler keeps running and its next publish reaches the broker once it is back.
Telemetry and command contexts keep raising, because the telemetry runner
relies on the exception to leave its publish de-duplication state untouched
(ADR-011 amendment, 2026-10-01).

Test Techniques Used:
    - Decision Table Testing: tolerate flag x exception type -> raise / drop
    - Specification-based Testing: which wiring builders set the flag
    - State Transition Testing: disconnected publish -> connected publish
    - Mock-based Isolation: MockMqttClient.raise_on_publish
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest

from cosalette import App, DeviceContext, MqttNotConnectedError
from cosalette._errors import ErrorPublisher
from cosalette._health import HealthReporter
from cosalette._runners._stream_types import Stream
from cosalette._runners._telemetry_runner import TelemetryRunner
from cosalette._wiring import build_contexts, build_stream_contexts
from cosalette.testing import FakeClock, MockMqttClient, make_settings

pytestmark = pytest.mark.unit

PREFIX = "myapp"


def _disconnected() -> MockMqttClient:
    return MockMqttClient(
        raise_on_publish=MqttNotConnectedError("MqttClient is not connected")
    )


def _ctx(mqtt: MockMqttClient, *, tolerate_not_connected: bool) -> DeviceContext:
    return DeviceContext(
        name="blind",
        settings=make_settings(),
        mqtt=mqtt,
        topic_prefix=PREFIX,
        shutdown_event=asyncio.Event(),
        adapters={},
        clock=FakeClock(),
        tolerate_not_connected=tolerate_not_connected,
    )


async def _publish_state(ctx: DeviceContext) -> None:
    await ctx.publish_state({"position": 1})


async def _publish(ctx: DeviceContext) -> None:
    await ctx.publish("raw", "1")


async def _sub_entity_publish_state(ctx: DeviceContext) -> None:
    async with ctx.sub_entity("motor") as motor:
        await motor.publish_state({"running": True})


_PUBLISHERS = [
    pytest.param(_publish_state, id="publish_state"),
    pytest.param(_publish, id="publish"),
    pytest.param(_sub_entity_publish_state, id="sub_entity.publish_state"),
]


class TestContextDecisionTable:
    """Technique: Decision Table Testing — tolerate flag x exception type."""

    @pytest.mark.parametrize("publisher", _PUBLISHERS)
    async def test_tolerant_context_drops_not_connected(
        self, publisher: Any, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Arrange
        mqtt = _disconnected()
        ctx = _ctx(mqtt, tolerate_not_connected=True)

        # Act
        with caplog.at_level("DEBUG", logger="cosalette._context._device_context"):
            await publisher(ctx)

        # Assert
        assert mqtt.published == []
        assert "MQTT not connected" in caplog.text

    @pytest.mark.parametrize("publisher", _PUBLISHERS)
    async def test_strict_context_raises_not_connected(self, publisher: Any) -> None:
        # Arrange
        ctx = _ctx(_disconnected(), tolerate_not_connected=False)

        # Act / Assert
        with pytest.raises(MqttNotConnectedError):
            await publisher(ctx)

    @pytest.mark.parametrize("publisher", _PUBLISHERS)
    async def test_tolerant_context_raises_other_errors(self, publisher: Any) -> None:
        # Arrange
        mqtt = MockMqttClient(raise_on_publish=RuntimeError("boom"))
        ctx = _ctx(mqtt, tolerate_not_connected=True)

        # Act / Assert
        with pytest.raises(RuntimeError, match="boom"):
            await publisher(ctx)

    async def test_default_is_strict(self) -> None:
        # Arrange
        ctx = DeviceContext(
            name="blind",
            settings=make_settings(),
            mqtt=_disconnected(),
            topic_prefix=PREFIX,
            shutdown_event=asyncio.Event(),
            adapters={},
            clock=FakeClock(),
        )

        # Act / Assert
        with pytest.raises(MqttNotConnectedError):
            await ctx.publish_state({"position": 1})


class TestWiring:
    """Technique: Specification-based Testing — which contexts tolerate."""

    @staticmethod
    def _app() -> App:
        app = App(name=PREFIX, version="1.0.0")

        @app.device("blind")
        async def blind() -> AsyncIterator[None]:
            yield  # pragma: no cover

        @app.telemetry("climate", interval=30)
        async def climate() -> dict[str, object]:
            return {}  # pragma: no cover

        @app.command("valve")
        async def valve(payload: str) -> None:
            return None  # pragma: no cover

        @app.stream("feed")
        async def feed(stream: Stream[int]) -> AsyncIterator[None]:
            yield  # pragma: no cover

        return app

    @pytest.mark.parametrize(
        ("name", "tolerant"),
        [("blind", True), ("climate", False), ("valve", False)],
    )
    async def test_build_contexts(self, name: str, *, tolerant: bool) -> None:
        # Arrange
        app = self._app()
        contexts = build_contexts(
            [*app.devices, *app.telemetry_registrations, *app.commands],
            make_settings(),
            _disconnected(),
            PREFIX,
            asyncio.Event(),
            {},
            FakeClock(),
        )

        # Act / Assert
        if tolerant:
            await contexts[name].publish_state({"x": 1})
        else:
            with pytest.raises(MqttNotConnectedError):
                await contexts[name].publish_state({"x": 1})

    async def test_build_stream_contexts(self) -> None:
        # Arrange
        app = self._app()
        contexts = build_stream_contexts(
            list(app.stream_registrations),
            make_settings(),
            _disconnected(),
            PREFIX,
            asyncio.Event(),
            {},
            FakeClock(),
        )

        # Act / Assert — does not raise
        await contexts["feed"].publish_state({"x": 1})


async def test_device_generator_survives_broker_outage() -> None:
    """A device keeps running through an outage and publishes after reconnect.

    Technique: State Transition Testing — disconnected publish, then connected.
    """
    # Arrange
    mqtt = _disconnected()
    app = App(name=PREFIX, version="1.0.0")
    steps: list[int] = []

    @app.device("blind")
    async def blind(ctx: DeviceContext) -> AsyncIterator[None]:
        for position in (1, 2):
            await ctx.publish_state({"position": position})
            steps.append(position)
            mqtt.raise_on_publish = None
            yield

    contexts = build_contexts(
        list(app.devices),
        make_settings(),
        mqtt,
        PREFIX,
        asyncio.Event(),
        {},
        FakeClock(),
    )
    reporter = HealthReporter(
        mqtt=mqtt, topic_prefix=PREFIX, version="1.0.0", clock=FakeClock()
    )
    reporter.set_device_status("blind", "ok")

    # Act
    await TelemetryRunner(store=None).run_device(
        app.devices[0],
        contexts["blind"],
        ErrorPublisher(mqtt=mqtt, topic_prefix=PREFIX),
        reporter,
    )

    # Assert
    assert steps == [1, 2]
    assert mqtt.get_messages_for(f"{PREFIX}/blind/state") == [
        ('{"position":2}', True, 1)
    ]
    assert mqtt.get_messages_for(f"{PREFIX}/error") == []
