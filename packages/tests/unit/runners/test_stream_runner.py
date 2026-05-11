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
from cosalette._runners._stream_runner import (
    _StreamHandlerProxy,
    find_stream_adapter,
    run_stream,
)
from cosalette._runners._stream_types import Stream, StreamablePort
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
        """Returns (item_type, adapter) for a matching StreamablePort[T]."""
        port_instance = _FakePort()
        resolved: dict[type, object] = {StreamablePort[_Item]: port_instance}

        async def handler(stream: Stream[_Item]) -> None:
            pass

        reg = _make_reg(handler)
        item_type, adapter = find_stream_adapter(reg, resolved)

        assert item_type is _Item
        assert adapter is port_instance

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
            match=r"Stream 'test_stream' requires StreamablePort\[_Item\]"
            r" but no matching adapter",
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
        # Full canonical order:
        # open → register_callback → start_scan → stop_scan → close
        assert port.calls.index("open") < port.calls.index("register_callback")
        assert port.calls.index("register_callback") < port.calls.index("start_scan")
        assert port.calls.index("start_scan") < port.calls.index("stop_scan")
        assert port.calls.index("stop_scan") < port.calls.index("close")

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


# ---------------------------------------------------------------------------
# TestConcreteAdapterInjection
# ---------------------------------------------------------------------------


