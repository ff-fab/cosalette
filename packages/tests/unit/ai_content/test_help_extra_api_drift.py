"""Guard tests for _help_extra.py API-surface claims vs. real signatures.

``_ai_content/_help_extra.py`` is hand-maintained prose documenting the
framework's public API for ``cosalette ai help`` topics. Because it is not
generated from signatures, it can silently drift out of sync and confidently
describe parameters that no longer exist — as happened with a ``lifespan=``
claim on ``Router`` and a ``dependencies=`` claim removed in cos-v1dj.3 (see
cos-rzrm). These guards reflect over a curated set of API-surface claims made
in the file and assert they still hold against the real callables, so future
drift is a CI failure instead of a reader's ``TypeError``.

Test Techniques Used:
- Specification-based Testing: documented parameter names must exist on the
  real callable they describe.
- Regression Testing: reproduces the exact historical failure mode (stale
  ``lifespan=``/``dependencies=`` claims on Router).
"""

from __future__ import annotations

import inspect
from collections.abc import Callable

import pytest

from cosalette import Router
from cosalette._ai_content._help_extra import get_extra_help
from cosalette._app import App
from cosalette.di import Depends
from cosalette.mqtt import Payload, Topic
from cosalette.schema import ConsumerMeta, percent, temperature


def _param_names(func: Callable[..., object]) -> set[str]:
    """Return the real parameter names of *func*, excluding 'self'."""
    return set(inspect.signature(func).parameters) - {"self"}


# (topic, callable, documented parameter names claimed in _help_extra.py)
_DOCUMENTED_API_SURFACE: list[tuple[str, Callable[..., object], set[str]]] = [
    ("router", Router.__init__, {"prefix", "tags", "adapters"}),
    ("router", App.include_router, {"router", "prefix", "tags", "adapters"}),
    ("router", Depends, {"dependency"}),
    ("contracts", Payload, {"raw"}),
    ("contracts", Topic, set()),
    ("consumer", temperature, {"display_name"}),
    ("consumer", percent, {"display_name", "icon"}),
]


class TestHelpExtraDocumentedParamsExist:
    """_help_extra.py names these parameters; they must still be real (cos-rzrm)."""

    @pytest.mark.parametrize(
        ("topic", "func", "documented_params"), _DOCUMENTED_API_SURFACE
    )
    def test_help_extra_documented_params_exist_on_real_callable(
        self, topic: str, func: Callable[..., object], documented_params: set[str]
    ) -> None:
        """Every parameter _help_extra.py claims for *func* must be real.

        Technique: Specification-based — the callable's signature is the
        specification; the doc's claims are the assertions under test.
        """
        # Arrange
        real_params = _param_names(func)

        # Act
        missing = documented_params - real_params

        # Assert
        func_name = getattr(func, "__qualname__", repr(func))
        assert not missing, (
            f"cosalette ai help {topic!r} claims {func_name} accepts "
            f"{missing}, which no longer exist on the real signature "
            f"{sorted(real_params)}. Update _ai_content/_help_extra.py."
        )

    def test_router_help_does_not_claim_removed_dependencies_param(self) -> None:
        """Router help must not resurrect the dependencies= param removed in #365.

        Technique: Regression Testing — reproduces the exact historical defect
        (cos-v1dj.3) this guard exists to catch.
        """
        content = get_extra_help("router")

        assert content is not None
        assert "There is NO dependencies=" in content

    def test_router_help_does_not_document_nonexistent_lifespan_param(self) -> None:
        """Router help must not claim a lifespan= param — Router never had one.

        Technique: Regression Testing — reproduces the exact historical defect
        (cos-rzrm) this guard exists to catch.
        """
        content = get_extra_help("router")

        assert content is not None
        assert "lifespan=" not in content


class TestHelpExtraConsumerKeySetMatchesReader:
    """The 'consumer' topic's documented key set must match ConsumerMeta."""

    def test_consumer_help_key_set_matches_consumer_meta_typed_dict(self) -> None:
        """Documented consumer() keys must be exactly ConsumerMeta's keys.

        Technique: Specification-based — ConsumerMeta is the single source of
        truth for valid x-cosalette-consumer keys (drift-guarded elsewhere for
        the reader side); this asserts the help text's prose list agrees.
        """
        # Arrange
        documented_keys = {
            "display_name",
            "device_class",
            "unit",
            "state_class",
            "icon",
            "read_only",
        }

        # Act
        real_keys = set(ConsumerMeta.__annotations__)

        # Assert
        assert documented_keys == real_keys, (
            f"cosalette ai help consumer's documented key set {documented_keys} "
            f"no longer matches ConsumerMeta's real keys {real_keys}. Update "
            f"_ai_content/_help_extra.py."
        )
