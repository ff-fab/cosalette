"""Stream handler registration and async runner.

Private module containing the internal runner for @app.stream handlers.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
from collections.abc import AsyncIterable, Callable
from typing import TYPE_CHECKING, Any, cast, get_args, get_origin

from cosalette._injection import resolve_kwargs
from cosalette._persistence._stores import DeviceStore, Store
from cosalette._registration import _StreamRegistration
from cosalette._runners._runner_utils import (
    async_create_device_store,
    async_save_store_on_shutdown,
)
from cosalette._stream import Stream, StreamablePort
from cosalette._utils import _callable_qualname

if TYPE_CHECKING:
    from cosalette._registration import _ReactorRegistration

logger = logging.getLogger(__name__)


async def _async_safe_call(coro_fn: Callable[[], Any], label: str, name: str) -> None:
    """Await coro_fn(); log and suppress any exception."""
    try:
        await coro_fn()
    except Exception:
        logger.exception("%s() error for stream '%s'", label, name)


def _find_port_entry(
    item_type: type, resolved_adapters: dict[type, object]
) -> object | None:
    """Return the adapter instance for StreamablePort[item_type], or None."""
    for port_type, adapter in resolved_adapters.items():
        args = get_args(port_type)
        if args and args[0] == item_type and get_origin(port_type) is StreamablePort:
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
    """Find the stream adapter matching reg's stream item type.

    Returns (item_type, adapter_instance).
    Raises RuntimeError if no compatible adapter is found.
    """
    for _param_name, annotation in reg.injection_plan:
        if get_origin(annotation) is Stream:
            item_type = get_args(annotation)[0]
            adapter = _find_port_entry(item_type, resolved_adapters)
            if adapter is not None:
                return item_type, adapter
            item_type_name = getattr(item_type, "__name__", repr(item_type))
            msg = (
                f"Stream '{reg.name}' requires StreamablePort[{item_type_name}] "
                f"but no matching adapter was registered. "
                f"Register one with "
                f"app.adapter(StreamablePort[{item_type_name}], YourAdapter)."
            )
            raise RuntimeError(msg)
    msg = f"Stream '{reg.name}': no Stream[T] found in injection plan"
    raise RuntimeError(msg)


async def run_stream(
    reg: _StreamRegistration,
    resolved_adapters: dict[type, object],
    providers: dict[type, Any],
    shutdown_event: asyncio.Event,
    reactors: list[_ReactorRegistration] | None = None,
    store: Store | None = None,
) -> None:
    """Open adapter, wire stream, run handler, tear down.

    The runner:
    1. Finds the StreamablePort[T] adapter instance.
    2. Creates a Stream[T](maxsize, backpressure) instance.
    3. Opens an AsyncExitStack and registers port.close / port.stop_scan as
       callbacks **before** calling port.open() — guaranteeing teardown even
       when open() raises.  Callbacks execute in LIFO order: stop_scan first,
       then close.  Each callback is wrapped in _async_safe_call so a failure
       in one does not prevent the other from running.
    4. Awaits port.open(), calls port.register_callback(stream.put), awaits
       port.start_scan().
    5. Starts a background task that calls stream.shutdown() when shutdown_event fires.
    6. Injects the Stream instance into the handler's kwargs and awaits the handler.
    7. On exit (normal, exception, or cancel) the AsyncExitStack fires the
       registered callbacks; the watcher task is then cancelled and stream
       shutdown is signalled.

    CancelledError propagates immediately for clean shutdown.
    """
    _item_type, _port = find_stream_adapter(reg, resolved_adapters)
    stream: Stream[Any] = Stream(maxsize=reg.maxsize, backpressure=reg.backpressure)

    # Create a per-stream DeviceStore only when the handler's injection plan
    # declares DeviceStore — avoids unnecessary backend I/O for stateless
    # handlers.
    needs_store = any(a is DeviceStore for _, a in reg.injection_plan)
    device_store: DeviceStore | None = None
    stream_providers: dict[type, Any] = dict(providers)
    # Expose the stream adapter's concrete type so handlers can inject it
    # for non-lifecycle operations (e.g. set_led, get_battery).  The
    # framework retains exclusive lifecycle ownership (open, start_scan,
    # stop_scan, close); handlers must not call those methods on the
    # injected instance.
    stream_providers[type(_port)] = _port
    if store is not None and needs_store:
        device_store = await async_create_device_store(store, reg.name)
        stream_providers[DeviceStore] = device_store

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
            port: StreamablePort[Any] = cast(StreamablePort[Any], _port)
            port_stack.push_async_callback(
                _async_safe_call, port.close, "close", reg.name
            )
            port_stack.push_async_callback(
                _async_safe_call, port.stop_scan, "stop_scan", reg.name
            )
            await port.open()
            port.register_callback(stream.put)
            await port.start_scan()
            await _run_stream_handler(reg, stream, stream_providers, reactors)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Stream handler '%s' error", reg.name)
    finally:
        watcher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watcher
        stream.shutdown()
        await async_save_store_on_shutdown(device_store, reg.name)


async def _run_stream_handler(
    reg: _StreamRegistration,
    stream: Stream[Any],
    providers: dict[type, Any],
    reactors: list[_ReactorRegistration] | None,
) -> None:
    """Run stream handler and dispatch reactors after each yield.

    Supports async generator/iterable handlers only.
    Dispatches reactors after each yielded boundary and once at
    normal completion.
    """
    kwargs = _build_handler_kwargs(reg, stream, providers)
    result = reg.func(**kwargs)

    # Reject coroutine-style handlers (breaking change)
    if inspect.iscoroutine(result):
        # Clean up the coroutine to prevent unawaited coroutine warnings
        result.close()
        type_name = type(result).__qualname__
        msg = (
            f"Stream handler {_callable_qualname(reg.func)!r} must return "
            f"an async generator or async iterable, got {type_name!r}. "
            f"Update to 'async def' that yields after each unit of work."
        )
        raise TypeError(msg)

    # Support any AsyncIterable, not just async generators
    if not isinstance(result, AsyncIterable):
        type_name = type(result).__qualname__
        msg = (
            f"Stream handler {_callable_qualname(reg.func)!r} must return "
            f"an async generator or async iterable, got {type_name!r}. "
            f"Update to 'async def' that yields after each unit of work."
        )
        raise TypeError(msg)

    # Iterate the async iterable and dispatch reactors after each yield
    from cosalette._reactors import run_reactor_boundaries

    await run_reactor_boundaries(result, providers, reactors)
