"""Stream availability, ``stale_after=`` and ``feeds=`` (ADR-081 amendment).

Covers: decoration-time validation of the new ``@app.stream`` /
``Router.stream`` kwargs, bootstrap validation of ``feeds=``, the named
stream's ``{prefix}/{stream}/availability`` topic end to end (startup,
manual marks, freshness, shutdown), the root stream's heartbeat-only
behaviour, availability propagation to fed entities, the connect
re-announce and the ADR-048 retained-cleanup snapshot.

Test Techniques Used:
    - Equivalence Partitioning: named vs root streams; valid vs invalid
      ``feeds`` names (unknown, root entity, root stream, bare ``str``).
    - Boundary Value Analysis: ``stale_after`` at 0, negative, non-finite,
      ``bool`` and the smallest valid value.
    - State Transition Testing: online -> offline (stale / manual / crash)
      -> online (next item / mark_available).
    - Decision Table Testing: stream source x fed entity's own source.
    - Specification-based Testing: topics, payloads and heartbeat statuses.
"""

from __future__ import annotations

import asyncio
import json
import math
from collections.abc import AsyncIterator, Callable
from typing import Any, cast

import pytest

from cosalette import App, Router
from cosalette._context import DeviceContext
from cosalette._health import HealthReporter
from cosalette._mqtt import MqttPort
from cosalette._registration import _StreamRegistration
from cosalette._runners._stream_types import Stream, StreamablePort
from cosalette._wiring import (
    register_connect_reannounce,
    resolve_stream_health,
    track_streams,
)
from cosalette._wiring._retained_cleanup import build_entity_snapshot
from cosalette.testing import (
    AppHarness,
    FakeClock,
    ManualClock,
    MockMqttClient,
    make_settings,
)
from tests.fixtures.mqtt import FakeConnectAwareMqttClient

pytestmark = pytest.mark.unit

PREFIX = "testapp"


class _Reading:
    """Stream item type."""


