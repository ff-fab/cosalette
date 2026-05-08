"""Tests for cosalette App stream decorator and registration.

Covers: @app.stream registration, signature validation, adapter compatibility
checks, and deferred enabled= behavior.

Test Techniques Used:
    - Specification-based Testing: Verifying @app.stream decorator contracts
      and registration semantics (name, func, metadata stored correctly).
    - Equivalence Partitioning: enabled= variants (True, False, callable).
    - Branch/Condition Coverage: All validation branches (missing Stream param,
      unparameterized Stream, missing adapter, multiple streams, double-declare).
    - Error Guessing: Anticipating TypeError for each invalid signature scenario.
"""

from __future__ import annotations

import pytest

from cosalette._app import App
from cosalette._stream import AsyncStreamablePort, Stream, StreamablePort
from cosalette._wiring import resolve_enabled
from cosalette.testing import make_settings

pytestmark = pytest.mark.unit


class SensorReading:
    """Test type for stream items."""

    def __init__(self, value: float) -> None:
        self.value = value


class DummyStreamableAdapter:
    """Mock adapter implementing StreamablePort[SensorReading]."""

    def __init__(self) -> None:
        self._callback = None

    def open(self) -> None:
        pass

    def close(self) -> None:
        pass

    def start_scan(self) -> None:
        pass

    def stop_scan(self) -> None:
        pass

    def register_callback(self, cb) -> None:
        self._callback = cb


class TestStreamRegistration:
    """Test @app.stream decorator registration and validation."""

    def test_successful_registration_with_matching_adapter(self) -> None:
        """@app.stream registers when Stream[T] and StreamablePort[T] adapter match."""
        app = App(name="test-stream", version="1.0.0")
        app.adapter(StreamablePort[SensorReading], DummyStreamableAdapter)

        @app.stream("sensor_stream")
        async def handle_sensor_stream(stream: Stream[SensorReading]) -> None:
            async for _ in stream:
                pass

        # Should not raise and should be registered
        assert len(app._streams) == 1
        registration = app._streams[0]
        assert registration.name == "sensor_stream"
        assert registration.func is handle_sensor_stream

    def test_missing_stream_parameter_raises_type_error(self) -> None:
        """@app.stream raises TypeError when function lacks Stream[T] parameter."""
        app = App(name="test-stream", version="1.0.0")
        app.adapter(StreamablePort[SensorReading], DummyStreamableAdapter)

        with pytest.raises(TypeError, match="must declare a Stream\\[T\\] parameter"):

            @app.stream("no_stream")
            async def handle_without_stream() -> None:
                pass

    def test_unparameterized_stream_raises_type_error(self) -> None:
        """@app.stream raises TypeError when Stream parameter lacks type argument."""
        app = App(name="test-stream", version="1.0.0")
        app.adapter(StreamablePort[SensorReading], DummyStreamableAdapter)

        with pytest.raises(TypeError, match="must be parameterized: Stream\\[T\\]"):

            @app.stream("bad_stream")
            async def handle_unparam_stream(stream: Stream) -> None:  # type: ignore[type-arg]
                async for _ in stream:
                    pass

    def test_missing_compatible_adapter_deferred_to_runtime(self) -> None:
        """@app.stream defers adapter availability check to runtime (cos-s2q.4)."""
        app = App(name="test-stream", version="1.0.0")
        # No adapter registered for SensorReading

        # Decorator should succeed now (adapter check is deferred)
        @app.stream("sensor_stream")
        async def handle_sensor_stream(stream: Stream[SensorReading]) -> None:
            async for _ in stream:
                pass

        # Registration should succeed
        assert len(app._streams) == 1
        assert app._streams[0].name == "sensor_stream"

    def test_adapter_registered_after_decorator_works(self) -> None:
        """@app.stream can be used before app.adapter() call (cos-s2q.4)."""
        app = App(name="test-stream", version="1.0.0")

        # Decorator first, adapter later
        @app.stream("sensor_stream")
        async def handle_sensor_stream(stream: Stream[SensorReading]) -> None:
            async for _ in stream:
                pass

        # Now register the adapter
        app.adapter(StreamablePort[SensorReading], DummyStreamableAdapter)

        # Should be registered
        assert len(app._streams) == 1
        assert app._streams[0].name == "sensor_stream"

    def test_enabled_false_skips_registration(self) -> None:
        """@app.stream with enabled=False should skip registration entirely."""
        app = App(name="test-stream", version="1.0.0")
        # No adapter needed since registration should be skipped

        @app.stream("disabled_stream", enabled=False)
        async def handle_disabled_stream(stream: Stream[SensorReading]) -> None:
            async for _ in stream:
                pass

        # Should not be registered
        assert len(app._streams) == 0

    def test_enabled_callable_defers_validation(self) -> None:
        """@app.stream with enabled=callable should defer all validation."""
        app = App(name="test-stream", version="1.0.0")
        # No adapter registered, but validation should be deferred

        @app.stream("deferred_stream", enabled=lambda settings: True)
        async def handle_deferred_stream(stream: Stream[SensorReading]) -> None:
            async for _ in stream:
                pass

        # Should be registered despite missing adapter (validation deferred)
        assert len(app._streams) == 1
        registration = app._streams[0]
        assert registration.name == "deferred_stream"
        assert callable(registration.enabled_spec)

    def test_multiple_stream_parameters_raises_type_error(self) -> None:
        """@app.stream should reject functions with multiple Stream[T] parameters."""
        app = App(name="test-stream", version="1.0.0")
        app.adapter(StreamablePort[SensorReading], DummyStreamableAdapter)

        with pytest.raises(TypeError, match="declares multiple Stream parameters"):

            @app.stream("multi_stream")
            async def handle_multi_stream(
                stream1: Stream[SensorReading],
                stream2: Stream[SensorReading],
            ) -> None:
                async for _ in stream1:
                    pass

    def test_metadata_fields_preserved(self) -> None:
        """@app.stream should preserve summary, behavior, and effects metadata."""
        app = App(name="test-stream", version="1.0.0")
        app.adapter(StreamablePort[SensorReading], DummyStreamableAdapter)

        @app.stream(
            "documented_stream",
            summary="Processes sensor readings",
            behavior=["reads from BLE", "publishes state"],
            effects=["updates device state"],
        )
        async def handle_documented_stream(stream: Stream[SensorReading]) -> None:
            async for _ in stream:
                pass

        registration = app._streams[0]
        assert registration.summary == "Processes sensor readings"
        assert registration.behavior == ["reads from BLE", "publishes state"]
        assert registration.effects == ["updates device state"]

    def test_root_stream_when_name_is_none(self) -> None:
        """@app.stream with name=None should use function name and mark as root."""
        app = App(name="test-stream", version="1.0.0")
        app.adapter(StreamablePort[SensorReading], DummyStreamableAdapter)

        @app.stream()  # name=None
        async def sensor_handler(stream: Stream[SensorReading]) -> None:
            async for _ in stream:
                pass

        registration = app._streams[0]
        assert registration.name == "sensor_handler"  # function name used
        assert registration.is_root is True

    def test_double_declare_port_and_stream_raises_type_error(self) -> None:
        """@app.stream rejects handlers declaring Stream[T] and StreamablePort[T]."""
        app = App(name="test-stream", version="1.0.0")
        app.adapter(StreamablePort[SensorReading], DummyStreamableAdapter)

        with pytest.raises(TypeError, match="declares both"):

            @app.stream("double_declare")
            async def handle_double(
                stream: Stream[SensorReading],
                port: StreamablePort[SensorReading],
            ) -> None:
                async for _ in stream:
                    pass


