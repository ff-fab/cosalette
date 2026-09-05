"""DeviceContext for per-device runtime contexts.

Provides the main DeviceContext class and the _seconds_until helper function
for wall-clock scheduling.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import logging
from collections.abc import AsyncIterator, Callable, Iterable, Mapping, Sequence
from types import MappingProxyType
from typing import TYPE_CHECKING, cast, overload

from cosalette._clock import ClockPort
from cosalette._command import Command
from cosalette._health._reporter import HealthReporter
from cosalette._mqtt import CommandHandler, MqttPort
from cosalette._runners._contracts import validate_state_payload
from cosalette._runners._stream_types import BackpressurePolicy, apply_backpressure
from cosalette._settings import Settings
from cosalette._utils import _DEFAULT_COMMAND_TIMEOUT

if TYPE_CHECKING:
    from cosalette._context._sub_entity_context import SubEntityContext

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
    now = (
        datetime.datetime.now(tz=tz)
        if tz is not None
        else datetime.datetime.now().astimezone()
    )
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


async def _cancel_and_drain(tasks: Iterable[asyncio.Task[object]]) -> None:
    """Cancel *tasks* and await their unwinding.

    Suppresses only the :class:`asyncio.CancelledError` raised by the
    ``task.cancel()`` requested here.  A cancellation delivered to the
    *current* task from outside is re-raised, so a caller parked in the
    losing branch of a ``FIRST_COMPLETED`` race still unwinds on shutdown
    instead of returning normally.
    """
    tasks = list(tasks)
    for task in tasks:
        task.cancel()
    for task in tasks:
        try:
            await task
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if current is not None and current.cancelling() > 0:
                raise


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
        health_reporter: HealthReporter | None = None,
        state_model: type | None = None,
        handler_name: str | None = None,
        command_maxsize: int = 0,
        command_backpressure: BackpressurePolicy = "drop_newest",
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
            health_reporter: Optional health reporter for availability
                signalling.
            state_model: Declared state contract for this handler's static
                ``state`` topic.  When set, every :meth:`publish_state`
                payload is validated and normalised against it.  ``None``
                (default) skips validation entirely — no ``TypeAdapter`` is
                built and no per-publish cost is added.
            handler_name: Qualified handler name, used only to make
                validation errors actionable.
            command_maxsize: Maximum size for the device command queue.
                ``0`` (default) means unbounded.
            command_backpressure: Policy applied when ``command_maxsize > 0``
                and the command queue is full.

        See Also:
            ADR-045 (amended 2026-08-07) — ``state_model`` is runtime
            load-bearing for ``@app.stream`` and ``@app.device``.
        """
        self._name = name
        self._settings = settings
        self._mqtt = mqtt
        self._topic_prefix = topic_prefix
        self._shutdown_event = shutdown_event
        self._adapters = adapters
        self._clock = clock
        self._command_handlers: dict[str | None, CommandHandler] = {}
        self._command_timeouts: dict[str | None, float | None] = {}
        self._is_root = is_root
        self._command_backpressure = command_backpressure
        self._log_label = f"command for {name!r}"
        self._command_queue: asyncio.Queue[Command] = asyncio.Queue(
            maxsize=command_maxsize
        )
        self._commands_consumed: bool = False
        self._topic_base = topic_prefix if is_root else f"{topic_prefix}/{name}"
        self._active_sub_entities: set[str] = set()
        self._health_reporter = health_reporter
        self._is_unavailable: bool = False
        self._state_model = state_model
        self._handler_name = handler_name

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

    def get_command_timeout(self, sub_topic: str | None = None) -> float | None:
        """Look up the watchdog bound for a sub-topic handler (or root).

        Returns ``_DEFAULT_COMMAND_TIMEOUT`` for handlers registered
        without an explicit ``timeout=`` (the dict is populated at
        registration time, so absence means pre-registration lookup).
        """
        return self._command_timeouts.get(sub_topic, _DEFAULT_COMMAND_TIMEOUT)

    # -- MQTT publishing ----------------------------------------------------

    async def publish_state(
        self,
        payload: dict[str, object],
        *,
        retain: bool = True,
    ) -> None:
        """Publish device state to ``{prefix}/{device}/state`` as JSON.

        For root devices (unnamed), publishes to ``{prefix}/state`` instead.

        This is the primary publication method for device state.
        The payload dict is JSON-serialised automatically.

        When the owning ``@app.stream`` or ``@app.device`` registration
        declared ``state_model=``, *payload* is validated and normalised
        against that model before publishing (ADR-046).  Without
        ``state_model`` the payload is published unchanged and no
        validation cost is incurred.

        Args:
            payload: Dict to serialise as JSON.
            retain: Whether the message should be retained (default True).

        Raises:
            ReturnValidationError: If ``state_model`` was declared and
                *payload* does not conform to it.  The message names the
                offending fields, the model, and the handler.
        """
        if self._state_model is not None:
            payload = validate_state_payload(
                payload,
                self._state_model,
                handler=self._handler_name,
            )
        topic = f"{self._topic_base}/state"
        await self._mqtt.publish(topic, payload, retain=retain, qos=1)

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

        await _cancel_and_drain(pending)

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
        from cosalette._context._sub_entity_context import SubEntityContext

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
        *,
        timeout: float | None = ...,
    ) -> CommandHandler: ...

    @overload
    def on_command(
        self,
        handler_or_sub_topic: str | None = ...,
        /,
        *,
        timeout: float | None = ...,
    ) -> Callable[[CommandHandler], CommandHandler]: ...

    def on_command(
        self,
        handler_or_sub_topic: CommandHandler | str | None = None,
        /,
        *,
        timeout: float | None = _DEFAULT_COMMAND_TIMEOUT,
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

        Args:
            handler_or_sub_topic: The handler itself (direct/decorator
                use) or the sub-topic string to bind the returned
                decorator to.
            timeout: Per-invocation watchdog in seconds (ADR-060).
                Defaults to ``_DEFAULT_COMMAND_TIMEOUT`` (30 s); pass
                ``None`` for unbounded execution. Unlike ``@app.command``,
                no Settings-derived callable form is supported here —
                context-level registration happens at runtime.

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
            self._command_timeouts[sub_topic] = timeout
            return handler

        # --- callable → register as root handler immediately ---
        if callable(handler_or_sub_topic):
            return _register(handler_or_sub_topic, None)  # ty: ignore[invalid-argument-type]

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
        await _cancel_and_drain(pending)

        if get_task in done:
            return get_task.result()
        return None

    def _enqueue_command(self, cmd: Command) -> None:
        """Enqueue command for commands() consumer, honoring backpressure."""
        apply_backpressure(
            self._command_queue,
            cmd,
            self._command_backpressure,
            log_label=self._log_label,
        )

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
            return cast(T, self._adapters[port_type])
        except KeyError:
            msg = f"No adapter registered for {port_type!r}"
            raise LookupError(msg) from None

    async def mark_unavailable(self) -> None:
        """Mark this device as transport-unavailable.

        Publishes ``"offline"`` to the device availability topic and sets
        an internal flag. The framework automatically publishes ``"online"``
        after the next successful command handler invocation.

        If no :class:`~cosalette._health._reporter.HealthReporter` is
        injected (e.g. in tests), this is a no-op.
        """
        if self._health_reporter is None:
            return
        self._is_unavailable = True
        await self._health_reporter.publish_device_unavailable(
            self._name, is_root=self._is_root
        )

    async def mark_available(self) -> None:
        """Mark this device as transport-available again.

        Mirrors :meth:`mark_unavailable`: publishes ``"online"`` to the
        device availability topic and clears the internal flag.

        Unlike command handlers, telemetry and device handlers do **not**
        auto-recover after a successful invocation (see ADR-047, which
        scopes auto-recovery to ``@app.command`` only). Callers using the
        ``@app.telemetry`` or ``@app.device`` archetypes must call this
        method explicitly to signal recovery.

        If no :class:`~cosalette._health._reporter.HealthReporter` is
        injected (e.g. in tests), this is a no-op.
        """
        if self._health_reporter is None:
            return
        await self._health_reporter.publish_device_available(
            self._name, is_root=self._is_root
        )
        self._is_unavailable = False
