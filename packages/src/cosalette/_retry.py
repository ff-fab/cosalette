"""Retry and backoff strategies for telemetry polling.

See ADR-024 for design rationale.
"""

from __future__ import annotations

import random
from typing import Protocol, override, runtime_checkable


@runtime_checkable
class BackoffStrategy(Protocol):
    """Backoff-delay contract for telemetry retry.

    The framework calls ``delay(attempt)`` between retry attempts.
    Attempt numbers are 1-based.
    """

    def delay(self, attempt: int) -> float:
        """Return delay in seconds for the given attempt (1-based)."""
        ...


class ExponentialBackoff:
    """Exponential backoff with ±20% jitter.

    ``min(base * 2^(attempt-1), max_delay)``
    """

    __slots__ = ("_base", "_max_delay")

    def __init__(self, base: float = 2.0, max_delay: float = 60.0) -> None:
        self._base = base
        self._max_delay = max_delay

    def delay(self, attempt: int) -> float:
        raw: float = min(self._base * (2 ** (attempt - 1)), self._max_delay)
        return raw * random.uniform(0.8, 1.2)  # noqa: S311  # jitter, not cryptographic

    @override
    def __repr__(self) -> str:
        return f"ExponentialBackoff(base={self._base}, max_delay={self._max_delay})"


class LinearBackoff:
    """Linear backoff: ``min(step * attempt, max_delay)`` with ±20% jitter."""

    __slots__ = ("_step", "_max_delay")

    def __init__(self, step: float = 2.0, max_delay: float = 60.0) -> None:
        self._step = step
        self._max_delay = max_delay

    def delay(self, attempt: int) -> float:
        raw = min(self._step * attempt, self._max_delay)
        return raw * random.uniform(0.8, 1.2)  # noqa: S311  # jitter, not cryptographic

    @override
    def __repr__(self) -> str:
        return f"LinearBackoff(step={self._step}, max_delay={self._max_delay})"


class FixedBackoff:
    """Fixed backoff: constant delay with ±20% jitter."""

    __slots__ = ("_delay",)

    def __init__(self, delay: float = 5.0) -> None:
        self._delay = delay

    def delay(self, attempt: int) -> float:  # noqa: ARG002
        return self._delay * random.uniform(0.8, 1.2)  # noqa: S311  # jitter, not cryptographic

    @override
    def __repr__(self) -> str:
        return f"FixedBackoff(delay={self._delay})"


_DEFAULT_BACKOFF = ExponentialBackoff(base=2.0, max_delay=60.0)
"""Default backoff used when retry > 0 and no explicit backoff is provided."""

_DEFAULT_RETRY_ON: tuple[type[BaseException], ...] = (OSError,)
"""Default exception types to retry on."""


class CircuitBreaker:
    """Optional circuit breaker for telemetry retry.

    Tracks consecutive failed cycles (where all retries were exhausted).
    After ``threshold`` consecutive failures, the circuit opens and
    the handler is skipped until a half-open probe succeeds.
    """

    __slots__ = ("_consecutive_failures", "_state", "_threshold")

    def __init__(self, threshold: int = 5) -> None:
        if not isinstance(threshold, int) or threshold < 1:
            msg = "threshold must be a positive integer (>= 1)"
            raise ValueError(msg)
        self._threshold = threshold
        self._consecutive_failures = 0
        self._state: str = "closed"  # closed | open | half-open

    @property
    def state(self) -> str:
        return self._state

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    @property
    def threshold(self) -> int:
        return self._threshold

    def record_success(self) -> None:
        """Record a successful handler execution. Resets all state."""
        self._consecutive_failures = 0
        self._state = "closed"

    def record_failure(self) -> None:
        """Record a cycle where all retries were exhausted."""
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._threshold:
            self._state = "open"

    def should_attempt(self) -> bool:
        """Return True if the handler should execute this cycle.

        - closed: always True
        - open: skip this cycle, transition to half-open for next cycle
        - half-open: True (probe attempt)
        """
        if self._state == "closed":
            return True
        if self._state == "open":
            # Skip this cycle, but transition to half-open for the NEXT cycle
            self._state = "half-open"
            return False
        # half-open: probe attempt
        return True

    @override
    def __repr__(self) -> str:
        return f"CircuitBreaker(threshold={self._threshold})"
