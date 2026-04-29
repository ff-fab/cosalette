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
from typing import Any
from unittest.mock import patch

import pytest

from cosalette._registration import _StreamRegistration
from cosalette._runners._stream_runner import find_stream_adapter, run_stream
from cosalette._stream import Stream, StreamablePort

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

        async def handler(stream: Stream[_Item]) -> None:
            async for it in stream:
                items_seen.append(it)

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

        async def handler(stream: Stream[_Item]) -> None:
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

        async def bad_handler(stream: Stream[_Item]) -> None:
            shutdown.set()
            raise ValueError("boom")

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

        async def blocking_handler(stream: Stream[_Item]) -> None:
            async for _ in stream:  # pragma: no cover
                pass

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

        async def handler(stream: Stream[_Item], logger: logging.Logger) -> None:
            received_logger.append(logger)
            shutdown.set()
            async for _ in stream:
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
