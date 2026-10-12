"""Tests for cosalette._wiring._infra — service construction.

Focuses on ``create_services`` merging the app-provided ``error_type_map``
into the ErrorPublisher's map while keeping framework command exceptions
authoritative (LEAK-01 targeted opt-in; ADR-011), and warning at startup
when a framework entry shadows an app entry with a different label.

Test Techniques Used:
    - Specification-based Testing: merge contract and precedence
    - Equivalence Partitioning: no map / disjoint map / conflicting map;
      shadow warning for different-label / same-label / disjoint / no map
"""

from __future__ import annotations

import logging

import pytest

from cosalette import PayloadValidationError
from cosalette._errors import ErrorPublisher
from cosalette._runners._command_errors import (
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

    def test_payload_validation_error_is_framework_labelled(self) -> None:
        """Rejected typed-command input carries the framework label.

        Technique: Specification-based — ADR-011 amendment (2026-10-12).
        """
        publisher = _make_services(None)

        assert publisher.error_type_map[PayloadValidationError] == "invalid_payload"

    def test_app_map_is_copied_not_aliased(self) -> None:
        """Mutating the caller's map afterwards does not affect the publisher."""
        app_map: dict[type[Exception], str] = {_DomainError: "domain_error"}
        publisher = _make_services(app_map)

        app_map[_DomainError] = "mutated"

        assert publisher.error_type_map[_DomainError] == "domain_error"


_INFRA_LOGGER = "cosalette._wiring"


class TestShadowedAppEntryWarning:
    """A framework entry overriding an app entry warns once at startup."""

    def test_different_label_warns_once_naming_type_and_labels(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Technique: Equivalence Partitioning — overlap with a different label."""
        # Arrange
        app_map: dict[type[Exception], str] = {
            PayloadValidationError: "validation",
            _DomainError: "domain_error",
        }

        # Act
        with caplog.at_level(logging.WARNING, logger=_INFRA_LOGGER):
            _make_services(app_map)

        # Assert
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert [r.getMessage() for r in warnings] == [
            "error_type_map entry PayloadValidationError -> 'validation' is "
            "overridden by the framework label 'invalid_payload'"
        ]

    @pytest.mark.parametrize(
        "app_map",
        [
            pytest.param({PayloadValidationError: "invalid_payload"}, id="same"),
            pytest.param({_DomainError: "domain_error"}, id="disjoint"),
            pytest.param(None, id="none"),
        ],
    )
    def test_no_conflict_stays_silent(
        self,
        app_map: dict[type[Exception], str] | None,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Technique: Equivalence Partitioning — same label, no overlap, no map."""
        with caplog.at_level(logging.WARNING, logger=_INFRA_LOGGER):
            _make_services(app_map)

        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
