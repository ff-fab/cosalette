"""Shared ADR-068 ``state_model=`` test contracts and warning helpers.

These models and the ``production_warning_filters`` context manager are used
by several independent suites (``test_state_model_enforcement``,
``test_state_model_path_agreement``, and the ``ai_content`` drift guards).
They live here so those suites depend on a shared fixture rather than
cross-importing each other's test modules.
"""

from __future__ import annotations

import contextlib
import warnings
from collections.abc import Iterator

from pydantic import BaseModel


class Reading(BaseModel):
    """All-required model — its dump shape is stable under exclude_none."""

    sensor: str
    value: float


class OptionalReading(BaseModel):
    """Model with an optional field — the clause C / clause D shape case."""

    sensor: str
    brightness: int | None = None


@contextlib.contextmanager
def production_warning_filters() -> Iterator[None]:
    """Run the body with warnings non-fatal, as they are in production.

    A suite's ``filterwarnings = ["error"]`` config would turn Pydantic's
    ``PydanticSerializationUnexpectedValue`` warning into an exception on its
    own, so the pre-0.9.0 fast path would appear to fail closed under pytest
    while silently republishing in production.  Every assertion about clause B
    must therefore be made under the default (non-raising) filters, or it
    proves nothing.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("always")
        yield