# ---------------------------------------------------------------------------
# TestStreamEnabledBootstrap
# ---------------------------------------------------------------------------


class TestStreamEnabledBootstrap:
    """Callable enabled= is evaluated at bootstrap (resolve_enabled phase)."""

    def test_callable_enabled_false_removes_at_bootstrap(self) -> None:
        """enabled=lambda s: False removes the stream registration at bootstrap.

        Technique: State Transition — callable spec evaluated once; entry removed.
        """
        app = App(name="test-stream", version="1.0.0")
        app.adapter(StreamablePort[SensorReading], DummyStreamableAdapter)

        @app.stream("skipped", enabled=lambda s: False)
        async def skipped(stream: Stream[SensorReading]) -> None:
            async for _ in stream:
                pass

        assert len(app._streams) == 1  # stored before resolution
        settings = make_settings()
        resolve_enabled(
            app._telemetry,
            app._devices,
            app._commands,
            settings,
            None,
            stream_list=app._streams,
        )
        assert len(app._streams) == 0

    def test_callable_enabled_true_retains_at_bootstrap(self) -> None:
        """enabled=lambda s: True keeps the stream registration after bootstrap.

        Technique: State Transition — callable spec evaluated; entry retained.
        """
        app = App(name="test-stream", version="1.0.0")
        app.adapter(StreamablePort[SensorReading], DummyStreamableAdapter)

        @app.stream("kept", enabled=lambda s: True)
        async def kept(stream: Stream[SensorReading]) -> None:
            async for _ in stream:
                pass

        settings = make_settings()
        resolve_enabled(
            app._telemetry,
            app._devices,
            app._commands,
            settings,
            None,
            stream_list=app._streams,
        )
        assert len(app._streams) == 1
        assert app._streams[0].name == "kept"

    def test_callable_enabled_receives_settings(self) -> None:
        """Callable enabled= receives the resolved Settings instance.

        Technique: Specification-based — verifying the callable argument.
        """
        app = App(name="test-stream", version="1.0.0")
        app.adapter(StreamablePort[SensorReading], DummyStreamableAdapter)
        captured: list[object] = []

        def _check_settings(s: object) -> bool:
            captured.append(s)
            return True

        @app.stream("probe", enabled=_check_settings)
        async def probe(stream: Stream[SensorReading]) -> None:
            async for _ in stream:
                pass

        settings = make_settings()
        resolve_enabled(
            app._telemetry,
            app._devices,
            app._commands,
            settings,
            None,
            stream_list=app._streams,
        )
        assert len(captured) == 1
        assert captured[0] is settings


