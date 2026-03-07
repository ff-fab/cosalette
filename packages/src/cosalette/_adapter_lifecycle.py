"""Adapter lifecycle management for cosalette applications.

Handles resolution of registered adapters to concrete instances plus
async-context-manager entry/exit via :class:`~contextlib.AsyncExitStack`.
These were originally private methods on :class:`~cosalette._app.App`;
extracting them reduces the god-class surface and isolates the two
highest-cyclomatic-complexity routines.

.. note::

   The module is private (``_adapter_lifecycle``), so the functions omit
   the leading underscore that they carried as ``App`` methods.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from cosalette._injection import build_injection_plan, resolve_kwargs
from cosalette._settings import Settings
from cosalette._utils import _import_string

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal value objects & helpers (relocated from _registration.py)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _AdapterEntry:
    """Internal record of a registered adapter.

    Both impl and dry_run can be either a class, a factory callable,
    or a ``module:ClassName`` string for lazy import.
    """

    impl: type | str | Callable[..., object]
    dry_run: type | str | Callable[..., object] | None = None


def _is_async_context_manager(obj: object) -> bool:
    """Check if an object implements the async context manager protocol.

    Uses ``hasattr`` checks rather than
    ``isinstance(obj, AbstractAsyncContextManager)`` because the ABC
    requires explicit registration — duck-typing is more inclusive.
    """
    return hasattr(obj, "__aenter__") and hasattr(obj, "__aexit__")


def _build_adapter_providers(settings: Settings) -> dict[type, Any]:
    """Build the provider map available during adapter resolution.

    At adapter-resolution time only the parsed :class:`Settings`
    instance is available (MQTT, clock, and device contexts are
    created later in the bootstrap sequence).

    Returns a mapping keyed by both :class:`Settings` and the
    concrete settings subclass, so factories can annotate with
    either.
    """
    # Extend this dict when new types become injectable at
    # adapter-resolution time (e.g. app metadata, logging config).
    providers: dict[type, Any] = {Settings: settings}
    settings_type = type(settings)
    if settings_type is not Settings:
        providers[settings_type] = settings
    return providers


def _call_factory(
    factory: Callable[..., object],
    providers: dict[type, Any],
) -> object:
    """Invoke an adapter factory with signature-based injection.

    Introspects *factory*'s parameters and resolves each from
    *providers*.  Zero-arg factories are called directly (backward
    compatible).  This reuses the same :func:`build_injection_plan`
    / :func:`resolve_kwargs` machinery that device handlers use.

    Args:
        factory: A callable returning an adapter instance.
        providers: Type → instance map (currently just Settings).

    Returns:
        The adapter instance created by *factory*.
    """
    plan = build_injection_plan(factory)
    if not plan:
        return factory()
    kwargs = resolve_kwargs(plan, providers)
    return factory(**kwargs)


def _resolve_single(
    port_type: type,
    entry: _AdapterEntry,
    dry_run: bool,
    providers: dict[type, object],
) -> object:
    """Resolve one adapter entry to an instance."""
    raw_impl: type | str | Callable[..., object] = (
        entry.dry_run if (dry_run and entry.dry_run) else entry.impl
    )
    if isinstance(raw_impl, str):
        raw_impl = _import_string(raw_impl)
    # At this point raw_impl is a class or callable — both
    # accepted by _call_factory (classes are callable).
    if not callable(raw_impl):  # narrow for mypy
        msg = (
            f"expected callable adapter for {port_type.__name__}, "
            f"got {type(raw_impl).__name__}: {raw_impl!r}"
        )
        raise TypeError(msg)
    return _call_factory(raw_impl, providers)


def resolve_adapters(
    adapters_dict: dict[type, _AdapterEntry],
    dry_run: bool,
    settings: Settings,
) -> dict[type, object]:
    """Resolve all registered adapters to instances.

    When *dry_run* is ``True`` and an entry has a ``dry_run`` variant,
    the dry-run implementation is used instead of the normal one.
    String values are lazily imported via :func:`_import_string`
    before instantiation.

    All adapter forms — classes, import strings, and factory callables —
    are resolved via :func:`_call_factory` with signature-based
    injection.  If the callable (or class ``__init__``) declares a
    parameter annotated with ``Settings`` (or a subclass), the parsed
    settings instance is injected automatically.  Zero-arg constructors
    and callables remain backward compatible.

    Returned adapter instances that implement the async context manager
    protocol (``__aenter__``/``__aexit__``) will be auto-entered by
    :func:`enter_lifecycle_adapters` and exited during teardown.
    """
    providers = _build_adapter_providers(settings)
    return {
        port_type: _resolve_single(port_type, entry, dry_run, providers)
        for port_type, entry in adapters_dict.items()
    }


def _should_enter(adapter: object, seen: set[int]) -> bool:
    """Return ``True`` if *adapter* needs async-context-manager entry.

    Filters out non-lifecycle adapters, duplicates (by ``id``), and
    adapters whose ``__aenter__`` is not callable.
    """
    if not _is_async_context_manager(adapter):
        return False
    if id(adapter) in seen:
        return False
    if not callable(getattr(adapter, "__aenter__", None)):
        msg = f"Adapter {adapter!r} has __aenter__ but it's not callable"
        raise TypeError(msg)
    return True


@asynccontextmanager
async def enter_lifecycle_adapters(
    resolved_adapters: dict[type, object],
    shutdown_event: asyncio.Event,
) -> AsyncIterator[None]:
    """Enter adapters that implement the async context manager protocol.

    Uses :class:`~contextlib.AsyncExitStack` for LIFO exit ordering
    and exception safety.  Non-lifecycle adapters are ignored.
    Shared instances (same object registered for multiple ports)
    are entered only once, identified by ``id()``.

    Each adapter entry is raced against *shutdown_event* so that a
    signal arriving during a slow ``__aenter__`` triggers a clean
    abort instead of an indefinite hang.
    """
    async with contextlib.AsyncExitStack() as stack:
        seen: set[int] = set()
        for adapter in resolved_adapters.values():
            if not _should_enter(adapter, seen):
                continue
            seen.add(id(adapter))

            aborted = await enter_adapter_or_abort(stack, adapter, shutdown_event)
            if aborted:
                break
        yield


async def enter_adapter_or_abort(
    stack: contextlib.AsyncExitStack,
    adapter: object,
    shutdown_event: asyncio.Event,
) -> bool:
    """Enter a single adapter, racing against the shutdown event.

    Returns ``True`` if the shutdown event fired before entry
    completed (caller should stop entering further adapters).
    Returns ``False`` on successful entry.
    """
    entry_task: asyncio.Task[object] = asyncio.create_task(
        stack.enter_async_context(adapter)  # type: ignore[arg-type]
    )
    shutdown_task: asyncio.Task[bool] = asyncio.create_task(shutdown_event.wait())
    try:
        done, _pending = await asyncio.wait(
            {entry_task, shutdown_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
    finally:
        # Guarantee cleanup on all exit paths (including external
        # cancellation of this coroutine).
        entry_task.cancel()
        shutdown_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.gather(entry_task, shutdown_task)

    # Re-raise with original traceback if __aenter__ failed.
    if entry_task.done() and not entry_task.cancelled():
        entry_task.result()

    if shutdown_task in done and entry_task not in done:
        logger.warning(
            "Shutdown requested during entry of adapter %s "
            "— skipping remaining adapters",
            type(adapter).__name__,
        )
        return True

    return False
