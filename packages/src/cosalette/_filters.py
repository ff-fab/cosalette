"""Signal filter protocol.

Defines the structural typing contract that all filter implementations
must satisfy.  Concrete implementations live in ``cosalette-filters-rs``
(Rust/pyo3).  See ADR-014 for design rationale and ADR-022 for the
decision to make Rust the sole backend.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Filter(Protocol):
    """Signal filter contract.

    All filters follow the ``update → value`` pattern:

    1. Call ``update(raw)`` with each new measurement.
    2. The return value is the filtered output.
    3. Access ``value`` for the current filtered state.
    4. Call ``reset()`` to clear internal state.

    The first ``update()`` call seeds the filter — it returns the raw
    value unchanged (no history to smooth against).
    """

    @property
    def value(self) -> float | None:
        """Current filtered value, or ``None`` before the first update."""
        ...

    def update(self, raw: float) -> float:
        """Feed a raw measurement and return the filtered value."""
        ...

    def reset(self) -> None:
        """Clear internal state so the next update re-seeds."""
        ...
