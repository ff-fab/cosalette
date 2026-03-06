"""Shared stateless helpers used by the application runner.

These functions are used across telemetry, device, and command execution paths
but do not depend on ``App`` instance state — everything they need is passed as
arguments.

.. note::

   The module is private (``_runner_utils``), so the functions omit the
   leading underscore that they carried as ``App`` methods.
"""

from __future__ import annotations

import contextlib
import logging

from cosalette._errors import ErrorPublisher
from cosalette._persist import PersistPolicy
from cosalette._stores import DeviceStore, Store

logger = logging.getLogger(__name__)


def create_device_store(store: Store | None, name: str) -> DeviceStore:
    """Create and load a :class:`DeviceStore` for a device.

    Callers must ensure *store* is not ``None`` before calling.
    """
    if store is None:
        msg = "store must be set before calling create_device_store"
        raise RuntimeError(msg)
    device_store = DeviceStore(store, name)
    device_store.load()
    return device_store


def save_store_on_shutdown(device_store: DeviceStore | None, device_name: str) -> None:
    """Unconditional store save for shutdown safety net."""
    if device_store is None:
        return
    try:
        device_store.save()
    except Exception:
        logger.exception("Failed to save store for device '%s'", device_name)


async def publish_error_safely(
    error_publisher: ErrorPublisher,
    exc: Exception,
    device_name: str,
    is_root: bool,
) -> None:
    """Publish an error, suppressing failures to avoid masking the original."""
    with contextlib.suppress(Exception):
        await error_publisher.publish(exc, device=device_name, is_root=is_root)


def maybe_persist(
    device_store: DeviceStore | None,
    persist_policy: PersistPolicy | None,
    did_publish: bool,
    device_name: str,
) -> None:
    """Save device store if the persist policy says to."""
    if device_store is None or persist_policy is None:
        return
    if not persist_policy.should_save(device_store, did_publish):
        return
    try:
        device_store.save()
    except Exception:
        logger.exception("Failed to save store for device '%s'", device_name)
