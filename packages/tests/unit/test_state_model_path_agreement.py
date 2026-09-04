"""Drift guard: normalize_return and validate_state_payload must agree.

ADR-045's 2026-08-07 amendment split published-state validation into two
functions on purpose — ``normalize_return`` (dump-first EAFP, telemetry and
command) and ``validate_state_payload`` (validate-first, device and stream) —
and flagged *"their differing dump/validate ordering is a subtlety future
maintainers must respect"* as a hazard.  ADR-068 clauses A–D make the two
converge: one rule, one accept/reject verdict, one output shape.  Nothing in
the code keeps them converged.  This module is that guard.

One parametrised matrix is run against **both** entry points and the two
outcomes are compared to each other, so a change to either function alone
fails here with the case name and both outputs in the message.

The single sanctioned asymmetry — a *model instance* whose optional field the
handler itself set to ``None`` — is pinned in
:class:`TestStateModelPathsDocumentedDivergence` rather than papered over.

Test Techniques Used:
    - Back-to-Back Testing: the two implementations of one documented rule are
      run on identical input and their outputs compared directly; the shared
      expectation table is the oracle, the cross-path comparison is the guard.
    - Equivalence Partitioning: value classes (conforming dict / non-conforming
      dict / model instance / dict with extra keys / optional field absent,
      present, explicitly null) crossed with both code paths.
    - Decision Table Testing: ``_PATH_AGREEMENT_MATRIX`` is the decision table;
      every row states one verdict that must hold on both paths.
    - Regression Testing: pins the ADR-068 clause C fast-path asymmetry so it
      cannot widen unnoticed.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest

from cosalette._runners._contracts import (
    ReturnValidationError,
    normalize_return,
    validate_state_payload,
)
from tests.unit.test_state_model_enforcement import (
    OptionalReading,
    Reading,
    production_warning_filters,
)

pytestmark = pytest.mark.unit


# =============================================================================
# The two paths, behind one signature
# =============================================================================

#: ``(name, callable)`` for each documented entry point.  Both take a value and
#: a state model and return the dict that reaches the wire.
_PATHS: list[tuple[str, Callable[[Any, Any], dict[str, Any] | None]]] = [
    ("normalize_return", lambda v, m: normalize_return(v, m, handler="h")),
    ("validate_state_payload", lambda v, m: validate_state_payload(v, m, handler="h")),
]


@dataclass(frozen=True)
class _Case:
    """One row of the agreement matrix."""

    id: str
    value: Any
    model: Any
    #: Expected wire dict, or ``None`` when the case must raise.
    expected: dict[str, Any] | None
    rejects: bool = False


_PATH_AGREEMENT_MATRIX: list[_Case] = [
    _Case(
        "conforming_dict",
        {"sensor": "a", "value": 1.5},
        Reading,
        {"sensor": "a", "value": 1.5},
    ),
    _Case("non_conforming_dict", {"sensor": "a"}, Reading, None, rejects=True),
    _Case(
        "model_instance",
        Reading(sensor="a", value=1.5),
        Reading,
        {"sensor": "a", "value": 1.5},
    ),
    _Case(
        "extra_keys_not_on_model",
        {"sensor": "a", "value": 1.5, "stray": 9},
        Reading,
        {"sensor": "a", "value": 1.5},
    ),
    _Case("absent_optional_field", {"sensor": "a"}, OptionalReading, {"sensor": "a"}),
    _Case(
        "present_optional_field",
        {"sensor": "a", "brightness": 7},
        OptionalReading,
        {"sensor": "a", "brightness": 7},
    ),
    _Case(
        "explicitly_null_optional_field",
        {"sensor": "a", "brightness": None},
        OptionalReading,
        {"sensor": "a"},
    ),
]

_MATRIX_IDS = [case.id for case in _PATH_AGREEMENT_MATRIX]


def _outcome(
    path: Callable[[Any, Any], dict[str, Any] | None], case: _Case
) -> dict[str, Any] | str | None:
    """Run *case* through *path*, returning its dict or a rejection marker."""
    with production_warning_filters():
        try:
            return path(case.value, case.model)
        except ReturnValidationError:
            return "ReturnValidationError"


# =============================================================================
# The matrix
# =============================================================================


class TestStateModelPathsMatchTheMatrix:
    """Each path independently produces the verdict the matrix states.

    Technique: Decision Table Testing — the matrix is the specification both
    implementations are measured against.
    """

    @pytest.mark.parametrize("case", _PATH_AGREEMENT_MATRIX, ids=_MATRIX_IDS)
    @pytest.mark.parametrize("name,path", _PATHS, ids=[n for n, _ in _PATHS])
    def test_state_model_path_matches_matrix_for_every_case(
        self,
        name: str,
        path: Callable[[Any, Any], dict[str, Any] | None],
        case: _Case,
    ) -> None:
        """*path* accepts or rejects *case* exactly as the matrix says."""
        # Arrange / Act
        outcome = _outcome(path, case)

        # Assert
        expected: dict[str, Any] | str | None = (
            "ReturnValidationError" if case.rejects else case.expected or {}
        )
        assert outcome == expected, (
            f"{name} disagrees with the ADR-068 matrix on {case.id!r}: "
            f"expected {expected!r}, got {outcome!r}."
        )


class TestStateModelPathsAgreeWithEachOther:
    """The two paths produce the same verdict as each other, case for case.

    This is the drift guard proper: it holds even if someone updates the
    expectation table, because the oracle is the *other* implementation.

    Technique: Back-to-Back Testing.
    """

    @pytest.mark.parametrize("case", _PATH_AGREEMENT_MATRIX, ids=_MATRIX_IDS)
    def test_state_model_paths_agree_for_every_matrix_case(self, case: _Case) -> None:
        """normalize_return and validate_state_payload cannot drift apart."""
        # Arrange / Act
        from_return = _outcome(_PATHS[0][1], case)
        from_publish = _outcome(_PATHS[1][1], case)

        # Assert
        assert from_return == from_publish, (
            f"ADR-068 promises one rule across all four publishing archetypes, "
            f"but the two code paths disagree on {case.id!r}:\n"
            f"  normalize_return       (telemetry/command) -> {from_return!r}\n"
            f"  validate_state_payload (device/stream)     -> {from_publish!r}\n"
            f"Fix the divergence in _runners/_contracts.py, or — if it is "
            f"deliberate — amend ADR-068 and pin it in "
            f"TestStateModelPathsDocumentedDivergence."
        )


class TestStateModelPathsDocumentedDivergence:
    """The one sanctioned asymmetry, pinned so it cannot widen silently.

    ADR-068 clause C applies ``exclude_none=True`` to the *validated* dump
    only.  A handler returning a genuine model instance rides the EAFP fast
    path, which stays allocation-free (ADR-013 / ADR-021) and therefore dumps
    without ``exclude_none``.  So a ``None`` the handler set itself still
    reaches the wire as ``null`` on ``normalize_return``, while
    ``validate_state_payload`` omits it.

    Technique: Regression Testing — asserts the divergence as it is, so both
    closing it and widening it are visible changes.
    """

    def test_state_model_paths_diverge_for_model_instance_with_none_optional(
        self,
    ) -> None:
        """Fast-path null-fill vs. clause D omission — the known asymmetry."""
        # Arrange
        instance = OptionalReading(sensor="a")

        # Act
        with production_warning_filters():
            from_return = normalize_return(instance, OptionalReading, handler="h")
            from_publish = validate_state_payload(
                instance,  # ty: ignore[invalid-argument-type]
                OptionalReading,
                handler="h",
            )

        # Assert
        assert from_return == {"sensor": "a", "brightness": None}
        assert from_publish == {"sensor": "a"}

    def test_state_model_paths_agree_when_the_same_value_arrives_as_a_dict(
        self,
    ) -> None:
        """The divergence is about the fast path, not about the model.

        The same absent-optional payload expressed as a plain ``dict`` agrees
        on both paths — which is what bounds the asymmetry to one input class.
        """
        # Arrange
        payload: dict[str, object] = {"sensor": "a"}

        # Act
        with production_warning_filters():
            from_return = normalize_return(payload, OptionalReading, handler="h")
        from_publish = validate_state_payload(payload, OptionalReading, handler="h")

        # Assert
        assert from_return == from_publish == {"sensor": "a"}
