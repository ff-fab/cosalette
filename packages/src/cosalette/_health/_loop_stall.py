"""Opt-in event-loop stall watchdog (ADR-088).

When the ``COSALETTE_LOOP_STALL_TIMEOUT`` environment variable names a
number of seconds, a daemon thread watches a timestamp that a timer callback
on the event loop refreshes.  Once the loop has not refreshed it for longer
than the timeout, the thread writes a CRITICAL line and every thread's stack
to fd 2 and calls :func:`os._exit` with :data:`EXIT_LOOP_STALL`.  A restart
policy then restarts the process, and the broker publishes the LWT because
the MQTT client never disconnects cleanly.

The thread needs the GIL, which a loop stuck in C code may hold.  The same
callback therefore re-arms :func:`faulthandler.dump_traceback_later` as a
backstop: it fires after twice the timeout and exits with code ``1``.
"""

from __future__ import annotations

import asyncio
import faulthandler
import math
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from cosalette._constants import EXIT_LOOP_STALL

__all__ = [
    "LOOP_STALL_ENV",
    "LoopStallConfigError",
    "LoopStallWatchdog",
    "loop_stall_timeout_from_env",
]

LOOP_STALL_ENV = "COSALETTE_LOOP_STALL_TIMEOUT"
MAX_BEAT_INTERVAL = 5.0
"""Upper bound, in seconds, on the beat and check interval (timeout / 4)."""
BACKSTOP_FACTOR = 2.0
"""The faulthandler backstop fires after this many timeouts without a beat."""
_STDERR_FD = 2


class LoopStallConfigError(ValueError):
    """``COSALETTE_LOOP_STALL_TIMEOUT`` is not a positive number of seconds.

    :meth:`App.cli` maps it to exit code ``1`` (configuration error).
    """


def loop_stall_timeout_from_env() -> float | None:
    """Return the stall timeout from the environment, or ``None`` (off).

    Raises:
        LoopStallConfigError: The variable is set but is not a finite
            number greater than zero.
    """
    raw = os.environ.get(LOOP_STALL_ENV, "").strip()
    if not raw:
        return None
    try:
        timeout = float(raw)
    except ValueError:
        timeout = math.nan
    try:
        backstop_timeout = timeout * BACKSTOP_FACTOR
        # dump_traceback_later converts seconds to a C time_t deadline. Keep
        # this bound comfortably below the platform range to avoid overflow.
        max_timer_timeout = float((1 << 31) - 1)
        representable = math.isfinite(backstop_timeout) and (
            backstop_timeout <= max_timer_timeout
        )
    except OverflowError:
        representable = False
    if not (math.isfinite(timeout) and timeout > 0 and representable):
        msg = (
            f"{LOOP_STALL_ENV} must be a positive number of seconds whose "
            f"backstop timer is representable, got {raw!r}"
        )
        raise LoopStallConfigError(msg)
    return timeout


def exit_on_stall(stalled_for: float, timeout: float) -> None:
    """Dump every thread's stack to fd 2 and exit with :data:`EXIT_LOOP_STALL`.

    Writes with :func:`os.write`, not :mod:`logging`: a loop wedged inside a
    log handler holds that handler's lock.
    """
    line = (
        f"CRITICAL cosalette: event loop stalled for {stalled_for:.1f}s "
        f"(limit {timeout:g}s); exiting with code {EXIT_LOOP_STALL}\n"
    )
    os.write(_STDERR_FD, line.encode())
    faulthandler.dump_traceback(file=_STDERR_FD, all_threads=True)
    os._exit(EXIT_LOOP_STALL)


@dataclass
class LoopStallWatchdog:
    """Exit the process when the event loop stops running for *timeout* s.

    :meth:`start` must be called on the running loop.  :meth:`stop` is
    idempotent and safe to call from the loop at any time.
    """

    timeout: float
    clock: Callable[[], float] = time.monotonic
    on_stall: Callable[[float, float], None] = exit_on_stall
    backstop: bool = True
    _last_beat: float = field(default=0.0, init=False, repr=False)
    _stopped: threading.Event = field(
        default_factory=threading.Event, init=False, repr=False
    )
    _timer: asyncio.TimerHandle | None = field(default=None, init=False, repr=False)

    @property
    def interval(self) -> float:
        """Seconds between loop beats and between stall checks."""
        return min(self.timeout / 4, MAX_BEAT_INTERVAL)

    def start(self) -> None:
        """Begin beating on the running loop and start the watcher thread."""
        loop = asyncio.get_running_loop()

        def _tick() -> None:
            self.beat()
            self._timer = loop.call_later(self.interval, _tick)

        _tick()
        threading.Thread(
            target=self._watch, name="cosalette-stall", daemon=True
        ).start()

    def beat(self) -> None:
        """Record that the loop is running and re-arm the backstop."""
        self._last_beat = self.clock()
        if self.backstop:
            faulthandler.dump_traceback_later(
                self.timeout * BACKSTOP_FACTOR, exit=True, file=_STDERR_FD
            )

    def check(self) -> bool:
        """Call *on_stall* and return ``True`` if the last beat is too old."""
        stalled_for = self.clock() - self._last_beat
        if self._stopped.is_set() or stalled_for <= self.timeout:
            return False
        self.on_stall(stalled_for, self.timeout)
        return True

    def stop(self) -> None:
        """Disarm: stop beating, end the thread and cancel the backstop."""
        if self._stopped.is_set():
            return
        self._stopped.set()
        if self._timer is not None:
            self._timer.cancel()
        if self.backstop:
            faulthandler.cancel_dump_traceback_later()

    def _watch(self) -> None:
        while not self._stopped.wait(self.interval):
            if self.check():
                return
