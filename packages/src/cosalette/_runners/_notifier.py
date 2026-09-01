"""In-process trigger arming for locally-triggerable telemetry entities.

Provides :class:`EntityNotifier`, the DI-injectable handle that wakes an
expanded ``@app.telemetry`` entity because *something happened* — a
hardware push landing in an adapter, a domain event, a serial frame —
instead of waiting for the next ``interval=`` tick.

The notifier is a **stable handle**: the framework creates it during
bootstrap (Phase 1) so that ``@app.state`` factories and adapter
factories can receive it, and late-binds it to the per-entity trigger
slots once they exist (Phase 2, after name expansion and
``TriggerConfig.build``).  Arming before that bind raises
:exc:`NotifierNotReadyError` rather than silently doing nothing.

See Also:
    ADR-064 — Local (in-process) trigger source for triggerable telemetry.
    ADR-042 — ``thread_safe`` / ``call_soon_threadsafe`` precedent.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cosalette._runners._telemetry_types import _TriggerSlot

logger = logging.getLogger(__name__)


class EntityNotifierError(RuntimeError):
    """Base class for :class:`EntityNotifier` arming failures."""


class NotifierNotReadyError(EntityNotifierError):
    """Raised when the notifier is armed before its trigger slots exist.

    Trigger slots are created in lifecycle Phase 2, after ``@app.state``
    factories and adapter factories have run.  Calling the notifier from
    inside a factory body — rather than storing it and calling it later,
    once the app is running — hits this error.
    """


class UnknownEntityError(EntityNotifierError):
    """Raised when a name is not a locally-triggerable telemetry entity.

    The name may be unknown entirely (a typo, or a stale name from
    before ``name=`` expansion), or it may exist but not declare
    ``triggerable="local"`` / ``triggerable="both"``.
    """


class EntityNotifier:
    """Wake a locally-triggerable telemetry entity by expanded name.

    Injected by type into any handler, ``@app.state`` factory, adapter
    factory or ``on_configure`` hook that declares an ``EntityNotifier``
    parameter.  Calling it arms the entity's trigger slot; the telemetry
    runner is already racing that slot against its ``interval=`` sleep,
    so the handler runs immediately through the **identical** publish
    cycle used by a scheduled tick (``publish=``, ``state_model=``,
    availability, persistence, error publication).

    Arming is *coalescing*: repeated calls before the handler runs
    collapse into a single out-of-cycle run.

    The call is safe from any OS thread.  When invoked from the event
    loop's own thread the slot is armed inline; from any other thread
    the arm is marshalled with
    :meth:`~asyncio.AbstractEventLoop.call_soon_threadsafe` (ADR-042).
    The name is always validated in the calling thread, so a bad name
    raises there rather than disappearing into the loop.

    Examples:
        Handing the notifier to an adapter that pushes off-loop::

            @app.state
            def shared(notify: EntityNotifier) -> SharedState:
                return SharedState(notify=notify)

            class WizBulbAdapter:
                def _on_push(self, ip: str) -> None:   # UDP thread
                    self._notify(self._name_for(ip))

    Raises:
        NotifierNotReadyError: If armed before the framework binds the
            trigger slots (lifecycle Phase 2).
        UnknownEntityError: If *entity_name* is not a telemetry entity
            declaring a local trigger source.
    """

    __slots__ = ("_loop", "_loop_thread_id", "_slots")

    def __init__(self) -> None:
        self._slots: dict[str, _TriggerSlot] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread_id: int = -1

    def _bind(self, slots: dict[str, _TriggerSlot]) -> None:
        """Late-bind the per-entity trigger slots (framework use only).

        Called from the event loop thread in lifecycle Phase 2, once
        ``TriggerConfig.build`` has created a slot per expanded name.
        """
        self._loop = asyncio.get_running_loop()
        self._loop_thread_id = threading.get_ident()
        self._slots = slots

    @property
    def entities(self) -> frozenset[str]:
        """Expanded names this notifier can wake (empty before binding)."""
        return frozenset(self._slots or ())

    def __call__(self, entity_name: str) -> None:
        """Arm *entity_name* so its handler runs at the next opportunity."""
        slot = self._require_slot(entity_name)
        if threading.get_ident() == self._loop_thread_id:
            slot.arm_local()
            return
        assert self._loop is not None  # noqa: S101  # set together with _slots
        try:
            self._loop.call_soon_threadsafe(slot.arm_local)
        except RuntimeError:
            # The loop closed between the bind and this off-loop call —
            # a push callback outliving app shutdown.  Nothing to wake.
            logger.debug("Notify for '%s' dropped: event loop is closed", entity_name)

    def _require_slot(self, entity_name: str) -> _TriggerSlot:
        """Return the slot for *entity_name* or raise a named error."""
        if self._slots is None:
            msg = (
                f"EntityNotifier armed for {entity_name!r} before the "
                f"framework bound its trigger slots.  Store the notifier "
                f"in your state/adapter and call it once the app is "
                f"running, not from the factory body itself."
            )
            raise NotifierNotReadyError(msg)
        slot = self._slots.get(entity_name)
        if slot is None:
            known = ", ".join(sorted(self._slots)) or "(none)"
            msg = (
                f"{entity_name!r} is not a locally-triggerable telemetry "
                f"entity.  Add triggerable='local' (or 'both') to its "
                f"@app.telemetry registration and use the expanded name.  "
                f"Known: {known}"
            )
            raise UnknownEntityError(msg)
        return slot
