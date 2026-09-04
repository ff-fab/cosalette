"""Unit tests for the ADR-068 clause F registration-time conflict warning.

``state_model=`` outranks the return annotation (clause A), so a differently
typed return annotation is a silent contradiction.  Clause F makes it visible
at registration — a ``UserWarning`` naming both types — without failing the
registration.

Test Techniques Used:
    - Specification-based Testing: clause F's three stated cases — differing
      types warn, same type stays silent, no annotation stays silent.
    - Equivalence Partitioning: annotation classes (absent / same model /
      ``M | None`` / ``None`` / loose ``dict``) against the warn-or-silent
      outcome.
    - Pairwise Testing: every annotation class is exercised against every
      registration entry point (``App.telemetry``, ``App.add_telemetry``,
      ``App.command``, ``App.add_command``, ``Router.telemetry``,
      ``Router.command``), since each reaches the check by a different path.
    - Boundary Value Analysis: ``-> M | None`` and ``-> None`` sit either side
      of the "declares a different contract" boundary.
    - Error Guessing: a fixed ``stacklevel`` would blame framework internals,
      so the reported source file is asserted explicitly.
"""

from __future__ import annotations

import warnings
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from pydantic import BaseModel

from cosalette import App, Router

pytestmark = pytest.mark.unit


class Reading(BaseModel):
    """The declared contract under test."""

    sensor: str


class Other(BaseModel):
    """A second, incompatible contract."""

    value: int


ENTRY_POINTS = [
    "App.telemetry",
    "App.add_telemetry",
    "App.command",
    "App.add_command",
    "Router.telemetry",
    "Router.command",
]


def register(
    entry_point: str,
    handler: Callable[..., Any],
    state_model: Any,
) -> None:
    """Register *handler* through *entry_point* with *state_model*.

    Every entry point reaches ``warn_on_state_model_conflict`` by a different
    route — two decorator bodies, two imperative adders, and the Router
    equivalents — so each is driven through its real public API.
    """
    if entry_point == "App.telemetry":
        app = App(name="test", version="1.0.0")
        app.telemetry("t", interval=30, state_model=state_model)(handler)
    elif entry_point == "App.add_telemetry":
        app = App(name="test", version="1.0.0")
        app.add_telemetry("t", handler, interval=30, state_model=state_model)
    elif entry_point == "App.command":
        app = App(name="test", version="1.0.0")
        app.command("c", state_model=state_model)(handler)
    elif entry_point == "App.add_command":
        app = App(name="test", version="1.0.0")
        app.add_command("c", handler, state_model=state_model)
    elif entry_point == "Router.telemetry":
        Router(prefix="r").telemetry("t", interval=30, state_model=state_model)(handler)
    elif entry_point == "Router.command":
        Router(prefix="r").command("c", state_model=state_model)(handler)
    else:  # pragma: no cover - guards a typo in the parametrisation
        raise AssertionError(f"unknown entry point {entry_point!r}")


@pytest.fixture
def captured() -> Iterator[list[warnings.WarningMessage]]:
    """Record warnings instead of raising them, so silence can be asserted.

    The suite runs with ``filterwarnings = ["error"]``, which proves a warning
    fires but cannot distinguish "no warning" from "some other warning".
    """
    with warnings.catch_warnings(record=True) as record:
        warnings.simplefilter("always")
        yield record


def conflict_warnings(
    record: list[warnings.WarningMessage],
) -> list[warnings.WarningMessage]:
    """Return only the clause F warnings from *record*."""
    return [w for w in record if "state_model=" in str(w.message)]


class TestConflictingAnnotationWarns:
    """A differently typed return annotation warns at registration.

    Technique: Specification-based Testing + Pairwise Testing over entry points.
    """

    @pytest.mark.parametrize("entry_point", ENTRY_POINTS)
    def test_loose_dict_annotation_warns(self, entry_point: str) -> None:
        """``state_model=Reading`` with ``-> dict[str, object]`` disagrees."""

        # Arrange
        async def handler() -> dict[str, object]:
            return {}

        # Act / Assert
        with pytest.warns(UserWarning, match="ADR-068"):
            register(entry_point, handler, Reading)

    @pytest.mark.parametrize("entry_point", ENTRY_POINTS)
    def test_other_model_annotation_warns(self, entry_point: str) -> None:
        """Two concrete but different models are the clearest contradiction."""

        # Arrange
        async def handler() -> Other:
            return Other(value=1)

        # Act / Assert
        with pytest.warns(UserWarning, match="ADR-068"):
            register(entry_point, handler, Reading)


