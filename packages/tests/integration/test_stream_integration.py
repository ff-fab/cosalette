"""Integration tests — stream proxy lifecycle ownership.

Validates ADR-045 contracts: the framework owns the port lifecycle
(open/register_callback/start_scan/stop_scan/close) while the handler
receives a StreamHandlerProxy that blocks direct lifecycle access.

See Also:
    ADR-045 — Stateful stream receiver semantics.
    ADR-007 — Testing strategy (integration layer).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from cosalette._runners._stream_types import Stream, StreamablePort
from cosalette.testing import AppHarness, StreamHandlerProxy

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _StreamItem:
    """Minimal item type for stream lifecycle integration tests."""


class _LifecycleTrackingPort:
    """Fake StreamablePort that records all lifecycle calls in order."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self._callback: object = None
        self.scan_started: asyncio.Event = asyncio.Event()

    async def open(self) -> None:
        self.calls.append("open")

    async def close(self) -> None:
        self.calls.append("close")

    async def start_scan(self) -> None:
        self.calls.append("start_scan")
        self.scan_started.set()

    async def stop_scan(self) -> None:
        self.calls.append("stop_scan")

    def register_callback(self, cb: object) -> None:
        self.calls.append("register_callback")
        self._callback = cb


# ---------------------------------------------------------------------------
# TestStreamProxyLifecycleOwnership
# ---------------------------------------------------------------------------


class TestStreamProxyLifecycleOwnership:
    """Stream receiver: framework owns lifecycle; handler receives proxy.

    Validates ADR-045 contracts:
    - open/register_callback/start_scan/stop_scan/close are called by the
      framework (run_stream), not the handler.
    - The handler receives a _StreamHandlerProxy, not the raw adapter.
    - The proxy blocks access to lifecycle methods (open, close, start_scan,
      stop_scan) while forwarding non-lifecycle attribute access.

    Technique:
        - Protocol Conformance: _StreamHandlerProxy enforces ADR-045 contract.
        - State Transition Testing: port lifecycle state sequence verified.
        - Integration Testing: run_stream wires the full lifecycle end-to-end.

    See Also:
        ADR-045 — Stateful stream receiver semantics.
    """

    async def test_framework_calls_lifecycle_in_canonical_order(self) -> None:
        """run_stream calls open → register_callback → start_scan → stop_scan → close.

        Technique: State Transition Testing — canonical lifecycle order.
        """
        harness = AppHarness.create()
        port = _LifecycleTrackingPort()
        resolved: dict[type, object] = {StreamablePort[_StreamItem]: port}
        shutdown = asyncio.Event()

        async def handler(stream: Stream[_StreamItem]) -> AsyncIterator[None]:
            async for _ in stream:
                yield

        async def _drive() -> None:
            # Wait until start_scan fires — deterministic, no tick-counting.
            await port.scan_started.wait()
            shutdown.set()

        driver = asyncio.create_task(_drive())
        try:
            await harness.run_stream(handler, resolved, shutdown=shutdown)
        finally:
            driver.cancel()
            await asyncio.gather(driver, return_exceptions=True)

        assert "open" in port.calls
        assert "register_callback" in port.calls
        assert "start_scan" in port.calls
        assert "stop_scan" in port.calls
        assert "close" in port.calls

        # Canonical ordering
        idx = port.calls.index
        assert idx("open") < idx("register_callback")
        assert idx("register_callback") < idx("start_scan")
        assert idx("start_scan") < idx("stop_scan")
        assert idx("stop_scan") < idx("close")

    async def test_handler_receives_proxy_not_raw_port(self) -> None:
        """Handler injecting the concrete port type receives a StreamHandlerProxy.

        Technique: Protocol Conformance — proxy type assertion.
        """
        harness = AppHarness.create()
        port = _LifecycleTrackingPort()
        resolved: dict[type, object] = {StreamablePort[_StreamItem]: port}
        shutdown = asyncio.Event()
        received: list[object] = []

        async def handler(
            stream: Stream[_StreamItem],
            p: _LifecycleTrackingPort,
        ) -> AsyncIterator[None]:
            received.append(p)
            shutdown.set()
            async for _ in stream:
                yield

        await harness.run_stream(handler, resolved, shutdown=shutdown)

        assert len(received) == 1
        assert isinstance(received[0], StreamHandlerProxy), (
            f"Expected StreamHandlerProxy, got {type(received[0])}"
        )

    async def test_proxy_blocks_lifecycle_method_open(self) -> None:
        """Accessing proxy.open raises AttributeError (lifecycle method blocked).

        Technique: Protocol Conformance — ADR-045 lifecycle method guard.
        """
        port = _LifecycleTrackingPort()
        proxy = StreamHandlerProxy(port)

        with pytest.raises(AttributeError, match="lifecycle method"):
            _ = proxy.open

    async def test_proxy_blocks_lifecycle_method_close(self) -> None:
        """Accessing proxy.close raises AttributeError (lifecycle method blocked).

        Technique: Protocol Conformance — ADR-045 lifecycle method guard.
        """
        port = _LifecycleTrackingPort()
        proxy = StreamHandlerProxy(port)

        with pytest.raises(AttributeError, match="lifecycle method"):
            _ = proxy.close

    async def test_proxy_blocks_start_scan_and_stop_scan(self) -> None:
        """Accessing proxy.start_scan and proxy.stop_scan raise AttributeError.

        Technique: Protocol Conformance — ADR-045 lifecycle method guard.
        """
        port = _LifecycleTrackingPort()
        proxy = StreamHandlerProxy(port)

        with pytest.raises(AttributeError, match="lifecycle method"):
            _ = proxy.start_scan

        with pytest.raises(AttributeError, match="lifecycle method"):
            _ = proxy.stop_scan

    def test_proxy_allows_non_lifecycle_attribute_access(self) -> None:
        """Proxy forwards non-lifecycle attributes to the underlying adapter.

        Technique: Protocol Conformance — ADR-045 non-lifecycle forwarding.
        """

        class _ExtendedPort(_LifecycleTrackingPort):
            """Port with an extra non-lifecycle method for testing."""

            def get_status(self) -> str:
                return "ready"

        port = _ExtendedPort()
        proxy = StreamHandlerProxy(port)

        # Non-lifecycle method should be accessible
        assert proxy.get_status() == "ready"  # type: ignore[attr-defined]

    async def test_framework_closes_port_when_handler_raises(self) -> None:
        """Port lifecycle (stop_scan, close) runs even when handler raises.

        Validates ADR-045: the framework calls stop_scan and close
        regardless of whether the stream handler raises.  ``run_stream``
        absorbs handler exceptions (logging them) so the caller sees a
        clean return — the key invariant is that lifecycle cleanup runs.

        Technique: Fault Injection + State Transition Testing.
        """
        harness = AppHarness.create()
        port = _LifecycleTrackingPort()
        resolved: dict[type, object] = {StreamablePort[_StreamItem]: port}
        shutdown = asyncio.Event()

        async def failing_handler(stream: Stream[_StreamItem]) -> AsyncIterator[None]:
            raise ValueError("simulated handler error")
            yield  # pragma: no cover  # make it an async generator

        # run_stream absorbs handler exceptions (logs, does not re-raise)
        await harness.run_stream(failing_handler, resolved, shutdown=shutdown)

        assert "stop_scan" in port.calls
        assert "close" in port.calls
        assert port.calls.index("stop_scan") < port.calls.index("close")
