"""Framework availability channels in the generated AsyncAPI document (ADR-086).

Every entity that owns an availability topic at runtime (devices, telemetry,
commands and *named* streams) gets one framework channel carrying
``x-cosalette-framework: "availability"``. Root entities share the app-wide
``{prefix}/availability``; root streams are heartbeat-only and get nothing.

Test Techniques Used:
    - Specification-based Testing: channel id, address, payload, bindings,
      extensions and the paired publish operation.
    - Equivalence Partitioning: device / telemetry / command / named stream /
      root stream; prefixed vs unprefixed documents.
    - Boundary Value Analysis: a root name containing ``/`` (Router prefix) —
      the flat ``{prefix}/availability`` boundary.
    - Decision Table Testing: shared telemetry+command name collapses to one
      channel; the single root entity gets the ``availability`` channel.
    - Error Guessing: ``discoverable=False`` must not hide availability; a
      literal ``enabled=False`` must not invent a channel.
    - Parity Testing: the generated addresses equal the topics a real
      ``AppHarness`` run announces ``online`` on.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import replace as dc_replace
from typing import Any

import pytest

from cosalette import App, Router
from cosalette._constants import availability_topic
from cosalette._context import DeviceContext
from cosalette._mcp._introspect import format_asyncapi_table
from cosalette._runners._stream_types import Stream, StreamablePort
from cosalette.testing import AppHarness, ManualClock, MockMqttClient, make_settings

pytestmark = pytest.mark.unit

_FRAMEWORK = "x-cosalette-framework"


def _availability_channels(doc: dict[str, Any]) -> dict[str, Any]:
    """Return only the framework availability channels of *doc*."""
    return {
        cid: ch
        for cid, ch in doc.get("channels", {}).items()
        if ch.get(_FRAMEWORK) == "availability"
    }


def _addresses(doc: dict[str, Any]) -> dict[str, str]:
    """Map each availability channel id to its address."""
    return {cid: ch["address"] for cid, ch in _availability_channels(doc).items()}


class _Reading:
    """Stream item type."""


class _Port:
    """A ``StreamablePort[_Reading]`` that never yields an item."""

    async def open(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def start_scan(self) -> None:
        pass

    async def stop_scan(self) -> None:
        pass

    def register_callback(self, cb: Callable[[_Reading], None]) -> None:
        pass


async def _consume(stream: Stream[_Reading]) -> AsyncIterator[None]:
    async for _ in stream:
        yield


def _stream_app(name: str = "bridge") -> App:
    app = App(name=name, version="1.0.0", store=None)
    app.adapter(StreamablePort[_Reading], _Port)
    return app


# ---------------------------------------------------------------------------
# Shared helper
# ---------------------------------------------------------------------------


class TestAvailabilityTopicHelper:
    """``availability_topic`` is the single topic rule (reporter, generator, HA).

    Technique: Boundary Value Analysis — root vs named, and a root whose name
    contains ``/``.
    """

    def test_named_entity_topic(self) -> None:
        assert availability_topic("house/wiz", "desk", is_root=False) == (
            "house/wiz/desk/availability"
        )

    def test_root_entity_topic_is_flat(self) -> None:
        assert availability_topic("house/wiz", "status", is_root=True) == (
            "house/wiz/availability"
        )

    def test_root_name_with_slash_stays_flat(self) -> None:
        """A Router-prefixed root (``sensors/status``) still announces flat."""
        assert availability_topic("bridge", "sensors/status", is_root=True) == (
            "bridge/availability"
        )


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------


class TestAvailabilityChannelShape:
    """One channel's exact shape and its publish operation.

    Technique: Specification-based Testing.
    """

    def test_channel_shape(self) -> None:
        # Arrange
        app = App(name="bridge", version="1.0.0")

        @app.telemetry("desk", interval=5)
        async def desk() -> dict[str, Any]:
            return {}

        # Act
        channel = app.asyncapi()["channels"]["deskAvailability"]

        # Assert
        assert channel == {
            "address": "bridge/desk/availability",
            "x-cosalette-app": "bridge",
            "messages": {
                "message": {
                    "payload": {"type": "string", "enum": ["online", "offline"]}
                }
            },
            "bindings": {"mqtt": {"qos": 1, "retain": True}},
            "x-cosalette-framework": "availability",
            "x-cosalette-discoverable": False,
        }
        assert "x-cosalette-archetype" not in channel

    def test_publish_operation(self) -> None:
        # Arrange
        app = App(name="bridge", version="1.0.0")

        @app.telemetry("desk", interval=5)
        async def desk() -> dict[str, Any]:
            return {}

        # Act
        operation = app.asyncapi()["operations"]["publishDeskAvailability"]

        # Assert
        assert operation == {
            "action": "send",
            "channel": {"$ref": "#/channels/deskAvailability"},
        }

    def test_identity_tag_is_app_name_under_prefix(self) -> None:
        # Arrange
        app = App(name="bridge", version="1.0.0")

        @app.telemetry("desk", interval=5)
        async def desk() -> dict[str, Any]:
            return {}

        # Act
        doc = app.asyncapi(topic_prefix="house/wiz")

        # Assert
        channel = doc["channels"]["deskAvailability"]
        assert channel["x-cosalette-app"] == "bridge"
        assert channel["address"] == "house/wiz/desk/availability"


class TestAvailabilityChannelCoverage:
    """Which entities own a channel, mirroring the runtime announcement.

    Technique: Equivalence Partitioning and Decision Table Testing.
    """

    def test_one_channel_per_owning_entity(self) -> None:
        # Arrange
        app = _stream_app()

        @app.device("lamp")
        async def lamp(ctx: DeviceContext) -> None:
            pass

        @app.telemetry("uptime", interval=5)
        async def uptime() -> dict[str, Any]:
            return {}

        @app.command("reboot")
        async def reboot(payload: str) -> None:
            pass

        app.stream("feed")(_consume)

        # Act
        addresses = _addresses(app.asyncapi())

        # Assert
        assert addresses == {
            "feedAvailability": "bridge/feed/availability",
            "lampAvailability": "bridge/lamp/availability",
            "rebootAvailability": "bridge/reboot/availability",
            "uptimeAvailability": "bridge/uptime/availability",
        }

    def test_operation_per_channel(self) -> None:
        # Arrange
        app = App(name="bridge", version="1.0.0")

        @app.device("lamp")
        async def lamp(ctx: DeviceContext) -> None:
            pass

        @app.telemetry("uptime", interval=5)
        async def uptime() -> dict[str, Any]:
            return {}

        # Act
        doc = app.asyncapi()

        # Assert
        refs = {
            op["channel"]["$ref"].rsplit("/", 1)[-1]
            for op in doc["operations"].values()
        }
        assert set(_availability_channels(doc)) <= refs

    def test_telemetry_and_command_sharing_a_name_share_one_channel(self) -> None:
        # Arrange
        app = App(name="bridge", version="1.0.0")

        @app.telemetry("status", interval=5)
        async def status_tel() -> dict[str, Any]:
            return {}

        @app.command("status")
        async def status_cmd(payload: str) -> None:
            pass

        # Act
        doc = app.asyncapi()

        # Assert
        assert _addresses(doc) == {"statusAvailability": "bridge/status/availability"}
        assert "publishStatusAvailability" in doc["operations"]

    def test_root_entity_gets_the_single_root_channel(self) -> None:
        """The root entity's channel is ``availability``, beside named ones."""
        # Arrange
        app = App(name="bridge", version="1.0.0")

        @app.telemetry(interval=5)
        async def read_sensor() -> dict[str, Any]:
            return {}

        @app.command("reboot")
        async def reboot(payload: str) -> None:
            pass

        # Act
        doc = app.asyncapi()

        # Assert
        assert _addresses(doc) == {
            "availability": "bridge/availability",
            "rebootAvailability": "bridge/reboot/availability",
        }
        assert doc["operations"]["publishAvailability"] == {
            "action": "send",
            "channel": {"$ref": "#/channels/availability"},
        }

    def test_router_prefixed_root_gets_the_flat_root_channel(self) -> None:
        """A root named ``sensors/status`` announces on ``{prefix}/availability``."""
        # Arrange
        router = Router(prefix="sensors")

        @router.telemetry("status", interval=30)
        async def status_tel() -> dict[str, Any]:
            return {}

        app = App(name="bridge", version="1.0.0")
        app.include_router(router)
        app._telemetry[0] = dc_replace(app._telemetry[0], is_root=True)  # noqa: SLF001

        # Act
        addresses = _addresses(app.asyncapi())

        # Assert
        assert addresses == {"availability": "bridge/availability"}

    def test_root_stream_gets_no_channel(self) -> None:
        # Arrange
        app = _stream_app()
        app.stream(None)(_consume)

        # Act
        addresses = _addresses(app.asyncapi())

        # Assert
        assert addresses == {}

    def test_named_stream_gets_a_channel(self) -> None:
        # Arrange
        app = _stream_app()
        app.stream("feed")(_consume)

        # Act
        addresses = _addresses(app.asyncapi())

        # Assert
        assert addresses == {"feedAvailability": "bridge/feed/availability"}

    @pytest.mark.parametrize(
        ("topic_prefix", "expected"),
        [
            pytest.param(None, "bridge", id="unprefixed"),
            pytest.param("house/wiz", "house/wiz", id="prefixed"),
        ],
    )
    def test_addresses_honour_topic_prefix(
        self, topic_prefix: str | None, expected: str
    ) -> None:
        # Arrange
        app = App(name="bridge", version="1.0.0")

        @app.telemetry("desk", interval=5)
        async def desk() -> dict[str, Any]:
            return {}

        @app.command()
        async def handle(payload: str) -> None:
            pass

        # Act
        addresses = _addresses(app.asyncapi(topic_prefix=topic_prefix))

        # Assert
        assert addresses == {
            "availability": f"{expected}/availability",
            "deskAvailability": f"{expected}/desk/availability",
        }

    def test_non_discoverable_entity_still_gets_availability(self) -> None:
        """``discoverable=False`` hides HA/openHAB output, not availability."""
        # Arrange
        app = App(name="bridge", version="1.0.0")

        @app.telemetry("events", interval=30, discoverable=False)
        async def events() -> dict[str, Any]:
            return {}

        # Act
        addresses = _addresses(app.asyncapi())

        # Assert
        assert addresses == {"eventsAvailability": "bridge/events/availability"}

    def test_literal_disabled_entity_gets_no_channel(self) -> None:
        """``enabled=False`` never registers, so nothing announces either."""
        # Arrange
        app = App(name="bridge", version="1.0.0")

        @app.telemetry("ghost", interval=5, enabled=False)
        async def ghost() -> dict[str, Any]:
            return {}

        # Act
        addresses = _addresses(app.asyncapi())

        # Assert
        assert addresses == {}

    def test_empty_app_has_no_availability_channel(self) -> None:
        # Arrange
        app = App(name="bridge", version="1.0.0")

        # Act
        doc = app.asyncapi()

        # Assert
        assert _addresses(doc) == {}


