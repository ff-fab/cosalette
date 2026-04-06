"""Per-device and application contexts for cosalette device functions.

Provides :class:`DeviceContext` (injected into ``@app.device`` and
``@app.telemetry`` functions) and :class:`AppContext` (injected into
the lifespan context manager).

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
import datetime
import logging
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from types import MappingProxyType
from typing import overload

from cosalette._clock import ClockPort
from cosalette._command import Command
from cosalette._json import dumps
from cosalette._mqtt import CommandHandler, MqttPort
from cosalette._settings import Settings
from cosalette._utils import _import_string as _import_string  # re-export

logger = logging.getLogger(__name__)

_RESERVED_SUB_ENTITY_NAMES: frozenset[str] = frozenset(
    {
        "state",
        "set",
        "availability",
        "status",
        "error",
        "config",
        "attributes",
        "json_attributes",
        "diagnostic",
        "firmware",
    }
)


# ---------------------------------------------------------------------------
# Wall-clock helpers
# ---------------------------------------------------------------------------


def _seconds_until(
    target: datetime.time | Sequence[datetime.time],
    *,
    tz: datetime.tzinfo | None = None,
) -> float:
    """Compute seconds from now until the next occurrence of *target*.

    When *target* is a sequence, returns the minimum positive delta
    across all entries (i.e. sleeps until the nearest upcoming time).

    Uses local timezone when *tz* is ``None``.

    Returns:
        Non-negative seconds to sleep.

    Raises:
        ValueError: If *target* is an empty sequence.
    """
    now = datetime.datetime.now(tz=tz)
    targets = [target] if isinstance(target, datetime.time) else list(target)
    if not targets:
        msg = "At least one target time is required"
        raise ValueError(msg)

    deltas: list[float] = []
    for t in targets:
        target_dt = now.replace(
            hour=t.hour,
            minute=t.minute,
            second=t.second,
            microsecond=t.microsecond,
        )
        if target_dt <= now:
            target_dt += datetime.timedelta(days=1)
        deltas.append((target_dt - now).total_seconds())

    return min(deltas)


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
        is_root: bool = False,
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
            is_root: When True, topics omit the device name segment
                (root-level device).
        """
        self._name = name
        self._settings = settings
        self._mqtt = mqtt
        self._topic_prefix = topic_prefix
        self._shutdown_event = shutdown_event
        self._adapters = adapters
        self._clock = clock
        self._command_handlers: dict[str | None, CommandHandler] = {}
        self._is_root = is_root
        self._command_queue: asyncio.Queue[Command] = asyncio.Queue()
        self._commands_consumed: bool = False
        self._topic_base = topic_prefix if is_root else f"{topic_prefix}/{name}"
        self._active_sub_entities: set[str] = set()

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
    def command_handler(self) -> CommandHandler | None:
        """The root command handler, or None. Framework-internal."""
        return self._command_handlers.get(None)

    @property
    def command_handlers(self) -> Mapping[str | None, CommandHandler]:
        """All registered command handlers keyed by sub-topic. Framework-internal."""
        return MappingProxyType(self._command_handlers)

    def get_command_handler(
        self,
        sub_topic: str | None = None,
    ) -> CommandHandler | None:
        """Look up the command handler for a sub-topic (or root)."""
        return self._command_handlers.get(sub_topic)

    # -- MQTT publishing ----------------------------------------------------

    async def publish_state(
        self,
        payload: dict[str, object],
        *,
        retain: bool = True,
    ) -> None:
        """Publish device state to ``{prefix}/{device}/state`` as JSON.

        For root devices (unnamed), publishes to ``{prefix}/state`` instead.

        This is the primary publication method for device telemetry.
        The payload dict is JSON-serialised automatically.

        Args:
            payload: Dict to serialise as JSON.
            retain: Whether the message should be retained (default True).
        """
        topic = f"{self._topic_base}/state"
        await self._mqtt.publish(topic, dumps(payload), retain=retain, qos=1)

    async def publish(
        self,
        channel: str,
        payload: str,
        *,
        retain: bool = False,
        qos: int = 1,
    ) -> None:
        """Publish to an arbitrary sub-channel: ``{prefix}/{device}/{channel}``.

        For root devices (unnamed), publishes to ``{prefix}/{channel}`` instead.

        Escape hatch for non-standard topics. Prefer publish_state() for
        normal device state updates.
        """
        topic = f"{self._topic_base}/{channel}"
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
        if self._shutdown_event.is_set():
            return

        sleep_task = asyncio.ensure_future(self._clock.sleep(seconds))
        shutdown_task = asyncio.ensure_future(self._shutdown_event.wait())

        done, pending = await asyncio.wait(
            {sleep_task, shutdown_task},
            return_when=asyncio.FIRST_COMPLETED,
        )

        for task in pending:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def sleep_until(
        self,
        target: datetime.time | Sequence[datetime.time],
        *,
        tz: datetime.tzinfo | None = None,
    ) -> None:
        """Shutdown-aware sleep until a wall-clock time.

        Sleeps until the next occurrence of *target* (or the nearest
        upcoming time if a sequence is given).  Uses local timezone
        when *tz* is ``None``.

        Returns early (without exception) if shutdown is requested
        during the sleep, via :meth:`sleep`.

        Example — poll twice daily at 06:00 and 18:00 local time::

            while not ctx.shutdown_requested:
                data = await read_data()
                await ctx.publish_state(data)
                await ctx.sleep_until([time(6, 0), time(18, 0)])

        Args:
            target: A :class:`datetime.time` or sequence of times to
                sleep until.
            tz: Timezone for interpreting *target*.  ``None`` (default)
                uses the system's local timezone.

        Raises:
            ValueError: If *target* is an empty sequence.

        See Also:
            ADR-032 — Wall-clock scheduling design.
        """
        seconds = _seconds_until(target, tz=tz)
        await self.sleep(seconds)

    # -- Sub-entity lifecycle -----------------------------------------------

    def _validate_sub_entity_name(self, name: str) -> None:
        """Validate a sub-entity name.

        Raises:
            ValueError: If the name is empty, contains invalid MQTT
                characters, is a reserved topic name, or is already
                active on this context.
        """
        if not name:
            msg = "Sub-entity name must not be empty"
            raise ValueError(msg)
        invalid = set(name) & {"/", "+", "#"}
        if invalid:
            msg = (
                f"Sub-entity name contains invalid MQTT characters {invalid}: '{name}'"
            )
            raise ValueError(msg)
        if name in _RESERVED_SUB_ENTITY_NAMES:
            msg = f"Sub-entity name '{name}' is reserved"
            raise ValueError(msg)
        if name in self._active_sub_entities:
            msg = f"Sub-entity '{name}' is already active on device '{self._name}'"
            raise ValueError(msg)
        if name == self._name:
            logger.warning(
                "Sub-entity name '%s' matches device name — "
                "this is allowed but likely a mistake",
                name,
            )

    @contextlib.asynccontextmanager
    async def sub_entity(self, name: str) -> AsyncIterator[SubEntityContext]:
        """Scoped sub-entity lifecycle with automatic availability.

        Publishes ``"online"`` on enter and ``"offline"`` on exit to
        ``{topic_base}/{name}/availability``.  Clears retained state
        on exit by publishing an empty payload to the state topic.

        Args:
            name: Sub-entity name (single MQTT topic level).

        Yields:
            A :class:`SubEntityContext` scoped to the sub-entity's topics.

        Raises:
            ValueError: If the name fails validation.

        See Also:
            ADR-031 — Sub-entity context manager.
        """
        self._validate_sub_entity_name(name)
        self._active_sub_entities.add(name)
        sub = SubEntityContext(name=name, parent=self)
        avail_topic = f"{self._topic_base}/{name}/availability"
        try:
            await self._mqtt.publish(avail_topic, "online", retain=True, qos=1)
        except Exception:
            self._active_sub_entities.discard(name)
            raise
        try:
            yield sub
        finally:
            try:
                state_topic = f"{self._topic_base}/{name}/state"
                await self._mqtt.publish(state_topic, "", retain=True, qos=1)
                await self._mqtt.publish(avail_topic, "offline", retain=True, qos=1)
            finally:
                self._active_sub_entities.discard(name)

    # -- Command registration -----------------------------------------------

    @overload
    def on_command(
        self,
        handler_or_sub_topic: CommandHandler,
        /,
    ) -> CommandHandler: ...

    @overload
    def on_command(
        self,
        handler_or_sub_topic: str | None = ...,
        /,
    ) -> Callable[[CommandHandler], CommandHandler]: ...

    def on_command(
        self,
        handler_or_sub_topic: CommandHandler | str | None = None,
        /,
    ) -> CommandHandler | Callable[[CommandHandler], CommandHandler]:
        """Register a command handler for this device.

        Supports three call patterns:

        1. Decorator — root handler::

            @ctx.on_command
            async def handle(sub_topic: str | None, payload: str) -> None: ...

        2. Direct call — root handler::

            ctx.on_command(handle)

        3. Decorator factory — sub-topic handler::

            @ctx.on_command("calibrate")
            async def handle_cal(sub_topic: str | None, payload: str) -> None: ...

        Handlers may also accept a single :class:`Command` argument
        (new-style), detected automatically via type annotation::

            @ctx.on_command
            async def handle(cmd: Command) -> None: ...

        Raises:
            RuntimeError: If a handler is already registered for the same
                sub-topic, or if ``commands()`` is active and a root
                handler is being registered.
            ValueError: If the sub-topic string is empty or contains
                ``/``, ``+``, or ``#``.

        Returns:
            The handler unchanged when called with a callable, or a
            decorator function when called with a sub-topic string or None.
        """

        def _register(
            handler: CommandHandler,
            sub_topic: str | None,
        ) -> CommandHandler:
            if sub_topic is None and self._commands_consumed:
                msg = (
                    f"Cannot register on_command — commands() iterator already "
                    f"active for device '{self._name}'"
                )
                raise RuntimeError(msg)
            if sub_topic in self._command_handlers:
                label = f"sub-topic '{sub_topic}'" if sub_topic else "root"
                msg = (
                    f"Command handler already registered for {label} "
                    f"on device '{self._name}'"
                )
                raise RuntimeError(msg)
            self._command_handlers[sub_topic] = handler
            return handler

        # --- callable → register as root handler immediately ---
        if callable(handler_or_sub_topic):
            return _register(handler_or_sub_topic, None)

        # --- string → validate and return decorator for that sub-topic ---
        if isinstance(handler_or_sub_topic, str):
            if not handler_or_sub_topic:
                msg = "Sub-topic must not be empty"
                raise ValueError(msg)
            _invalid = set(handler_or_sub_topic) & {"/", "+", "#"}
            if _invalid:
                msg = (
                    f"Sub-topic contains invalid MQTT characters "
                    f"{_invalid}: '{handler_or_sub_topic}'"
                )
                raise ValueError(msg)
            sub = handler_or_sub_topic

            def _decorator(handler: CommandHandler) -> CommandHandler:
                return _register(handler, sub)

            return _decorator

        # --- None → return decorator for root handler ---
        def _root_decorator(handler: CommandHandler) -> CommandHandler:
            return _register(handler, None)

        return _root_decorator

    # -- Command queue helpers ----------------------------------------------

    async def _await_command(self, timeout: float | None) -> Command | None:
        """Race ``queue.get()`` against shutdown, with optional timeout.

        Returns the :class:`Command` if one arrived, or ``None`` for
        shutdown / timeout.  Mirrors the ``asyncio.wait(FIRST_COMPLETED)``
        pattern used by :meth:`sleep`.
        """
        get_task = asyncio.ensure_future(self._command_queue.get())
        shutdown_task = asyncio.ensure_future(self._shutdown_event.wait())
        race: set[asyncio.Task[object]] = {get_task, shutdown_task}
        if timeout is not None:
            race.add(asyncio.ensure_future(asyncio.sleep(timeout)))

        done, pending = await asyncio.wait(race, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await t

        if get_task in done:
            return get_task.result()
        return None

    # -- Command iterator ---------------------------------------------------

    def commands(
        self,
        timeout: float | None = None,
    ) -> AsyncIterator[Command | None]:
        """Async iterator that yields inbound commands from the internal queue.

        Provides a queue-backed async iterator for ``@app.device`` loops,
        eliminating the need for manual ``asyncio.Queue`` bridges.

        When *timeout* is provided, yields ``None`` on timeout expiry,
        enabling periodic-work patterns::

            async for cmd in ctx.commands(timeout=5):
                if cmd is None:
                    await periodic_check()
                else:
                    await process(cmd.payload)

        Without *timeout*, blocks until a command arrives or shutdown is
        requested. Shutdown is detected immediately via event racing.

        Args:
            timeout: Seconds to wait for a command before yielding None.
                When None (default), blocks indefinitely until a command
                arrives or shutdown is requested.

        Yields:
            Command when a command arrives, or None on timeout expiry.

        Raises:
            RuntimeError: If called more than once on the same context,
                or if a command handler is already registered via
                :meth:`on_command`.

        See Also:
            ADR-025 — Command channel and sub-topic routing.
        """
        if self._commands_consumed:
            msg = f"commands() already active for device '{self._name}'"
            raise RuntimeError(msg)
        if None in self._command_handlers:
            msg = (
                f"Cannot use commands() — on_command handler already "
                f"registered for device '{self._name}'"
            )
            raise RuntimeError(msg)
        self._commands_consumed = True

        async def _iter() -> AsyncIterator[Command | None]:
            while not self.shutdown_requested:
                result = await self._await_command(timeout)
                if result is not None:
                    yield result
                elif self.shutdown_requested:
                    break
                elif timeout is not None:
                    yield None
            # Drain any commands that arrived before/during shutdown
            while not self._command_queue.empty():
                yield self._command_queue.get_nowait()

        return _iter()

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
# SubEntityContext
# ---------------------------------------------------------------------------


class SubEntityContext:
    """Context for a sub-entity within a device.

    Provides scoped MQTT publishing for a sub-entity's topic namespace.
    Created via :meth:`DeviceContext.sub_entity` context manager — not
    instantiated directly by user code.

    See Also:
        ADR-031 — Sub-entity context manager.
    """

    __slots__ = ("name", "parent")

    def __init__(self, *, name: str, parent: DeviceContext) -> None:
        self.name = name
        self.parent = parent

    async def publish_state(
        self,
        payload: dict[str, object],
        *,
        retain: bool = True,
    ) -> None:
        """Publish sub-entity state to ``{device}/{name}/state`` as JSON.

        Args:
            payload: Dict to serialise as JSON.
            retain: Whether the message should be retained (default True).
        """
        topic = f"{self.parent._topic_base}/{self.name}/state"
        await self.parent._mqtt.publish(topic, dumps(payload), retain=retain, qos=1)

    def on_command(
        self,
        handler: CommandHandler,
    ) -> CommandHandler:
        """Register a command handler for this sub-entity's sub-topic.

        Delegates to the parent device's :meth:`~DeviceContext.on_command`
        with this sub-entity's name as the sub-topic.

        Args:
            handler: Async callable to handle inbound commands.

        Returns:
            The handler, unchanged.
        """
        return self.parent.on_command(self.name)(handler)


# ---------------------------------------------------------------------------
# AppContext
# ---------------------------------------------------------------------------


class AppContext:
    """Context for the application lifespan.

    Provided to the lifespan async context manager registered via
    ``App(lifespan=...)``.  Offers access to settings and adapter
    resolution but NOT per-device features (no publish, no on_command,
    no sleep).

    See Also:
        ADR-001 — Framework architecture (lifespan).
    """

    def __init__(
        self,
        *,
        settings: Settings,
        adapters: dict[type, object],
    ) -> None:
        """Initialise lifecycle-hook context.

        Args:
            settings: Application settings instance.
            adapters: Resolved adapter registry mapping port types to instances.
        """
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
