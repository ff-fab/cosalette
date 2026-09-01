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

See Also:
    ADR-065 — Local trigger source for the device archetype.
    ADR-064 — Local (in-process) trigger source for triggerable telemetry.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, override

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
        """
        if timeout is None:
            await self._slot.event.wait()
            return self._slot.consume()

        sleep_task = asyncio.create_task(self._clock.sleep(timeout))
        wake_task = asyncio.create_task(self._slot.event.wait())
        try:
            done, _ = await asyncio.wait(
                (sleep_task, wake_task), return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            for task in (sleep_task, wake_task):
                if not task.done():
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
        # A wake wins a tie: the notification must not be swallowed by a
        # timeout that landed in the same event-loop iteration.
        if wake_task in done:
            return self._slot.consume()
        return TriggerPayload.scheduled()