class _Port:
    """StreamablePort[_Reading] whose items the test pushes by hand."""

    def __init__(self) -> None:
        self._put: Callable[[_Reading], None] | None = None

    async def open(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def start_scan(self) -> None:
        pass

    async def stop_scan(self) -> None:
        pass

    def register_callback(self, cb: Callable[[_Reading], None]) -> None:
        self._put = cb

    def push(self) -> None:
        assert self._put is not None
        self._put(_Reading())


async def _consume(stream: Stream[_Reading]) -> AsyncIterator[None]:
    async for _ in stream:
        yield


def _app(**kwargs: Any) -> App:
    app = App(name=PREFIX, version="1.0.0", store=None, **kwargs)
    app.adapter(StreamablePort[_Reading], _Port)
    return app


def _harness(app: App, clock: ManualClock) -> tuple[AppHarness, _Port]:
    port = _Port()
    app._adapters.pop(StreamablePort[_Reading], None)  # noqa: SLF001
    app.adapter(StreamablePort[_Reading], lambda: port)
    harness = AppHarness(
        app=app,
        mqtt=MockMqttClient(),
        clock=clock,
        settings=make_settings(),
        shutdown_event=asyncio.Event(),
        run_streams=True,
    )
    return harness, port


def _availability(harness: AppHarness, entity: str) -> list[str]:
    return [p for p, _, _ in harness.messages_for(f"{PREFIX}/{entity}/availability")]


def _heartbeat_status(harness: AppHarness, entity: str) -> str | None:
    beats = [
        json.loads(payload)
        for payload, _, _ in harness.messages_for(f"{PREFIX}/status")
        if payload.startswith("{")
    ]
    if not beats:
        return None
    status = beats[-1]["devices"].get(entity, {}).get("status")
    assert status is None or isinstance(status, str)
    return status


# ---------------------------------------------------------------------------
# Decoration-time validation
# ---------------------------------------------------------------------------


class TestDecorationValidation:
    """``stale_after=`` and ``feeds=`` fail fast where knowable.

    Technique: Boundary Value Analysis (``stale_after``) and Equivalence
    Partitioning (``feeds`` shapes, named vs root stream).
    """

    @pytest.mark.parametrize("bad", [0, -1.0, math.inf, math.nan, True], ids=str)
    def test_invalid_stale_after_rejected(self, bad: Any) -> None:
        """Zero, negative, non-finite and bool bounds are rejected."""
        # Arrange
        app = _app()

        # Act / Assert
        with pytest.raises(ValueError, match="stale_after"):
            app.stream("feed", stale_after=bad)(_consume)

    def test_smallest_valid_stale_after_and_feeds_stored(self) -> None:
        """A tiny positive bound is kept; ``feeds`` is normalised to a tuple."""
        # Arrange
        app = _app()

        # Act
        app.stream("feed", stale_after=1e-9, feeds=["radon", "co2"])(_consume)

        # Assert
        (reg,) = app._streams  # noqa: SLF001
        assert reg.stale_after == 1e-9
        assert reg.feeds == ("radon", "co2")

    def test_feeds_as_str_rejected(self) -> None:
        """A bare ``str`` is a sequence of characters, not of names."""
        # Arrange
        app = _app()

        # Act / Assert
        with pytest.raises(TypeError, match=r"feeds=\['radon'\]"):
            app.stream("feed", feeds="radon")(_consume)

    def test_feeds_on_root_stream_rejected(self) -> None:
        """A root stream cannot drive other entities' availability."""
        # Arrange
        app = _app()

        # Act / Assert
        with pytest.raises(ValueError, match="Root stream"):
            app.stream(feeds=["radon"])(_consume)

    def test_stale_after_on_root_stream_allowed(self) -> None:
        """A root stream may declare ``stale_after`` (heartbeat-only)."""
        # Arrange
        app = _app()

        # Act
        app.stream(stale_after=30.0)(_consume)

        # Assert
        assert app._streams[0].stale_after == 30.0  # noqa: SLF001

    def test_deferred_enabled_path_validates(self) -> None:
        """The ``enabled=callable`` path applies the same checks."""
        # Arrange
        app = _app()

        # Act / Assert
        with pytest.raises(ValueError, match="Root stream"):
            app.stream(enabled=lambda _s: True, feeds=["radon"])(_consume)

    def test_add_stream_validates(self) -> None:
        """The imperative ``add_stream`` applies the same checks."""
        # Arrange
        app = _app()

        # Act / Assert
        with pytest.raises(ValueError, match="stale_after"):
            app.add_stream("feed", _consume, stale_after=0)

    def test_router_validates_and_preserves_fields(self) -> None:
        """Router streams validate and keep both fields through inclusion."""
        # Arrange
        router = Router()
        app = _app()

        # Act
        router.stream("feed", stale_after=5.0, feeds=["radon"])(_consume)
        app.include_router(router)
        with pytest.raises(ValueError, match="Root stream"):
            Router().stream(feeds=["radon"])(_consume)

        # Assert
        (reg,) = app._streams  # noqa: SLF001
        assert (reg.stale_after, reg.feeds) == (5.0, ("radon",))


# ---------------------------------------------------------------------------
# Bootstrap validation
# ---------------------------------------------------------------------------


def _stream_reg(name: str = "feed", **kwargs: Any) -> _StreamRegistration:
    return _StreamRegistration(name=name, func=_consume, injection_plan=[], **kwargs)


class TestResolveStreamHealth:
    """``feeds`` names are checked against the resolved entity set.

    Technique: Equivalence Partitioning — known device / telemetry, unknown
    name, root entity; callable ``stale_after`` resolution.
    """

    def _entities(self) -> App:
        app = App(name=PREFIX, version="1.0.0", store=None)

        @app.telemetry("radon", interval=10)
        async def radon() -> dict[str, float]:  # pragma: no cover
            return {"v": 1.0}

        @app.device("valve")
        async def valve(ctx: DeviceContext) -> None:  # pragma: no cover
            pass

        @app.telemetry(interval=10)
        async def root_reading() -> dict[str, float]:  # pragma: no cover
            return {"v": 1.0}

        return app

    def test_known_device_and_telemetry_accepted(self) -> None:
        """Registered device and telemetry names pass."""
        # Arrange
        app = self._entities()
        streams = [_stream_reg(feeds=("radon", "valve"))]

        # Act
        resolve_stream_health(
            streams,
            app._devices,
            app._telemetry,
            make_settings(),  # noqa: SLF001
        )

        # Assert
        assert streams[0].feeds == ("radon", "valve")

    def test_unknown_name_rejected(self) -> None:
        """A name no device or telemetry registers fails fast."""
        # Arrange
        app = self._entities()
        streams = [_stream_reg(feeds=("nope",))]

        # Act / Assert
        with pytest.raises(ValueError, match="feeds unknown entity 'nope'"):
            resolve_stream_health(
                streams,
                app._devices,
                app._telemetry,
                make_settings(),  # noqa: SLF001
            )

    def test_root_entity_rejected(self) -> None:
        """A root entity owns the app-wide topic; a stream may not drive it."""
        # Arrange
        app = self._entities()
        streams = [_stream_reg(feeds=("root_reading",))]

        # Act / Assert
        with pytest.raises(ValueError, match="feeds root entity 'root_reading'"):
            resolve_stream_health(
                streams,
                app._devices,
                app._telemetry,
                make_settings(),  # noqa: SLF001
            )

    def test_callable_stale_after_resolved(self) -> None:
        """A ``(Settings) -> float`` bound is resolved; ``None`` stays ``None``."""
        # Arrange
        streams = [
            _stream_reg("a", stale_after=lambda _s: 12.0),
            _stream_reg("b"),
        ]

        # Act
        resolve_stream_health(streams, [], [], make_settings())

        # Assert
        assert [s.stale_after for s in streams] == [12.0, None]

    def test_callable_stale_after_must_be_positive(self) -> None:
        """A callable resolving to zero is rejected like telemetry's."""
        # Arrange
        streams = [_stream_reg(stale_after=lambda _s: 0.0)]

        # Act / Assert
        with pytest.raises(ValueError, match="Stream stale_after for 'feed'"):
            resolve_stream_health(streams, [], [], make_settings())

    async def test_app_run_fails_fast_on_unknown_feed(self) -> None:
        """Bootstrap raises before anything runs."""
        # Arrange
        app = _app()
        app.stream("feed", feeds=["ghost"])(_consume)
        harness, _ = _harness(app, ManualClock())

        # Act / Assert
        with pytest.raises(ValueError, match="ghost"):
            await asyncio.wait_for(harness.run(), timeout=5.0)


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------


class TestStreamAvailabilityEndToEnd:
    """The stream's availability follows its health through a real run.

    Technique: State Transition Testing.
    """

    @pytest.mark.parametrize("root", [False, True], ids=["named", "root"])
    async def test_heartbeat_ok_from_startup(self, root: bool) -> None:
        """Before any item, the stream shows ``ok``; only a named one is online."""
        # Arrange
        clock = ManualClock()
        app = _app()
        app.stream(None if root else "feed")(_consume)
        harness, _ = _harness(app, clock)
        run = asyncio.create_task(harness.run())

        # Act
        try:
            await clock.settle(
                until=lambda: bool(harness.messages_for(f"{PREFIX}/status"))
            )
            status = _heartbeat_status(harness, "_consume" if root else "feed")
        finally:
            harness.trigger_shutdown()
            await asyncio.wait_for(run, timeout=5.0)

        # Assert
        assert status == "ok"
        topics = [t for t, _, _, _ in harness.published()]
        assert f"{PREFIX}/availability" not in topics
        if not root:
            assert _availability(harness, "feed") == ["online", "offline"]

    async def test_stale_then_item_restores(self) -> None:
        """No item for ``stale_after`` -> offline + ``stale``; item -> online."""
        # Arrange
        clock = ManualClock()
        app = _app(heartbeat_interval=1.0)
        app.stream("feed", stale_after=2.0)(_consume)
        harness, port = _harness(app, clock)
        run = asyncio.create_task(harness.run())

        try:
            # Act
            await clock.settle()
            await harness.advance_time(3.0)
            stale_status = _heartbeat_status(harness, "feed")
            stale_availability = _availability(harness, "feed")
            port.push()
            await clock.settle()
            await harness.advance_time(1.0)
            fresh_status = _heartbeat_status(harness, "feed")
        finally:
            harness.trigger_shutdown()
            await asyncio.wait_for(run, timeout=5.0)

        # Assert
        assert stale_status == "stale"
        assert stale_availability == ["online", "offline"]
        assert fresh_status == "ok"
        assert _availability(harness, "feed")[:3] == ["online", "offline", "online"]

    @pytest.mark.parametrize("root", [False, True], ids=["named", "root"])
    async def test_manual_marks_from_stream_context(self, root: bool) -> None:
        """``ctx.mark_unavailable()``/``mark_available()`` work in a stream.

        Technique: Equivalence Partitioning — a root stream's marks stay in
        the heartbeat and never reach ``{prefix}/availability``.
        """
        # Arrange
        clock = ManualClock()
        app = _app(heartbeat_interval=1.0)
        statuses: list[str | None] = []

        async def feed(
            stream: Stream[_Reading], ctx: DeviceContext
        ) -> AsyncIterator[None]:
            await ctx.mark_unavailable()
            async for _ in stream:
                await ctx.mark_available()
                yield

        app.stream(None if root else "feed")(feed)
        harness, port = _harness(app, clock)
        run = asyncio.create_task(harness.run())

        try:
            # Act
            await clock.settle()
            await harness.advance_time(1.0)
            statuses.append(_heartbeat_status(harness, "feed"))
            port.push()
            await clock.settle()
            await harness.advance_time(1.0)
            statuses.append(_heartbeat_status(harness, "feed"))
        finally:
            harness.trigger_shutdown()
            await asyncio.wait_for(run, timeout=5.0)

        # Assert
        assert statuses == ["unavailable", "ok"]
        topics = [t for t, _, _, _ in harness.published()]
        assert f"{PREFIX}/availability" not in topics
        if not root:
            assert _availability(harness, "feed") == [
                "online",
                "offline",
                "online",
                "offline",
            ]

    async def test_crash_propagates_to_fed_entity(self) -> None:
        """A crashed stream holds its fed telemetry offline until recovery.

        Technique: State Transition Testing on the fed entity — online ->
        offline (``stream:feed``) -> online at the stream's first item.
        """
        # Arrange
        clock = ManualClock()
        app = _app()
        attempts = 0

        @app.telemetry("radon", interval=100)
        async def radon() -> dict[str, float]:
            return {"v": 1.0}

        @app.stream("feed", feeds=["radon"])
        async def feed(stream: Stream[_Reading]) -> AsyncIterator[None]:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                msg = "decoder crashed"
                raise RuntimeError(msg)
            async for _ in stream:
                yield

        harness, port = _harness(app, clock)
        run = asyncio.create_task(harness.run())

        try:
            # Act
            await clock.settle(
                until=lambda: bool(harness.messages_for(f"{PREFIX}/feed/error"))
            )
            radon_after_crash = _availability(harness, "radon")
            await harness.advance_time(1.0)  # restart backoff
            await clock.settle(until=lambda: attempts >= 2)
            port.push()
            await clock.settle()
            radon_after_item = _availability(harness, "radon")
        finally:
            harness.trigger_shutdown()
            await asyncio.wait_for(run, timeout=5.0)

        # Assert
        assert radon_after_crash == ["online", "offline"]
        assert radon_after_item == ["online", "offline", "online"]


# ---------------------------------------------------------------------------
# Re-announce and retained cleanup
# ---------------------------------------------------------------------------


class TestStreamLifecycleTopics:
    """Named streams join the announce, re-announce and cleanup snapshot.

    Technique: Equivalence Partitioning — named vs root stream.
    """

    def _app_with_streams(self) -> App:
        app = _app()
        app.stream("feed")(_consume)

        async def root_feed(stream: Stream[_Reading]) -> AsyncIterator[None]:
            async for _ in stream:  # pragma: no cover
                yield

        app.stream()(root_feed)
        return app

    async def test_connect_announces_and_reconnect_reasserts(self) -> None:
        """First connect: online; reconnect: re-asserts an offline mark."""
        # Arrange
        app = self._app_with_streams()
        fake = FakeConnectAwareMqttClient()
        clock = FakeClock()
        reporter = HealthReporter(
            mqtt=cast(MqttPort, fake), topic_prefix=PREFIX, version="1", clock=clock
        )
        track_streams(app._streams, reporter)  # noqa: SLF001
        register_connect_reannounce(
            fake,
            app,
            reporter,
            app._announced_registrations,  # noqa: SLF001
            PREFIX,
            None,
        )

        # Act
        await fake.simulate_connect()
        await reporter.publish_device_unavailable("feed", source="supervisor")
        fake.reset()
        await fake.simulate_connect()

        # Assert
        assert fake.get_messages_for(f"{PREFIX}/feed/availability") == [
            ("offline", True, 1)
        ]
        topics = [t for t, _, _, _ in fake.published]
        assert f"{PREFIX}/availability" not in topics

    def test_snapshot_holds_named_stream_availability_only(self) -> None:
        """The named stream owns ``availability``; the root stream is absent."""
        # Arrange
        app = self._app_with_streams()

        # Act
        snapshot = build_entity_snapshot(app._announced_registrations)  # noqa: SLF001

        # Assert
        entities = cast("dict[str, Any]", snapshot["entities"])
        assert entities == {
            "feed": {"is_root": False, "retained_kinds": ["availability"]}
        }

    @pytest.mark.parametrize(
        ("name", "expected"), [("feed", True), (None, False)], ids=["named", "root"]
    )
    def test_config_disabled_named_stream_is_dynamic(
        self, name: str | None, expected: bool
    ) -> None:
        """A callable ``enabled=`` on a named stream makes the set dynamic."""
        # Arrange
        app = _app()
        app.stream(name, enabled=lambda _s: True)(_consume)

        # Act
        dynamic = app._has_dynamic_entity_set()  # noqa: SLF001

        # Assert
        assert dynamic is expected
