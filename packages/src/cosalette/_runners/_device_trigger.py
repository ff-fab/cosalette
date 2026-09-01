"""In-process trigger waiting for triggerable ``@app.device`` handlers.

Provides :class:`DeviceTrigger`, the per-device handle a
``@app.device`` generator awaits instead of sleeping on a fixed
interval.  Where ``@app.telemetry`` owns its loop and the framework
races the trigger against ``interval=`` internally, a device handler
owns its own loop — so the framework hands it the same
:class:`~cosalette._runners._telemetry_types._TriggerSlot` to await
directly.

Both archetypes therefore share one trigger mechanism: the same slot,
the same coalescing, the same :class:`~cosalette.EntityNotifier` and
the same :class:`~cosalette.TriggerPayload`.

The framework does not own a device handler's loop, so the ADR-066
``min_interval=`` storm throttle is enforced *inside* :meth:`wait` —
the device-side twin of ``TelemetryRunner._sleep_or_trigger``.  Both
read the same two pure slot methods; arming itself stays a plain,
non-blocking ``event.set()``.

See Also:
    ADR-066 — Min-interval storm throttle for trigger-initiated runs.
    ADR-065 — Local trigger source for the device archetype.
    ADR-064 — Local (in-process) trigger source for triggerable telemetry.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, override

from cosalette._runners._asyncio_utils import _cancel_task
from cosalette._runners._trigger import TriggerPayload

if TYPE_CHECKING:
    from cosalette._clock import ClockPort
    from cosalette._runners._telemetry_types import _TriggerSlot


class DeviceTrigger:
    """Await the next in-process wake for one ``@app.device`` entity.

    Injected by type into a device handler that declares
    ``triggerable="local"``.  Each :meth:`wait` call blocks until an
    :class:`~cosalette.EntityNotifier` arms this device's slot, then
    returns the :class:`~cosalette.TriggerPayload` describing the wake.

    Arming is *coalescing*: notifications that land while the handler
    is busy collapse into a single pending wake, exactly as they do for
    triggerable telemetry.

    Examples:
        A drain loop that publishes when a frame arrives, with a
        heartbeat so the retained state topic is refreshed even if the
        hardware goes quiet::

            @app.device(name=_sensor_map, triggerable="local")
            async def sensor_entity(
                ctx: DeviceContext,
                cfg: SensorConfig,
                bus: SensorBus,
                trigger: DeviceTrigger,
            ) -> AsyncIterator[None]:
                while True:
                    await trigger.wait(timeout=60.0)
                    reading = bus.take(cfg.id)
                    if reading is not None:
                        await ctx.publish_state(reading.as_dict())
                    yield
    """

    __slots__ = ("_clock", "_name", "_slot")

    def __init__(self, slot: _TriggerSlot, name: str, clock: ClockPort) -> None:
        self._slot = slot
        self._name = name
        self._clock = clock

    @property
    def name(self) -> str:
        """Expanded device name this handle waits on."""
        return self._name

    @override
    def __repr__(self) -> str:
        return f"DeviceTrigger({self._name!r})"

    async def wait(self, timeout: float | None = None) -> TriggerPayload:
        """Block until this device is woken, or until *timeout* elapses.

        Args:
            timeout: Optional heartbeat bound in seconds.  ``None``
                (the default) waits indefinitely for a wake — use a
                timeout when the loop must also run on a fixed cadence,
                e.g. to refresh the retained state topic if the
                hardware never pushes again.

        Returns:
            :meth:`TriggerPayload.local` for a notifier wake, or
            :meth:`TriggerPayload.scheduled` when *timeout* elapsed
            first — so the handler can tell the two apart the same way
            a telemetry handler does.

        Note:
            With ``min_interval=`` set (ADR-066), a wake that lands
            inside a closed throttle window is held until the window
            reopens, and a *timeout* that expires first still returns
            :meth:`TriggerPayload.scheduled` **while that wake stays
            pending** — the next :meth:`wait` delivers it.  A handler
            that reads ``"scheduled"`` as "nothing arrived" is subtly
            wrong once ``min_interval`` is set.
        """
        deadline = None if timeout is None else self._clock.now() + timeout
        while True:
            if self._slot.event.is_set():
                payload = await self._consume_when_window_opens(deadline)
                if payload is not None:
                    return payload
                continue  # window slept out — re-check for a newer arm
            if deadline is None:
                await self._slot.event.wait()
                continue  # armed — re-enter the throttle gate
            if not await self._wake_before(deadline):
                return TriggerPayload.scheduled()
            # A wake landed; loop so the throttle gate runs before returning.

    async def _consume_when_window_opens(
        self, deadline: float | None
    ) -> TriggerPayload | None:
        """Gate an armed slot on ``min_interval`` (ADR-066).

        Returns the payload to hand back to the caller, or ``None`` when
        the throttle window was slept out and the caller should look at
        the slot again (a later arm may have coalesced in meanwhile).

        With ``min_interval`` unset, ``throttle_delay`` is always ``0.0``
        and this collapses to today's straight-line ``consume()``.
        """
        now = self._clock.now()
        delay = self._slot.throttle_delay(now)
        if delay <= 0.0:
            # For a device, returning the wake *is* the run start.
            self._slot.note_trigger_start(now)
            return self._slot.consume()
        remaining = None if deadline is None else max(0.0, deadline - now)
        if remaining is not None and remaining < delay:
            # The heartbeat wins; the arm stays pending for the next wait().
            await self._clock.sleep(remaining)
            return TriggerPayload.scheduled()
        await self._clock.sleep(delay)
        return None

    async def _wake_before(self, deadline: float) -> bool:
        """Race a wake against *deadline*. ``True`` when the wake won."""
        sleep_task = asyncio.create_task(
            self._clock.sleep(max(0.0, deadline - self._clock.now()))
        )
        wake_task = asyncio.create_task(self._slot.event.wait())
        try:
            done, _ = await asyncio.wait(
                (sleep_task, wake_task), return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            for task in (sleep_task, wake_task):
                if not task.done():
                    await _cancel_task(task)
        # A wake wins a tie: the notification must not be swallowed by a
        # timeout that landed in the same event-loop iteration.
        return wake_task in done
