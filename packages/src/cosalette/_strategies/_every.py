"""Time-based and count-based publish throttle strategy."""

from __future__ import annotations

from cosalette._clock import ClockPort
from cosalette._strategies._base import _StrategyBase


class Every(_StrategyBase):
    """Time-based or count-based publish throttle.

    Exactly **one** of ``seconds`` or ``n`` must be provided.

    ``Every(seconds=30)``
        Publish at most once every 30 seconds.  Requires a
        :class:`ClockPort` injected via ``_bind()``.  Before binding,
        ``should_publish`` always returns ``True`` (safe fallback).

    ``Every(n=5)``
        Publish every 5th reading.  No clock dependency.

    Raises:
        ValueError: If both, neither, or non-positive values are given.
    """

    def __init__(
        self,
        *,
        seconds: float | None = None,
        n: int | None = None,
    ) -> None:
        if seconds is not None and n is not None:
            msg = "Specify exactly one of 'seconds' or 'n', not both"
            raise ValueError(msg)
        if seconds is None and n is None:
            msg = "Specify exactly one of 'seconds' or 'n'"
            raise ValueError(msg)

        if seconds is not None and seconds <= 0:
            msg = "'seconds' must be positive"
            raise ValueError(msg)
        if n is not None and n <= 0:
            msg = "'n' must be positive"
            raise ValueError(msg)

        self._seconds = seconds
        self._n = n

        # Time-mode state
        self._clock: ClockPort | None = None
        self._last_publish_time: float | None = None

        # Count-mode state
        self._counter: int = 0

    # -- clock injection ----------------------------------------------------

    def _bind(self, clock: ClockPort) -> None:
        """Inject a clock for time-based throttling."""
        self._clock = clock
        self._last_publish_time = clock.now()

    # -- protocol -----------------------------------------------------------

    def should_publish(
        self,
        current: dict[str, object],  # noqa: ARG002
        previous: dict[str, object] | None,  # noqa: ARG002
    ) -> bool:
        """Return ``True`` when enough time/calls have elapsed."""
        if self._seconds is not None:
            return self._should_publish_time()
        return self._should_publish_count()

    def on_published(self) -> None:
        """Record publish timestamp or reset counter."""
        if self._seconds is not None:
            if self._clock is not None:
                self._last_publish_time = self._clock.now()
        else:
            self._counter = 0

    def __repr__(self) -> str:
        if self._seconds is not None:
            return f"Every(seconds={self._seconds!r})"
        return f"Every(n={self._n!r})"

    # -- internals ----------------------------------------------------------

    def _should_publish_time(self) -> bool:
        """Time-mode: check elapsed seconds since last publish."""
        if self._clock is None:
            # Not yet bound — safe fallback: always publish.
            return True
        assert self._seconds is not None  # guarded by constructor  # noqa: S101
        assert self._last_publish_time is not None  # set in _bind  # noqa: S101
        elapsed = self._clock.now() - self._last_publish_time
        return elapsed >= self._seconds

    def _should_publish_count(self) -> bool:
        """Count-mode: increment counter and check threshold."""
        self._counter += 1
        assert self._n is not None  # guarded by constructor  # noqa: S101
        return self._counter >= self._n
