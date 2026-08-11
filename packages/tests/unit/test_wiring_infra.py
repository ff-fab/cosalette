"""Tests for cosalette._wiring._infra — service construction.

Focuses on ``create_services`` merging the app-provided ``error_type_map``
into the ErrorPublisher's map while keeping framework command exceptions
authoritative (LEAK-01 targeted opt-in; ADR-011).

Test Techniques Used:
    - Specification-based Testing: merge contract and precedence
    - Equivalence Partitioning: no map / disjoint map / conflicting map
"""

from __future__ import annotations

import pytest

from cosalette._errors import ErrorPublisher
from cosalette._runners._command_runner import (
    _FRAMEWORK_ERROR_TYPE_MAP,
    InvalidJsonError,
)
from cosalette._wiring._infra import create_services
from cosalette.testing import FakeClock, MockMqttClient

pytestmark = pytest.mark.unit


class _DomainError(Exception):
    """Stand-in for an app-owned domain exception."""


def _make_services(
    error_type_map: dict[type[Exception], str] | None = None,
) -> ErrorPublisher:
    _, publisher = create_services(
        MockMqttClient(),
        "testapp",
        "1.0.0",
        FakeClock(),
        error_type_map=error_type_map,
    )
    return publisher


class TestCreateServicesErrorTypeMap:
    """create_services merges app + framework error type maps."""

    def test_defaults_to_framework_map_only(self) -> None:
        """No app map → publisher carries exactly the framework map."""
        publisher = _make_services(None)

        assert publisher.error_type_map == dict(_FRAMEWORK_ERROR_TYPE_MAP)

    def test_app_entries_extend_the_map(self) -> None:
        """App-owned types are added alongside the framework types."""
        publisher = _make_services({_DomainError: "domain_error"})

        assert publisher.error_type_map[_DomainError] == "domain_error"
        # Framework entries remain present.
        for exc_type, type_str in _FRAMEWORK_ERROR_TYPE_MAP.items():
            assert publisher.error_type_map[exc_type] == type_str

    def test_framework_entries_are_authoritative(self) -> None:
        """An app cannot override a framework command exception mapping."""
        publisher = _make_services({InvalidJsonError: "app_override"})

        assert publisher.error_type_map[InvalidJsonError] == "invalid_json"

    def test_app_map_is_copied_not_aliased(self) -> None:
        """Mutating the caller's map afterwards does not affect the publisher."""
        app_map: dict[type[Exception], str] = {_DomainError: "domain_error"}
        publisher = _make_services(app_map)

        app_map[_DomainError] = "mutated"

        assert publisher.error_type_map[_DomainError] == "domain_error"