class TestAgreeingRegistrationIsSilent:
    """No warning when the declarations agree or the annotation is absent.

    Technique: Equivalence Partitioning over annotation classes; each partition
    is checked against every entry point.
    """

    @pytest.mark.parametrize("entry_point", ENTRY_POINTS)
    def test_same_type_annotation_is_silent(
        self, entry_point: str, captured: list[warnings.WarningMessage]
    ) -> None:
        """``-> Reading`` with ``state_model=Reading`` is not a contradiction."""

        # Arrange
        async def handler() -> Reading:
            return Reading(sensor="a")

        # Act
        register(entry_point, handler, Reading)

        # Assert
        assert conflict_warnings(captured) == []

    @pytest.mark.parametrize("entry_point", ENTRY_POINTS)
    def test_missing_annotation_is_silent(
        self, entry_point: str, captured: list[warnings.WarningMessage]
    ) -> None:
        """No annotation is the documented ``state_model=``-only form."""

        # Arrange
        async def handler():  # noqa: ANN202
            return {"sensor": "a"}

        # Act
        register(entry_point, handler, Reading)

        # Assert
        assert conflict_warnings(captured) == []

    @pytest.mark.parametrize("entry_point", ENTRY_POINTS)
    def test_optional_same_type_annotation_is_silent(
        self, entry_point: str, captured: list[warnings.WarningMessage]
    ) -> None:
        """``-> Reading | None`` only adds "may suppress the publish"."""

        # Arrange
        async def handler() -> Reading | None:
            return None

        # Act
        register(entry_point, handler, Reading)

        # Assert
        assert conflict_warnings(captured) == []

    @pytest.mark.parametrize("entry_point", ENTRY_POINTS)
    def test_none_annotation_is_silent(
        self, entry_point: str, captured: list[warnings.WarningMessage]
    ) -> None:
        """``-> None`` never returns state, so clause A never overrides it.

        ``state_model=`` on such a handler is channel metadata — it is what
        gives an ``@app.command`` its AsyncAPI state channel.
        """

        # Arrange
        async def handler() -> None:
            return None

        # Act
        register(entry_point, handler, Reading)

        # Assert
        assert conflict_warnings(captured) == []

    @pytest.mark.parametrize("entry_point", ENTRY_POINTS)
    def test_equal_but_not_identical_generic_is_silent(
        self, entry_point: str, captured: list[warnings.WarningMessage]
    ) -> None:
        """A generic contract equal-but-not-identical to the annotation is silent.

        ``dict[str, int]`` built at two call sites is ``==`` but not ``is``;
        clause F compares structurally (ADR-068), so no spurious warning fires.
        """

        # Arrange
        async def handler() -> dict[str, int]:
            return {}

        # Act
        register(entry_point, handler, dict[str, int])

        # Assert
        assert conflict_warnings(captured) == []

    @pytest.mark.parametrize("entry_point", ENTRY_POINTS)
    def test_no_state_model_is_silent(
        self, entry_point: str, captured: list[warnings.WarningMessage]
    ) -> None:
        """Without ``state_model=`` there is nothing to disagree with."""

        # Arrange
        async def handler() -> dict[str, object]:
            return {}

        # Act
        register(entry_point, handler, None)

        # Assert
        assert conflict_warnings(captured) == []


class TestWarningContent:
    """The warning must be actionable on its own.

    Technique: Specification-based Testing on clause F's wording requirement;
    Error Guessing on the stacklevel.
    """

    def test_warning_names_both_types_and_the_winner(
        self, captured: list[warnings.WarningMessage]
    ) -> None:
        """Both type names, the handler name, and the precedence are stated."""

        # Arrange
        async def handler() -> dict[str, object]:
            return {}

        # Act
        register("App.telemetry", handler, Reading)

        # Assert
        message = str(conflict_warnings(captured)[0].message)
        assert "Reading" in message
        assert "dict" in message
        assert "'t'" in message
        assert "state_model= wins" in message

    @pytest.mark.parametrize("entry_point", ENTRY_POINTS)
    def test_warning_points_at_the_registration_site(
        self, entry_point: str, captured: list[warnings.WarningMessage]
    ) -> None:
        """The stacklevel must blame this file, not framework internals."""

        # Arrange
        async def handler() -> dict[str, object]:
            return {}

        # Act
        register(entry_point, handler, Reading)

        # Assert
        assert conflict_warnings(captured)[0].filename == __file__

    def test_registration_still_succeeds(self) -> None:
        """Clause F warns; it must not fail the registration."""
        # Arrange
        app = App(name="test", version="1.0.0")

        async def handler() -> dict[str, object]:
            return {}

        # Act
        with pytest.warns(UserWarning):
            app.telemetry("t", interval=30, state_model=Reading)(handler)

        # Assert
        assert [reg.name for reg in app.telemetry_registrations] == ["t"]