# ---------------------------------------------------------------------------
# TestAsyncStreamablePort
# ---------------------------------------------------------------------------


class DummyAsyncStreamableAdapter:
    """Mock adapter implementing AsyncStreamablePort[SensorReading]."""

    def __init__(self) -> None:
        self._callback = None

    async def open(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def start_scan(self) -> None:
        pass

    async def stop_scan(self) -> None:
        pass

    def register_callback(self, cb) -> None:
        self._callback = cb


class TestAsyncStreamablePortRegistration:
    """Tests for @app.stream with AsyncStreamablePort adapters."""

    def test_async_adapter_registration_succeeds(self) -> None:
        """@app.stream registers when AsyncStreamablePort[T] adapter is present."""
        app = App(name="test-async-stream", version="1.0.0")
        app.adapter(AsyncStreamablePort[SensorReading], DummyAsyncStreamableAdapter)

        @app.stream("async_sensor")
        async def handle(stream: Stream[SensorReading]) -> None:
            async for _ in stream:
                pass

        assert len(app._streams) == 1
        assert app._streams[0].name == "async_sensor"

    def test_double_declare_async_port_raises(self) -> None:
        """@app.stream rejects handlers declaring Stream[T] and AsyncStreamablePort[T].

        Mirrors the sync StreamablePort guard.
        """
        app = App(name="test-async-stream", version="1.0.0")
        app.adapter(AsyncStreamablePort[SensorReading], DummyAsyncStreamableAdapter)

        with pytest.raises(TypeError, match="declares both"):

            @app.stream("double_async")
            async def handle(
                stream: Stream[SensorReading],
                port: AsyncStreamablePort[SensorReading],
            ) -> None:
                async for _ in stream:
                    pass

    def test_async_adapter_deferred_check_passes(self) -> None:
        """Adapter check deferred; async adapter registered after decorator."""
        # noqa: E501
        app = App(name="test-async-stream", version="1.0.0")

        @app.stream("deferred_async")
        async def handle(stream: Stream[SensorReading]) -> None:
            async for _ in stream:
                pass

        app.adapter(AsyncStreamablePort[SensorReading], DummyAsyncStreamableAdapter)
        assert len(app._streams) == 1


# ---------------------------------------------------------------------------
# TestBuildStreamContexts
# ---------------------------------------------------------------------------


class TestBuildStreamContexts:
    """build_stream_contexts: per-stream DeviceContext creation."""

    def test_builds_context_for_each_stream(self) -> None:
        """Returns one DeviceContext per unique stream name."""
        import asyncio
        from unittest.mock import MagicMock

        from cosalette._clock import ClockPort
        from cosalette._context import DeviceContext
        from cosalette._wiring import build_stream_contexts

        app = App(name="test-ctx", version="1.0.0")
        app.adapter(StreamablePort[SensorReading], DummyStreamableAdapter)

        @app.stream("stream_a")
        async def handler_a(stream: Stream[SensorReading]) -> None:
            async for _ in stream:
                pass

        @app.stream("stream_b")
        async def handler_b(stream: Stream[SensorReading]) -> None:
            async for _ in stream:
                pass

        settings = make_settings()
        mqtt = MagicMock()
        clock = MagicMock(spec=ClockPort)
        shutdown = asyncio.Event()

        ctxs = build_stream_contexts(
            app._streams,
            settings,
            mqtt,
            "myapp",
            shutdown,
            {},
            clock,
        )

        assert set(ctxs.keys()) == {"stream_a", "stream_b"}
        assert all(isinstance(c, DeviceContext) for c in ctxs.values())
        assert ctxs["stream_a"].name == "stream_a"
        assert ctxs["stream_b"].name == "stream_b"

    def test_context_topic_prefix_set(self) -> None:
        """DeviceContext topic_prefix matches the provided prefix."""
        import asyncio
        from unittest.mock import MagicMock

        from cosalette._clock import ClockPort
        from cosalette._wiring import build_stream_contexts

        app = App(name="test-ctx", version="1.0.0")
        app.adapter(StreamablePort[SensorReading], DummyStreamableAdapter)

        @app.stream("my_stream")
        async def handler(stream: Stream[SensorReading]) -> None:
            async for _ in stream:
                pass

        settings = make_settings()
        mqtt = MagicMock()
        clock = MagicMock(spec=ClockPort)
        shutdown = asyncio.Event()

        ctxs = build_stream_contexts(
            app._streams,
            settings,
            mqtt,
            "custom_prefix",
            shutdown,
            {},
            clock,
        )

        ctx = ctxs["my_stream"]
        assert ctx._topic_prefix == "custom_prefix"

    def test_empty_streams_returns_empty_dict(self) -> None:
        """No streams → empty contexts dict."""
        import asyncio
        from unittest.mock import MagicMock

        from cosalette._clock import ClockPort
        from cosalette._wiring import build_stream_contexts

        settings = make_settings()
        clock = MagicMock(spec=ClockPort)

        ctxs = build_stream_contexts(
            [],
            settings,
            MagicMock(),
            "prefix",
            asyncio.Event(),
            {},
            clock,
        )

        assert ctxs == {}
