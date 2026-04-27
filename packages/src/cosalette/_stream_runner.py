"""Stream handler registration and async runner.

Private module containing the internal runner for @app.stream handlers.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any, cast, get_args, get_origin

from cosalette._injection import resolve_kwargs
from cosalette._registration import _StreamRegistration
from cosalette._stream import Stream, StreamablePort

logger = logging.getLogger(__name__)


def find_stream_adapter(
    reg: _StreamRegistration,
    resolved_adapters: dict[type, object],
) -> tuple[type, object]:
    """Find the StreamablePort[T] adapter matching reg's stream item type.

    Returns (item_type, adapter_instance).
    Raises RuntimeError if no compatible adapter is found at runtime
    (should have been caught at registration, but defensive).
    """
    # Extract item type from injection_plan: find the Stream[T] param
    for _param_name, annotation in reg.injection_plan:
        origin = get_origin(annotation)
        if origin is Stream:
            item_type = get_args(annotation)[0]
            # Find matching StreamablePort[item_type] in resolved_adapters
            for port_type, adapter_instance in resolved_adapters.items():
                if get_origin(port_type) is StreamablePort:
                    port_args = get_args(port_type)
                    if port_args and port_args[0] == item_type:
                        return item_type, adapter_instance
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
    stream: Stream[Any] = Stream()

    # Background task: call stream.shutdown() when global shutdown fires
    async def _shutdown_watcher() -> None:
        await shutdown_event.wait()
        stream.shutdown()

    watcher = asyncio.create_task(
        _shutdown_watcher(), name=f"stream-watcher:{reg.name}"
    )

    try:
        # Open and wire the port inside try so cleanup always runs on startup failure
        port.open()
        port.register_callback(stream.put)
        port.start_scan()

        # Build kwargs: directly map the stream param name to the stream instance
        stream_kwargs: dict[str, Any] = {}
        for param_name, annotation in reg.injection_plan:
            if get_origin(annotation) is Stream:
                stream_kwargs[param_name] = stream
                break

        # Resolve everything else from providers
        non_stream_plan = [
            (n, a) for n, a in reg.injection_plan if get_origin(a) is not Stream
        ]
        other_kwargs = resolve_kwargs(non_stream_plan, providers)
        kwargs = {**other_kwargs, **stream_kwargs}
        await reg.func(**kwargs)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Stream handler '%s' error", reg.name)
    finally:
        watcher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watcher
        stream.shutdown()
        try:
            port.stop_scan()
        except Exception:
            logger.exception("stop_scan() error for stream '%s'", reg.name)
        try:
            port.close()
        except Exception:
            logger.exception("close() error for stream '%s'", reg.name)