# ---------------------------------------------------------------------------
# MCP manifest table
# ---------------------------------------------------------------------------


class TestManifestTableOmitsFrameworkChannels:
    """The manifest table lists app channels once, not their availability twins.

    Technique: Specification-based Testing.
    """

    def test_availability_channels_not_listed(self) -> None:
        # Arrange
        app = App(name="bridge", version="1.0.0")

        @app.telemetry("desk", interval=5)
        async def desk() -> dict[str, Any]:
            return {}

        # Act
        table = format_asyncapi_table(app.asyncapi())

        # Assert
        assert "bridge/desk/state" in table
        assert "availability" not in table.lower()
        assert "Other" not in table


# ---------------------------------------------------------------------------
# Runtime parity
# ---------------------------------------------------------------------------


def _parity_named_app() -> App:
    """Named entities of every kind, a shared name and a root stream."""
    app = _stream_app("parity")

    @app.device("lamp")
    async def lamp(ctx: DeviceContext) -> None:
        while not ctx.shutdown_requested:
            await ctx.sleep(1.0)

    @app.telemetry("uptime", interval=5)
    async def uptime() -> dict[str, Any]:
        return {"s": 1}

    @app.command("uptime")
    async def uptime_cmd(payload: str) -> None:
        pass

    @app.command("reboot")
    async def reboot(payload: str) -> None:
        pass

    app.stream("feed")(_consume)
    app.stream(None)(_consume)
    return app