class _ExtendedFakePort:
    """StreamablePort[_Item] fake with an extra non-lifecycle method."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self._callback: Any = None
        self.battery_level: int = 99

    async def open(self) -> None:
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

    def get_battery_level(self) -> int:
        """Non-lifecycle operation: concrete adapter capability."""
        return self.battery_level


class TestConcreteAdapterInjection:
    """Concrete adapter type is injectable for non-lifecycle operations.

    ADR-045: the framework owns stream-source lifecycle (open, start_scan,
    stop_scan, close).  Handlers may inject the concrete adapter class to
    call non-lifecycle methods.  StreamablePort[T]
    direct injection is separately guarded at registration time.
    """

    async def test_concrete_adapter_injectable_for_non_lifecycle(self) -> None:
        """Handler injecting concrete adapter class receives the real instance.

        The handler calls a non-lifecycle method and the returned value
        proves it received the same instance used by the framework for
        stream lifecycle management (async port lifecycle).
        """
        port = _ExtendedFakePort()
        port.battery_level = 77
        resolved: dict[type, object] = {StreamablePort[_Item]: port}
        shutdown = asyncio.Event()
        received_levels: list[int] = []

        async def handler(
            stream: Stream[_Item],
            adapter: _ExtendedFakePort,  # concrete type, not StreamablePort[T]
        ) -> AsyncIterator[None]:
            # Non-lifecycle call — framework still owns open/start_scan/etc.
            received_levels.append(adapter.get_battery_level())
            shutdown.set()
            yield
            async for _ in stream:
                yield  # pragma: no cover

        reg = _make_reg(handler)

        async def _drive() -> None:
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            shutdown.set()

        driver = asyncio.create_task(_drive())
        await run_stream(reg, resolved, {}, shutdown)
        await driver

        # Concrete adapter was injected and the non-lifecycle method was called.
        assert received_levels == [77]
        # Framework still owned lifecycle.
        assert "open" in port.calls
        assert "start_scan" in port.calls
        assert "stop_scan" in port.calls
        assert "close" in port.calls

    async def test_concrete_adapter_injected_as_proxy(self) -> None:
        """Injected concrete adapter is a capability-limited proxy, not the raw port.

        ADR-045: the framework wraps the concrete adapter in a proxy before
        injecting it so that handlers cannot accidentally call lifecycle
        methods (open/close/start_scan/stop_scan).  Non-lifecycle methods
        are forwarded transparently through the proxy.
        """
        port = _ExtendedFakePort()
        port.battery_level = 55
        resolved: dict[type, object] = {StreamablePort[_Item]: port}
        shutdown = asyncio.Event()
        injected_instances: list[Any] = []
        battery_readings: list[int] = []

        async def handler(
            stream: Stream[_Item],
            adapter: _ExtendedFakePort,
        ) -> AsyncIterator[None]:
            injected_instances.append(adapter)
            # Non-lifecycle call forwarded through proxy
            battery_readings.append(adapter.get_battery_level())
            shutdown.set()
            yield
            async for _ in stream:
                yield  # pragma: no cover

        reg = _make_reg(handler)

        async def _drive() -> None:
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            shutdown.set()

        driver = asyncio.create_task(_drive())
        await run_stream(reg, resolved, {}, shutdown)
        await driver

        assert len(injected_instances) == 1
        # Proxy is injected, not the raw port
        assert isinstance(injected_instances[0], _StreamHandlerProxy)
        assert injected_instances[0] is not port
        # Non-lifecycle method was forwarded correctly through the proxy
        assert battery_readings == [55]
        # Framework still owned lifecycle on the raw port
        assert "open" in port.calls
        assert "start_scan" in port.calls
        assert "stop_scan" in port.calls
        assert "close" in port.calls


# ---------------------------------------------------------------------------
# TestStreamHandlerProxy
# ---------------------------------------------------------------------------


class TestStreamHandlerProxy:
    """_StreamHandlerProxy enforces lifecycle method restrictions.

    ADR-045: stream handlers must not call open/close/start_scan/stop_scan
    on the injected adapter.  The proxy enforces this at runtime by raising
    AttributeError for any of those four method names.

    Technique: Specification-based — one test per blocked method plus
    forwarding and repr coverage.
    """

    def test_non_lifecycle_attribute_is_forwarded(self) -> None:
        """Attribute access for non-lifecycle names is forwarded to the adapter."""
        port = _ExtendedFakePort()
        port.battery_level = 42
        proxy = _StreamHandlerProxy(port)

        assert proxy.get_battery_level() == 42

    def test_attribute_forwarding_reflects_adapter_state(self) -> None:
        """Proxy reads reflect live state from the underlying adapter."""
        port = _ExtendedFakePort()
        port.battery_level = 10
        proxy = _StreamHandlerProxy(port)

        port.battery_level = 99  # mutate after proxy creation
        assert proxy.battery_level == 99

    @pytest.mark.parametrize("method", ["open", "close", "start_scan", "stop_scan"])
    def test_lifecycle_method_raises_attribute_error_with_adr_message(
        self, method: str
    ) -> None:
        """Each lifecycle method raises AttributeError citing the method and ADR-045.

        Technique: Specification-based Testing — verifies both the error type and
        the diagnostic message in one parametrized case per blocked method name,
        eliminating the duplication that previously triggered CI similarity failures.
        """
        port = _ExtendedFakePort()
        proxy = _StreamHandlerProxy(port)

        with pytest.raises(AttributeError) as exc_info:
            getattr(proxy, method)
        msg = str(exc_info.value)
        assert method in msg
        assert "ADR-045" in msg

    def test_direct_adapter_access_is_rejected(self) -> None:
        """proxy._adapter raises AttributeError, preventing internal bypass.

        Technique: Specification-based Testing — security boundary: callers
        must not reach the raw adapter via the slot attribute name.
        """
        proxy = _StreamHandlerProxy(_ExtendedFakePort())

        with pytest.raises(AttributeError):
            _ = proxy._adapter  # type: ignore[attr-defined]

    def test_adapter_with_own_adapter_attr_does_not_leak_via_getattr(self) -> None:
        """Wrapped adapter that defines _adapter does not leak it through __getattr__.

        Regression: __getattribute__ raises AttributeError for '_adapter', so Python
        falls through to __getattr__. Without an explicit guard in __getattr__, the
        proxy would forward to the underlying adapter's own _adapter attribute.

        Technique: Error Guessing — defensive check that the __getattr__ guard fires
        even when the underlying adapter defines _adapter itself.
        """

        class _AdapterWithInternalRef(_ExtendedFakePort):
            _adapter = "leaked-secret"  # noqa: PIE798

        proxy = _StreamHandlerProxy(_AdapterWithInternalRef())

        with pytest.raises(AttributeError):
            _ = proxy._adapter  # type: ignore[attr-defined]

    def test_nonexistent_attribute_raises_attribute_error(self) -> None:
        """Accessing a missing attribute on the adapter raises AttributeError.

        Technique: Error Guessing — proxy forwarding must not swallow missing-attr
        errors.
        """
        proxy = _StreamHandlerProxy(_ExtendedFakePort())

        with pytest.raises(AttributeError):
            _ = proxy.no_such_method  # type: ignore[attr-defined]

    def test_repr_does_not_expose_raw_adapter_repr(self) -> None:
        """repr() shows the adapter class name only, not the raw adapter repr.

        Technique: Specification-based — repr leakage reduction; the proxy
        must identify the wrapper and the wrapped type without surfacing
        the adapter's own repr (which may expose sensitive state).
        """
        port = _ExtendedFakePort()
        proxy = _StreamHandlerProxy(port)

        result = repr(proxy)
        assert "_StreamHandlerProxy" in result
        assert "_ExtendedFakePort" in result
        # Raw adapter repr must NOT appear
        assert repr(port) not in result


# ---------------------------------------------------------------------------
# TestFalseyAdapterRegression
# ---------------------------------------------------------------------------


class _FalseyFakePort(_FakePort):
    """StreamablePort[_Item] fake that evaluates to False via __bool__.

    Regression guard: adapters that are falsey (e.g. wrappers over empty
    collections or objects with __bool__ overrides) must still be found by
    _find_port_entry.  The old ``match = match or found`` idiom silently
    dropped these.
    """

    def __bool__(self) -> bool:
        return False


class TestFalseyAdapterRegression:
    """Regression: falsey adapter objects are still resolved by None-checks.

    The old ``sync_match = sync_match or s`` idiom would discard any adapter
    whose ``__bool__`` returns False (e.g. a port wrapping an empty buffer).
    These tests guard the fix: explicit ``if s is not None`` checks.
    """

    def test_falsey_adapter_is_still_found(self) -> None:
        """A falsey StreamablePort[T] is resolved even when bool(adapter) is False."""
        port = _FalseyFakePort()
        assert not port  # fixture is truly falsey

        resolved: dict[type, object] = {StreamablePort[_Item]: port}

        async def handler(stream: Stream[_Item]) -> None:
            pass

        reg = _make_reg(handler)
        item_type, adapter = find_stream_adapter(reg, resolved)

        assert item_type is _Item
        assert adapter is port

    def test_falsey_sync_adapter_plus_non_matching_entry_is_still_found(self) -> None:
        """Falsey sync match survives iteration past a non-matching port entry.

        With the buggy ``or`` idiom, the falsey first match was cleared when
        the loop continued to a second (non-matching) entry that returned
        ``(None, None)`` — leaving sync_match as None and raising "not found".
        """

        class _OtherItem:
            pass

        falsey_port = _FalseyFakePort()
        other_port = _FakePort()
        resolved: dict[type, object] = {
            # The matching falsey port comes first
            StreamablePort[_Item]: falsey_port,
            # A second entry for a different type — returns (None, None)
            StreamablePort[_OtherItem]: other_port,
        }

        async def handler(stream: Stream[_Item]) -> None:
            pass

        reg = _make_reg(handler)
        item_type, adapter = find_stream_adapter(reg, resolved)

        assert item_type is _Item
        assert adapter is falsey_port
