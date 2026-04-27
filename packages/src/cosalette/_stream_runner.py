"""Stream handler registration and async runner.

Private module containing the internal runner for @app.stream handlers.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from typing import Any, cast, get_args, get_origin

from cosalette._injection import resolve_kwargs
from cosalette._registration import _StreamRegistration
from cosalette._stream import Stream, StreamablePort

logger = logging.getLogger(__name__)


def _safe_call(fn: Callable[[], None], label: str, name: str) -> None:
    """Call fn(); log and suppress any exception."""
    try:
        fn()
    except Exception:
        logger.exception("%s() error for stream '%s'", label, name)


def _find_port_for_item_type(
    item_type: type, resolved_adapters: dict[type, object]
) -> object | None:
    """Return the StreamablePort[item_type] adapter instance, or None."""
    for port_type, adapter in resolved_adapters.items():
        if get_origin(port_type) is StreamablePort:
            port_args = get_args(port_type)
            if port_args and port_args[0] == item_type:
                return adapter
    return None


def _build_handler_kwargs(
    reg: _StreamRegistration,
    stream: Stream[Any],
    providers: dict[type, Any],
) -> dict[str, Any]:
    """Map the Stream param and resolve remaining kwargs from providers."""
    stream_kwargs: dict[str, Any] = {}
    for param_name, annotation in reg.injection_plan:
        if get_origin(annotation) is Stream:
            stream_kwargs[param_name] = stream
            break
    non_stream_plan = [
        (n, a) for n, a in reg.injection_plan if get_origin(a) is not Stream
    ]
    return {**resolve_kwargs(non_stream_plan, providers), **stream_kwargs}


def find_stream_adapter(
    reg: _StreamRegistration,
    resolved_adapters: dict[type, object],
) -> tuple[type, object]:
    """Find the StreamablePort[T] adapter matching reg's stream item type.

    Returns (item_type, adapter_instance).
    Raises RuntimeError if no compatible adapter is found at runtime
    (should have been caught at registration, but defensive).
    """
    for _param_name, annotation in reg.injection_plan:
        if get_origin(annotation) is Stream:
            item_type = get_args(annotation)[0]
            adapter = _find_port_for_item_type(item_type, resolved_adapters)
            if adapter is not None:
                return item_type, adapter
            item_type_name = getattr(item_type, "__name__", repr(item_type))
            msg = (
                f"Stream runner '{reg.name}': no StreamablePort[{item_type_name}] "
                "found in resolved adapters at runtime"
            )
            raise RuntimeError(msg)
    msg = f"Stream runner '{reg.name}': no Stream[T] found in injection plan"
    raise RuntimeError(msg)


async def run_stream(
    reg: _StreamRegistration,
    resolved_adapters: dict[type, object],
    providers: dict[type, Any],
    shutdown_event: asyncio.Event,
) -> None:
    """Open adapter, wire stream, run handler, tear down.

    The runner:
    1. Finds the StreamablePort[T] adapter instance from resolved_adapters.
    2. Creates a Stream[T]() instance.
    3. Calls port.open(), port.register_callback(stream.put), port.start_scan().
    4. Starts a background task that calls stream.shutdown() when shutdown_event fires.
    5. Injects the Stream instance into the handler's kwargs and awaits the handler.
    6. In finally: calls stream.shutdown(), port.stop_scan(), port.close().

    CancelledError propagates immediately for clean shutdown.
    """
    _item_type, _port = find_stream_adapter(reg, resolved_adapters)
    port: StreamablePort[Any] = cast(StreamablePort[Any], _port)
    stream: Stream[Any] = Stream(maxsize=reg.maxsize, backpressure=reg.backpressure)

    # Background task: call stream.shutdown() when global shutdown fires
    async def _shutdown_watcher() -> None:
        await shutdown_event.wait()
        stream.shutdown()

    watcher = asyncio.create_task(
        _shutdown_watcher(), name=f"stream-watcher:{reg.name}"
    )

    try:
        async with contextlib.AsyncExitStack() as port_stack:
            # Register cleanup before open() so it always runs, even on failure.
            # LIFO: close runs last (registered first), stop_scan runs first
            # (registered last).
            port_stack.callback(_safe_call, port.close, "close", reg.name)
            port_stack.callback(_safe_call, port.stop_scan, "stop_scan", reg.name)
            port.open()
            port.register_callback(stream.put)
            port.start_scan()
            await reg.func(**_build_handler_kwargs(reg, stream, providers))
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Stream handler '%s' error", reg.name)
    finally:
        watcher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watcher
        stream.shutdown()
