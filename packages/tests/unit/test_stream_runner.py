"""Unit tests for cosalette._runners._stream_runner — stream adapter runner.

Covers: find_stream_adapter(), run_stream() lifecycle, cleanup on startup
failure, CancelledError propagation, exception isolation, and watcher task
cleanup.

Test Techniques Used:
    - Specification-based Testing: Verifying find_stream_adapter and
      run_stream contracts (happy path, error cases, lifecycle ordering).
    - State Transition Testing: Port lifecycle states (closed → open →
      scanning → stopped → closed) and watcher task lifecycle.
    - Error Guessing: Anticipating RuntimeError for missing adapter/plan,
      and verifying cleanup runs even when startup fails.
    - Branch/Condition Coverage: All exception paths in run_stream
      (CancelledError re-raise, generic exception isolation, startup failure).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import patch

import pytest

from cosalette._context import DeviceContext
from cosalette._persistence._stores import DeviceStore, MemoryStore
from cosalette._registration import _StreamRegistration
from cosalette._runners._stream_runner import find_stream_adapter, run_stream
from cosalette._stream import AsyncStreamablePort, Stream, StreamablePort
from cosalette.testing import FakeClock, MockMqttClient, make_settings

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers / test doubles
# ---------------------------------------------------------------------------


class _Item:
    """Minimal item type for stream tests."""


class _FakePort:
    """Minimal StreamablePort[_Item] fake tracking call order."""

    def __init__(self, *, open_raises: Exception | None = None) -> None:
        self.calls: list[str] = []
        self._callback: Any = None
        self._open_raises = open_raises

    def open(self) -> None:
        if self._open_raises:
            raise self._open_raises
        self.calls.append("open")

    def close(self) -> None:
        self.calls.append("close")

    def start_scan(self) -> None:
        self.calls.append("start_scan")

    def stop_scan(self) -> None:
        self.calls.append("stop_scan")

    def register_callback(self, cb: Any) -> None:
        self.calls.append("register_callback")
        self._callback = cb


def _make_reg(
    func: Any, injection_plan: list[tuple[str, Any]] | None = None
) -> _StreamRegistration:
    """Build a minimal _StreamRegistration for testing."""
    from cosalette._injection import build_injection_plan

    plan = injection_plan if injection_plan is not None else build_injection_plan(func)
    return _StreamRegistration(
        name="test_stream",
        func=func,
        injection_plan=plan,
        enabled_spec=True,
        summary=None,
        behavior=None,
        effects=None,
    )


# ---------------------------------------------------------------------------
# TestFindStreamAdapter
# ---------------------------------------------------------------------------


class TestFindStreamAdapter:
    """find_stream_adapter: resolves StreamablePort[T] from resolved_adapters."""

    def test_returns_matching_adapter(self) -> None:
        """Returns (item_type, adapter, is_async) for a matching StreamablePort[T]."""
        port_instance = _FakePort()
        resolved: dict[type, object] = {StreamablePort[_Item]: port_instance}

        async def handler(stream: Stream[_Item]) -> None:
            pass

        reg = _make_reg(handler)
        item_type, adapter, is_async = find_stream_adapter(reg, resolved)

        assert item_type is _Item
        assert adapter is port_instance
        assert is_async is False

    def test_raises_when_no_matching_port(self) -> None:
        """RuntimeError when plan has Stream[T] but no StreamablePort[T] in adapters."""

        class _OtherItem:
            pass

        port_instance = _FakePort()
        resolved: dict[type, object] = {StreamablePort[_OtherItem]: port_instance}

        async def handler(stream: Stream[_Item]) -> None:
            pass

        reg = _make_reg(handler)
        with pytest.raises(
            RuntimeError,
            match=(
                r"Stream 'test_stream' requires StreamablePort\[_Item\] "
                r"or AsyncStreamablePort\[_Item\] "
                r"but no matching adapter was registered"
            ),
        ):
            find_stream_adapter(reg, resolved)

    def test_raises_when_no_stream_in_plan(self) -> None:
        """RuntimeError when injection_plan contains no Stream[T] annotation."""

        async def handler() -> None:
            pass

        reg = _make_reg(handler, injection_plan=[])
        with pytest.raises(RuntimeError, match="no Stream\\[T\\] found"):
            find_stream_adapter(reg, {})

    def test_error_message_safe_for_non_name_types(self) -> None:
        """Error message does not crash when item_type has no __name__ attribute."""
        from typing import Any as AnyT

        # Build a plan that looks like Stream[Any] (Any has no __name__)
        # We construct the generic alias directly to bypass annotation resolution
        stream_annotation = Stream[AnyT]  # type: ignore[type-arg]
        plan: list[tuple[str, Any]] = [("stream", stream_annotation)]

        async def handler() -> None:
            pass

        reg = _make_reg(handler, injection_plan=plan)
        with pytest.raises(RuntimeError):
            find_stream_adapter(reg, {})


# ---------------------------------------------------------------------------
# TestRunStream
# ---------------------------------------------------------------------------


class TestRunStream:
    """run_stream: full port lifecycle, handler invocation, and cleanup."""

    async def test_happy_path_lifecycle_order(self) -> None:
        """Port lifecycle: open → register_callback → start_scan → stop_scan → close."""
        port = _FakePort()
        resolved: dict[type, object] = {StreamablePort[_Item]: port}

        shutdown = asyncio.Event()
        items_seen: list[_Item] = []

        item = _Item()

        async def handler(stream: Stream[_Item]) -> AsyncIterator[None]:
            async for it in stream:
                items_seen.append(it)
                yield

        reg = _make_reg(handler)
        providers: dict[type, Any] = {}

        async def _drive() -> None:
            # Yield twice so run_stream can open port and start awaiting
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert port._callback is not None
            port._callback(item)
            # Give handler time to consume the item
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            shutdown.set()

        driver = asyncio.create_task(_drive())
        await run_stream(reg, resolved, providers, shutdown)
        await driver

        assert items_seen == [item]
        assert "open" in port.calls
        assert "register_callback" in port.calls
        assert "start_scan" in port.calls
        assert "stop_scan" in port.calls
        assert "close" in port.calls
        # open must precede stop_scan
        assert port.calls.index("open") < port.calls.index("stop_scan")

    async def test_cleanup_runs_when_port_open_raises(self) -> None:
        """stop_scan and close are called in finally even when port.open() raises.

        run_stream catches generic exceptions (exception isolation), so it
        returns normally. The finally block still runs, calling stop_scan/close.
        """
        exc = RuntimeError("open failed")
        port = _FakePort(open_raises=exc)
        resolved: dict[type, object] = {StreamablePort[_Item]: port}
        shutdown = asyncio.Event()

        async def handler(stream: Stream[_Item]) -> AsyncIterator[None]:
            yield
            pass  # pragma: no cover

        reg = _make_reg(handler)
        # Exception is caught and logged; run_stream returns normally
        await run_stream(reg, resolved, {}, shutdown)

        # Cleanup always runs via finally
        assert "stop_scan" in port.calls
        assert "close" in port.calls

    async def test_exception_in_handler_is_isolated(self) -> None:
        """Handler exceptions are logged; run_stream returns normally."""
        port = _FakePort()
        resolved: dict[type, object] = {StreamablePort[_Item]: port}
        shutdown = asyncio.Event()

        async def bad_handler(stream: Stream[_Item]) -> AsyncIterator[None]:
            shutdown.set()
            raise ValueError("boom")
            yield  # noqa: PGH004

        reg = _make_reg(bad_handler)

        with patch.object(
            logging.getLogger("cosalette._runners._stream_runner"), "exception"
        ) as mock_log:
            await run_stream(reg, resolved, {}, shutdown)

        mock_log.assert_called_once()

    async def test_cancelled_error_propagates(self) -> None:
        """CancelledError is re-raised, not swallowed."""
        port = _FakePort()
        resolved: dict[type, object] = {StreamablePort[_Item]: port}
        shutdown = asyncio.Event()

        async def blocking_handler(stream: Stream[_Item]) -> AsyncIterator[None]:
            async for _ in stream:
                yield
                pass  # pragma: no cover

        reg = _make_reg(blocking_handler)

        task = asyncio.create_task(run_stream(reg, resolved, {}, shutdown))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # Cleanup should have run
        assert "stop_scan" in port.calls
        assert "close" in port.calls

    async def test_logger_injectable_via_providers(self) -> None:
        """Handler declaring logging.Logger receives it from providers."""
        port = _FakePort()
        resolved: dict[type, object] = {StreamablePort[_Item]: port}
        shutdown = asyncio.Event()
        received_logger: list[logging.Logger] = []
        test_logger = logging.getLogger("test.stream.logger")

        async def handler(
            stream: Stream[_Item], logger: logging.Logger
        ) -> AsyncIterator[None]:
            received_logger.append(logger)
            shutdown.set()
            yield
            async for _ in stream:
                yield
                pass

        reg = _make_reg(handler)
        providers: dict[type, Any] = {logging.Logger: test_logger}

        async def _drive() -> None:
            await asyncio.sleep(0)
            assert port._callback is not None
            shutdown.set()

        driver = asyncio.create_task(_drive())
        await run_stream(reg, resolved, providers, shutdown)
        await driver

        assert received_logger == [test_logger]


# ---------------------------------------------------------------------------
# TestFindStreamAdapterAsync
# ---------------------------------------------------------------------------


class TestFindStreamAdapterAsync:
    """find_stream_adapter: async port detection and ambiguity errors."""

    def test_returns_async_adapter_with_is_async_true(self) -> None:
        """Returns (item_type, adapter, True) for AsyncStreamablePort[T]."""

        class _AsyncPort:
            async def open(self) -> None: ...
            async def close(self) -> None: ...
            async def start_scan(self) -> None: ...
            async def stop_scan(self) -> None: ...
            def register_callback(self, cb: Any) -> None: ...

        port_instance = _AsyncPort()
        resolved: dict[type, object] = {AsyncStreamablePort[_Item]: port_instance}

        async def handler(stream: Stream[_Item]) -> None:
            pass

        reg = _make_reg(handler)
        item_type, adapter, is_async = find_stream_adapter(reg, resolved)

        assert item_type is _Item
        assert adapter is port_instance
        assert is_async is True

    def test_ambiguous_adapters_raise_runtime_error(self) -> None:
        """RuntimeError when both sync and async port adapters registered.

        Ambiguity: same item type, different port protocols.
        """

        class _AsyncPort:
            async def open(self) -> None: ...
            async def close(self) -> None: ...
            async def start_scan(self) -> None: ...
            async def stop_scan(self) -> None: ...
            def register_callback(self, cb: Any) -> None: ...

        resolved: dict[type, object] = {
            StreamablePort[_Item]: _FakePort(),
            AsyncStreamablePort[_Item]: _AsyncPort(),
        }

        async def handler(stream: Stream[_Item]) -> None:
            pass

        reg = _make_reg(handler)
        with pytest.raises(
            RuntimeError,
            match=r"Ambiguous stream adapter for item type '_Item'",
        ):
            find_stream_adapter(reg, resolved)


# ---------------------------------------------------------------------------
# TestRunStreamAsync
# ---------------------------------------------------------------------------


class _AsyncFakePort:
    """Async StreamablePort fake tracking call order."""

    def __init__(self, *, open_raises: Exception | None = None) -> None:
        self.calls: list[str] = []
        self._callback: Any = None
        self._open_raises = open_raises

    async def open(self) -> None:
        if self._open_raises:
            raise self._open_raises
        self.calls.append("open")

    async def close(self) -> None:
        self.calls.append("close")

    async def start_scan(self) -> None:
        self.calls.append("start_scan")

    async def stop_scan(self) -> None:
        self.calls.append("stop_scan")

    def register_callback(self, cb: Any) -> None:
        self.calls.append("register_callback")
        self._callback = cb


class TestRunStreamAsync:
    """run_stream: async port lifecycle, cleanup, and cancellation."""

    async def test_async_lifecycle_order(self) -> None:
        """Async port: open → register_callback → start_scan → stop_scan → close."""
        port = _AsyncFakePort()
        resolved: dict[type, object] = {AsyncStreamablePort[_Item]: port}
        shutdown = asyncio.Event()
        items_seen: list[_Item] = []
        item = _Item()

        async def handler(stream: Stream[_Item]) -> AsyncIterator[None]:
            async for it in stream:
                items_seen.append(it)
                yield

        reg = _make_reg(handler)

        async def _drive() -> None:
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert port._callback is not None
            port._callback(item)
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            shutdown.set()

        driver = asyncio.create_task(_drive())
        await run_stream(reg, resolved, {}, shutdown)
        await driver

        assert items_seen == [item]
        assert port.calls.index("open") < port.calls.index("start_scan")
        assert port.calls.index("start_scan") < port.calls.index("stop_scan")
        assert port.calls.index("stop_scan") < port.calls.index("close")

    async def test_async_cleanup_runs_when_open_raises(self) -> None:
        """Async stop_scan and close called in finally even when open() raises."""
        exc = RuntimeError("async open failed")
        port = _AsyncFakePort(open_raises=exc)
        resolved: dict[type, object] = {AsyncStreamablePort[_Item]: port}
        shutdown = asyncio.Event()

        async def handler(stream: Stream[_Item]) -> AsyncIterator[None]:
            yield  # pragma: no cover

        reg = _make_reg(handler)
        await run_stream(reg, resolved, {}, shutdown)

        assert "stop_scan" in port.calls
        assert "close" in port.calls

    async def test_async_cleanup_runs_on_handler_failure(self) -> None:
        """Async stop_scan and close called after handler raises."""
        port = _AsyncFakePort()
        resolved: dict[type, object] = {AsyncStreamablePort[_Item]: port}
        shutdown = asyncio.Event()

        async def bad_handler(stream: Stream[_Item]) -> AsyncIterator[None]:
            shutdown.set()
            raise ValueError("boom")
            yield  # noqa: PGH004

        reg = _make_reg(bad_handler)
        await run_stream(reg, resolved, {}, shutdown)

        assert "stop_scan" in port.calls
        assert "close" in port.calls

    async def test_async_cancelled_error_propagates(self) -> None:
        """CancelledError re-raised for async port; cleanup still runs."""
        port = _AsyncFakePort()
        resolved: dict[type, object] = {AsyncStreamablePort[_Item]: port}
        shutdown = asyncio.Event()

        async def blocking_handler(stream: Stream[_Item]) -> AsyncIterator[None]:
            async for _ in stream:
                yield  # pragma: no cover

        reg = _make_reg(blocking_handler)
        task = asyncio.create_task(run_stream(reg, resolved, {}, shutdown))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert "stop_scan" in port.calls
        assert "close" in port.calls


# ---------------------------------------------------------------------------
# TestRunStreamDeviceContextAndStore
# ---------------------------------------------------------------------------


class TestRunStreamDeviceContextAndStore:
    """run_stream: DeviceContext and DeviceStore injection."""

    async def test_device_context_injectable_via_providers(self) -> None:
        """Handler declaring DeviceContext receives it from stream_providers."""
        from unittest.mock import MagicMock

        port = _FakePort()
        resolved: dict[type, object] = {StreamablePort[_Item]: port}
        shutdown = asyncio.Event()
        received_ctx: list[object] = []

        fake_ctx = MagicMock(spec=DeviceContext)

        async def handler(
            stream: Stream[_Item], ctx: DeviceContext
        ) -> AsyncIterator[None]:
            received_ctx.append(ctx)
            shutdown.set()
            yield
            async for _ in stream:
                yield

        reg = _make_reg(handler)
        providers: dict[type, Any] = {DeviceContext: fake_ctx}

        async def _drive() -> None:
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            shutdown.set()

        driver = asyncio.create_task(_drive())
        await run_stream(reg, resolved, providers, shutdown)
        await driver

        assert received_ctx == [fake_ctx]

    async def test_device_store_loaded_before_handler_and_injectable(self) -> None:
        """When store is configured, DeviceStore is loaded and injected into handler."""
        port = _FakePort()
        resolved: dict[type, object] = {StreamablePort[_Item]: port}
        shutdown = asyncio.Event()
        mem_store = MemoryStore({"test_stream": {"count": 42}})
        received_store: list[DeviceStore] = []

        async def handler(
            stream: Stream[_Item], device_store: DeviceStore
        ) -> AsyncIterator[None]:
            received_store.append(device_store)
            shutdown.set()
            yield
            async for _ in stream:
                yield

        reg = _make_reg(handler)

        async def _drive() -> None:
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            shutdown.set()

        driver = asyncio.create_task(_drive())
        await run_stream(reg, resolved, {}, shutdown, store=mem_store)
        await driver

        assert len(received_store) == 1
        assert received_store[0]["count"] == 42

    async def test_device_store_mutated_state_saved_on_shutdown(self) -> None:
        """State mutated in handler is persisted to store on stream shutdown."""
        port = _FakePort()
        resolved: dict[type, object] = {StreamablePort[_Item]: port}
        shutdown = asyncio.Event()
        mem_store = MemoryStore()

        async def handler(
            stream: Stream[_Item], device_store: DeviceStore
        ) -> AsyncIterator[None]:
            device_store["status"] = "running"
            shutdown.set()
            yield
            async for _ in stream:
                yield

        reg = _make_reg(handler)

        async def _drive() -> None:
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            shutdown.set()

        driver = asyncio.create_task(_drive())
        await run_stream(reg, resolved, {}, shutdown, store=mem_store)
        await driver

        saved = mem_store.load("test_stream")
        assert saved is not None
        assert saved["status"] == "running"

    async def test_store_saved_on_handler_exit_via_exception(self) -> None:
        """Store is saved in finally even when the handler raises an exception."""
        port = _FakePort()
        resolved: dict[type, object] = {StreamablePort[_Item]: port}
        shutdown = asyncio.Event()
        mem_store = MemoryStore()

        async def bad_handler(
            stream: Stream[_Item], device_store: DeviceStore
        ) -> AsyncIterator[None]:
            device_store["written"] = True
            shutdown.set()
            raise RuntimeError("handler boom")
            yield  # noqa: PGH004

        reg = _make_reg(bad_handler)
        await run_stream(reg, resolved, {}, shutdown, store=mem_store)

        saved = mem_store.load("test_stream")
        assert saved is not None
        assert saved["written"] is True

    async def test_no_store_and_handler_asks_for_device_store_raises_type_error(
        self,
    ) -> None:
        """TypeError: handler requests DeviceStore but no store configured."""
        port = _FakePort()
        resolved: dict[type, object] = {StreamablePort[_Item]: port}
        shutdown = asyncio.Event()

        async def handler(
            stream: Stream[_Item], device_store: DeviceStore
        ) -> AsyncIterator[None]:
            yield  # pragma: no cover

        reg = _make_reg(handler)

        # run_stream isolates handler exceptions; capture via patched logger
        with patch.object(
            logging.getLogger("cosalette._runners._stream_runner"), "exception"
        ) as mock_log:
            await run_stream(reg, resolved, {}, shutdown, store=None)

        # The exception should have been logged (handler failed during kwargs resolve)
        mock_log.assert_called_once()
        exc_arg = mock_log.call_args[0]
        assert "test_stream" in str(exc_arg)


# ---------------------------------------------------------------------------
# TestRunStreamDeviceContextPublish
# ---------------------------------------------------------------------------


def _make_reg_named(
    func: Any,
    *,
    name: str = "test_stream",
    is_root: bool = False,
) -> _StreamRegistration:
    """Build a _StreamRegistration with explicit name and is_root flag."""
    from cosalette._injection import build_injection_plan

    plan = build_injection_plan(func)
    return _StreamRegistration(
        name=name,
        func=func,
        injection_plan=plan,
        enabled_spec=True,
        is_root=is_root,
        summary=None,
        behavior=None,
        effects=None,
    )


class TestRunStreamDeviceContextPublish:
    """run_stream: DeviceContext MQTT publish semantics via real context."""

    def _make_ctx(
        self,
        mqtt: MockMqttClient,
        name: str = "my_stream",
        *,
        is_root: bool = False,
    ) -> DeviceContext:
        return DeviceContext(
            name=name,
            settings=make_settings(),
            mqtt=mqtt,
            topic_prefix="myapp",
            shutdown_event=asyncio.Event(),
            adapters={},
            clock=FakeClock(),
            is_root=is_root,
        )

    async def test_named_stream_publishes_to_device_segment_topic(self) -> None:
        """Named stream handler publishes to {prefix}/{name}/state."""
        mqtt = MockMqttClient()
        port = _FakePort()
        resolved: dict[type, object] = {StreamablePort[_Item]: port}
        shutdown = asyncio.Event()

        ctx = self._make_ctx(mqtt, name="my_stream", is_root=False)

        async def handler(
            stream: Stream[_Item], device_ctx: DeviceContext
        ) -> AsyncIterator[None]:
            await device_ctx.publish_state({"value": 1})
            shutdown.set()
            yield
            async for _ in stream:
                yield  # pragma: no cover

        reg = _make_reg_named(handler, name="my_stream", is_root=False)

        async def _drive() -> None:
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            shutdown.set()

        driver = asyncio.create_task(_drive())
        await run_stream(reg, resolved, {DeviceContext: ctx}, shutdown)
        await driver

        assert len(mqtt.published) == 1
        topic, _payload, _retain, _qos = mqtt.published[0]
        assert topic == "myapp/my_stream/state"

    async def test_root_stream_publishes_to_prefix_state_topic(self) -> None:
        """Root stream (is_root=True) publishes to {prefix}/state, no device segment."""
        mqtt = MockMqttClient()
        port = _FakePort()
        resolved: dict[type, object] = {StreamablePort[_Item]: port}
        shutdown = asyncio.Event()

        # is_root=True — topic omits the device name segment
        ctx = self._make_ctx(mqtt, name="sensor_handler", is_root=True)

        async def handler(
            stream: Stream[_Item], device_ctx: DeviceContext
        ) -> AsyncIterator[None]:
            await device_ctx.publish_state({"value": 2})
            shutdown.set()
            yield
            async for _ in stream:
                yield  # pragma: no cover

        reg = _make_reg_named(handler, name="sensor_handler", is_root=True)

        async def _drive() -> None:
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            shutdown.set()

        driver = asyncio.create_task(_drive())
        await run_stream(reg, resolved, {DeviceContext: ctx}, shutdown)
        await driver

        assert len(mqtt.published) == 1
        topic, _payload, _retain, _qos = mqtt.published[0]
        # Root stream: no device segment in topic
        assert topic == "myapp/state"