def _parity_root_app() -> App:
    """A root telemetry on ``{prefix}/availability`` beside named entities."""
    app = _stream_app("parity")

    @app.telemetry(interval=5)
    async def read_sensor() -> dict[str, Any]:
        return {"s": 1}

    @app.command("reboot")
    async def reboot(payload: str) -> None:
        pass

    app.stream("feed")(_consume)
    return app


class TestRuntimeSchemaParity:
    """Generated availability addresses equal the topics the runtime announces.

    Technique: Parity Testing — one real ``AppHarness`` run per app shape.
    """

    @pytest.mark.parametrize(
        "build", [_parity_named_app, _parity_root_app], ids=["named", "root"]
    )
    async def test_generated_addresses_match_announced_topics(
        self, build: Callable[[], App]
    ) -> None:
        # Arrange
        app = build()
        expected = set(_addresses(app.asyncapi()).values())
        clock = ManualClock()
        harness = AppHarness(
            app=app,
            mqtt=MockMqttClient(),
            clock=clock,
            settings=make_settings(),
            shutdown_event=asyncio.Event(),
            run_streams=True,
        )
        run = asyncio.create_task(harness.run())

        # Act
        try:
            await clock.settle(
                until=lambda: bool(harness.messages_for("parity/status"))
            )
        finally:
            harness.trigger_shutdown()
            await asyncio.wait_for(run, timeout=5.0)

        # Assert
        announced = {
            topic
            for topic, payload, _, _ in harness.published()
            if topic.endswith("/availability") and payload == "online"
        }
        assert announced == expected
