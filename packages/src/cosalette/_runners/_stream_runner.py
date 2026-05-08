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
from cosalette._runners._runner_utils import create_device_store, save_store_on_shutdown
from cosalette._stream import AsyncStreamablePort, Stream, StreamablePort
from cosalette._utils import _callable_qualname

if TYPE_CHECKING:
    from cosalette._registration import _ReactorRegistration

logger = logging.getLogger(__name__)


def _safe_call(fn: Callable[[], None], label: str, name: str) -> None:
    """Call fn(); log and suppress any exception."""
    try:
        fn()
    except Exception:
        logger.exception("%s() error for stream '%s'", label, name)


async def _async_safe_call(coro_fn: Callable[[], Any], label: str, name: str) -> None:
    """Await coro_fn(); log and suppress any exception."""
    try:
        await coro_fn()
    except Exception:
        logger.exception("%s() error for stream '%s'", label, name)


def _classify_port_entry(
    port_type: type, adapter: object, item_type: type
) -> tuple[object | None, object | None]:
    """Return (sync_match, async_match) for a single port entry against item_type."""
    args = get_args(port_type)
    if not (args and args[0] == item_type):
        return None, None
    origin = get_origin(port_type)
    if origin is StreamablePort:
        return adapter, None
    if origin is AsyncStreamablePort:
        return None, adapter
    return None, None


def _resolve_port_matches(
    sync_match: object | None,
    async_match: object | None,
    item_type: type,
) -> tuple[object, bool] | None:
    """Return (adapter, is_async), raise on ambiguity, or None if no match."""
    if sync_match is not None and async_match is not None:
        item_type_name = getattr(item_type, "__name__", repr(item_type))
        msg = (
            f"Ambiguous stream adapter for item type '{item_type_name}': "
            f"both StreamablePort[{item_type_name}] and "
            f"AsyncStreamablePort[{item_type_name}] are registered. "
            f"Remove one registration."
        )
        raise RuntimeError(msg)
    if async_match is not None:
        return async_match, True
    if sync_match is not None:
        return sync_match, False
    return None


def _find_port_entry_for_item_type(
    item_type: type, resolved_adapters: dict[type, object]
) -> tuple[object, bool] | None:
    """Return (adapter_instance, is_async) for item_type, or None.

    Checks both StreamablePort[item_type] and AsyncStreamablePort[item_type].
    Raises RuntimeError if both are registered (ambiguous).
    """
    sync_match: object | None = None
    async_match: object | None = None

    for port_type, adapter in resolved_adapters.items():
        s, a = _classify_port_entry(port_type, adapter, item_type)
        if s is not None:
            sync_match = s
        if a is not None:
            async_match = a

    return _resolve_port_matches(sync_match, async_match, item_type)


def _find_port_for_item_type(
    item_type: type, resolved_adapters: dict[type, object]
) -> object | None:
    """Return the StreamablePort[item_type] adapter instance, or None.

    Deprecated: use _find_port_entry_for_item_type for new code.
    """
    entry = _find_port_entry_for_item_type(item_type, resolved_adapters)
    return entry[0] if entry is not None else None


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
) -> tuple[type, object, bool]:
    """Find the stream adapter matching reg's stream item type.

    Returns (item_type, adapter_instance, is_async).
    ``is_async`` is True when the adapter was registered under
    AsyncStreamablePort[T], False for StreamablePort[T].
    Raises RuntimeError if no compatible adapter is found, or if both
    StreamablePort[T] and AsyncStreamablePort[T] are registered (ambiguous).
    """
    for _param_name, annotation in reg.injection_plan:
        if get_origin(annotation) is Stream:
            item_type = get_args(annotation)[0]
            entry = _find_port_entry_for_item_type(item_type, resolved_adapters)
            if entry is not None:
                adapter, is_async = entry
                return item_type, adapter, is_async
            item_type_name = getattr(item_type, "__name__", repr(item_type))
            msg = (
                f"Stream '{reg.name}' requires StreamablePort[{item_type_name}] "
                f"or AsyncStreamablePort[{item_type_name}] "
                f"but no matching adapter was registered. "
                f"Register one with "
                f"app.adapter(StreamablePort[{item_type_name}], YourAdapter) "
                f"or app.adapter(AsyncStreamablePort[{item_type_name}], YourAdapter)."
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
    1. Finds the StreamablePort[T] or AsyncStreamablePort[T] adapter instance.
    2. Creates a Stream[T](maxsize, backpressure) instance.
    3. Opens an AsyncExitStack and registers port.close / port.stop_scan as
       callbacks **before** calling port.open() — guaranteeing teardown even
       when open() raises.  Callbacks execute in LIFO order: stop_scan first,
       then close.  Each callback is wrapped in _safe_call/_async_safe_call
       so a failure in one does not prevent the other from running.
    4. Calls port.open(), port.register_callback(stream.put), port.start_scan().
       For async ports, open/start_scan/stop_scan/close are awaited.
    5. Starts a background task that calls stream.shutdown() when shutdown_event fires.
    6. Injects the Stream instance into the handler's kwargs and awaits the handler.
    7. On exit (normal, exception, or cancel) the AsyncExitStack fires the
       registered callbacks; the watcher task is then cancelled and stream
       shutdown is signalled.

    CancelledError propagates immediately for clean shutdown.
    """
    _item_type, _port, _is_async = find_stream_adapter(reg, resolved_adapters)
    stream: Stream[Any] = Stream(maxsize=reg.maxsize, backpressure=reg.backpressure)

    # Create a per-stream DeviceStore if a store backend is configured, and
    # make it available for injection.  The store is saved in the finally
    # block so state is persisted on both normal exit and error.
    device_store: DeviceStore | None = None
    stream_providers: dict[type, Any] = dict(providers)
    # Expose the stream adapter's concrete type so handlers can inject it
    # for non-lifecycle operations (e.g. set_led, get_battery).  The
    # framework retains exclusive lifecycle ownership (open, start_scan,
    # stop_scan, close); handlers must not call those methods on the
    # injected instance.
    stream_providers[type(_port)] = _port
    if store is not None:
        device_store = create_device_store(store, reg.name)
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
            if _is_async:
                _async_port: AsyncStreamablePort[Any] = cast(
                    AsyncStreamablePort[Any], _port
                )
                port_stack.push_async_callback(
                    _async_safe_call, _async_port.close, "close", reg.name
                )
                port_stack.push_async_callback(
                    _async_safe_call, _async_port.stop_scan, "stop_scan", reg.name
                )
                await _async_port.open()
                _async_port.register_callback(stream.put)
                await _async_port.start_scan()
            else:
                port: StreamablePort[Any] = cast(StreamablePort[Any], _port)
                port_stack.callback(_safe_call, port.close, "close", reg.name)
                port_stack.callback(_safe_call, port.stop_scan, "stop_scan", reg.name)
                port.open()
                port.register_callback(stream.put)
                port.start_scan()
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
        save_store_on_shutdown(device_store, reg.name)


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
