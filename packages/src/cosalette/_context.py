"""Per-device and application contexts for cosalette device functions.

Provides :class:`DeviceContext` (injected into ``@app.device`` and
``@app.telemetry`` functions) and :class:`AppContext` (injected into
``@app.on_startup`` / ``@app.on_shutdown`` hooks).

DeviceContext scopes MQTT operations to the device's topic namespace
and provides shutdown-aware sleeping, command handler registration,
and adapter resolution.

AppContext provides a subset of DeviceContext's capabilities — settings
and adapter resolution only — suitable for lifecycle hooks that
should not act as devices.

See Also:
    ADR-010 — Device archetypes.
    ADR-006 — Hexagonal architecture (adapter resolution).
    ADR-001 — Framework architecture (lifecycle hooks).
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import json
from typing import Any

from cosalette._clock import ClockPort
from cosalette._mqtt import MessageCallback, MqttPort
from cosalette._settings import Settings

# ---------------------------------------------------------------------------
# DeviceContext
# ---------------------------------------------------------------------------


class DeviceContext:
    """Per-device runtime context injected by the framework.

    Provides device-scoped access to MQTT publishing, command registration,
    shutdown-aware sleeping, and adapter resolution.

    Each device function receives its own DeviceContext instance. The context
    is pre-configured with the device's name and topic prefix so that
    publish operations target the correct MQTT topics automatically.

    See Also:
        ADR-010 — Device archetypes.
        ADR-006 — Hexagonal architecture (adapter resolution).
    """

    def __init__(
        self,
        *,
        name: str,
        settings: Settings,
        mqtt: MqttPort,
        topic_prefix: str,
        shutdown_event: asyncio.Event,
        adapters: dict[type, object],
        clock: ClockPort,
    ) -> None:
        """Initialise per-device context.

        Args:
            name: Device name as registered (e.g. "blind").
            settings: Application settings instance.
            mqtt: MQTT port for publishing.
            topic_prefix: Root prefix for MQTT topics (e.g. "velux2mqtt").
            shutdown_event: Shared event that signals graceful shutdown.
            adapters: Resolved adapter registry mapping port types to instances.
            clock: Monotonic clock for timing.
        """
        self._name = name
        self._settings = settings
        self._mqtt = mqtt
        self._topic_prefix = topic_prefix
        self._shutdown_event = shutdown_event
        self._adapters = adapters
        self._clock = clock
        self._command_handler: MessageCallback | None = None

    # -- Read-only properties -----------------------------------------------

    @property
    def name(self) -> str:
        """Device name as registered."""
        return self._name

    @property
    def settings(self) -> Settings:
        """Application settings instance."""
        return self._settings

    @property
    def clock(self) -> ClockPort:
        """Monotonic clock for timing."""
        return self._clock

    @property
    def shutdown_requested(self) -> bool:
        """True when the framework has received a shutdown signal."""
        return self._shutdown_event.is_set()

    @property
    def command_handler(self) -> MessageCallback | None:
        """The registered command handler, or None. Framework-internal."""
        return self._command_handler

    # -- MQTT publishing ----------------------------------------------------

    async def publish_state(
        self,
        payload: dict[str, object],
        *,
        retain: bool = True,
    ) -> None:
        """Publish device state to ``{prefix}/{device}/state`` as JSON.

        This is the primary publication method for device telemetry.
        The payload dict is JSON-serialised automatically.

        Args:
            payload: Dict to serialise as JSON.
            retain: Whether the message should be retained (default True).
        """
        topic = f"{self._topic_prefix}/{self._name}/state"
        await self._mqtt.publish(topic, json.dumps(payload), retain=retain, qos=1)

    async def publish(
        self,
        channel: str,
        payload: str,
        *,
        retain: bool = False,
        qos: int = 1,
    ) -> None:
        """Publish to an arbitrary sub-channel: ``{prefix}/{device}/{channel}``.

        Escape hatch for non-standard topics. Prefer publish_state() for
        normal device state updates.
        """
        topic = f"{self._topic_prefix}/{self._name}/{channel}"
        await self._mqtt.publish(topic, payload, retain=retain, qos=qos)

    # -- Shutdown-aware sleep -----------------------------------------------

    async def sleep(self, seconds: float) -> None:
        """Shutdown-aware sleep.

        Returns early (without exception) if shutdown is requested during
        the sleep period. This enables the idiomatic pattern::

            while not ctx.shutdown_requested:
                await ctx.sleep(10)
                # ... do work ...
        """
        sleep_task = asyncio.ensure_future(asyncio.sleep(seconds))
        shutdown_task = asyncio.ensure_future(self._shutdown_event.wait())

        done, pending = await asyncio.wait(
            {sleep_task, shutdown_task},
            return_when=asyncio.FIRST_COMPLETED,
        )

        for task in pending:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    # -- Command registration -----------------------------------------------

    def on_command(self, handler: MessageCallback) -> MessageCallback:
        """Register a command handler for this device.

        Can be used as a decorator::

            @ctx.on_command
            async def handle(topic: str, payload: str) -> None:
                ...

        Or as a direct call::

            ctx.on_command(handle)

        Raises:
            RuntimeError: If a handler is already registered.

        Returns:
            The handler unchanged (enables decorator use).
        """
        if self._command_handler is not None:
            msg = f"Command handler already registered for device '{self._name}'"
            raise RuntimeError(msg)
        self._command_handler = handler
        return handler

    # -- Adapter resolution -------------------------------------------------

    def adapter[T](self, port_type: type[T]) -> T:
        """Resolve an adapter by port type.

        Args:
            port_type: The Protocol type to look up.

        Returns:
            The adapter instance registered for that port type.

        Raises:
            LookupError: If no adapter is registered for the port type.
        """
        try:
            return self._adapters[port_type]  # type: ignore[return-value]
        except KeyError:
            msg = f"No adapter registered for {port_type!r}"
            raise LookupError(msg) from None


# ---------------------------------------------------------------------------
# AppContext
# ---------------------------------------------------------------------------


class AppContext:
    """Context for application lifecycle hooks.

    Provided to ``@app.on_startup`` and ``@app.on_shutdown`` handlers.
    Offers access to settings and adapter resolution but NOT per-device
    features (no publish, no on_command, no sleep).

    See Also:
        ADR-001 — Framework architecture (lifecycle hooks).
    """

    def __init__(
        self,
        *,
        settings: Settings,
        adapters: dict[type, object],
    ) -> None:
        self._settings = settings
        self._adapters = adapters

    @property
    def settings(self) -> Settings:
        """Application settings instance."""
        return self._settings

    def adapter[T](self, port_type: type[T]) -> T:
        """Resolve an adapter by port type.

        Args:
            port_type: The Protocol type to look up.

        Returns:
            The adapter instance registered for that port type.

        Raises:
            LookupError: If no adapter is registered for the port type.
        """
        try:
            return self._adapters[port_type]  # type: ignore[return-value]
        except KeyError:
            msg = f"No adapter registered for {port_type!r}"
            raise LookupError(msg) from None


# ---------------------------------------------------------------------------
# Import utility
# ---------------------------------------------------------------------------


def _import_string(dotted_path: str) -> Any:
    """Import a class from a ``module.path:ClassName`` string.

    Used for lazy adapter imports — hardware libraries may not be
    available on development machines (ADR-006 lazy import pattern).

    Args:
        dotted_path: Import path in ``module.path:ClassName`` format.

    Returns:
        The imported class/object.

    Raises:
        ImportError: If the module cannot be found.
        AttributeError: If the class doesn't exist in the module.
        ValueError: If the path doesn't contain exactly one ``:``.
    """
    parts = dotted_path.split(":")
    if len(parts) != 2:  # noqa: PLR2004
        msg = f"Expected 'module.path:ClassName', got {dotted_path!r}"
        raise ValueError(msg)

    module_path, class_name = parts
    module = importlib.import_module(module_path)
    return getattr(module, class_name)
