"""Tests for cosalette App stream decorator and registration.

Covers: @app.stream registration, signature validation, adapter compatibility
checks, and deferred enabled= behavior.
"""

from __future__ import annotations

import pytest

from cosalette._app import App
from cosalette._stream import Stream, StreamablePort

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

    def test_unpparameterized_stream_raises_type_error(self) -> None:
        """@app.stream raises TypeError when Stream parameter lacks type argument."""
        app = App(name="test-stream", version="1.0.0")
        app.adapter(StreamablePort[SensorReading], DummyStreamableAdapter)

        with pytest.raises(TypeError, match="must be parameterized: Stream\\[T\\]"):

            @app.stream("bad_stream")
            async def handle_unparam_stream(stream: Stream) -> None:  # type: ignore[type-arg]
                async for _ in stream:
                    pass

    def test_missing_compatible_adapter_raises_type_error(self) -> None:
        """@app.stream raises TypeError when no matching StreamablePort[T] adapter."""
        app = App(name="test-stream", version="1.0.0")
        # No adapter registered for SensorReading

        with pytest.raises(
            TypeError, match="No StreamablePort\\[SensorReading\\] adapter registered"
        ):

            @app.stream("sensor_stream")
            async def handle_sensor_stream(stream: Stream[SensorReading]) -> None:
                async for _ in stream:
                    pass

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
        assert callable(registration.enabled)

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
