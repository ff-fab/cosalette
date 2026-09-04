"""Guard tests for _help_extra.py API-surface claims vs. real signatures.

``_ai_content/_help_extra.py`` is hand-maintained prose documenting the
framework's public API for ``cosalette ai help`` topics. Because it is not
generated from signatures, it can silently drift out of sync and confidently
describe parameters that no longer exist. These guards reflect over a curated
set of API-surface claims made in the file and assert they still hold against
the real callables, so future drift is a CI failure instead of a reader's
``TypeError``.

The ``contracts`` topic's ``state_model`` guarantee gets a stronger guard than
the rest: it once shipped as a categorical false statement in the sanctioned
AI-context surface (ADR-034 / ADR-035), so each clause of the wording is pinned
to the *behaviour* backing it, not merely asserted as a string. Changing the
prose without the code — or the code without the prose — fails CI.

Test Techniques Used:
- Specification-based Testing: documented parameter names must exist on the
  real callable they describe.
- Regression Testing: catches stale parameter claims on ``App``/``Router``, and
  reproduces the ADR-068 defect where the help text promised validation the
  code did not perform.
- Back-to-Back Testing: each ``state_model`` claim is asserted twice — once
  against the help prose, once against the real callable that implements it —
  so the two cannot drift apart.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable

import pytest
from pydantic import BaseModel

from cosalette import App, Router
from cosalette._ai_content._help_extra import get_extra_help
from cosalette._runners._contracts import (
    ReturnValidationError,
    normalize_handler_return,
    normalize_return,
    validate_state_payload,
)
from cosalette.di import Depends
from cosalette.mqtt import Payload, Topic
from cosalette.schema import (
    ConsumerMeta,
    HaDiscoveryMeta,
    OpenHabMeta,
    percent,
    temperature,
)
from tests.fixtures.state_models import production_warning_filters


def _param_names(func: Callable[..., object]) -> set[str]:
    """Return the real parameter names of *func*, excluding 'self'."""
    return set(inspect.signature(func).parameters) - {"self"}


# (topic, callable, documented parameter names claimed in _help_extra.py)
_DOCUMENTED_API_SURFACE: list[tuple[str, Callable[..., object], set[str]]] = [
    ("router", Router.__init__, {"prefix", "tags", "adapters"}),
    ("router", App.include_router, {"router", "prefix", "tags", "adapters"}),
    ("router", Depends, {"dependency"}),
    ("contracts", Payload, {"raw"}),
    ("consumer", temperature, {"display_name"}),
    ("consumer", percent, {"display_name", "icon"}),
    ("discovery", App.discovery, {"discovery_prefix", "enrich"}),
]


class TestHelpExtraDocumentedParamsExist:
    """_help_extra.py names these parameters; they must still be real."""

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
        this guard exists to catch.
        """
        content = get_extra_help("router")

        assert content is not None
        assert "There is NO dependencies=" in content

    def test_topic_callable_accepts_no_parameters(self) -> None:
        """Topic() takes no parameters — guard against accidental signature changes."""
        assert _param_names(Topic) == set()

    def test_router_help_does_not_document_nonexistent_lifespan_param(self) -> None:
        """Router help must not claim a lifespan= param — Router never had one.

        Technique: Regression Testing — reproduces the exact historical defect
        this guard exists to catch.
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
        import typing

        real_keys = set(typing.get_type_hints(ConsumerMeta))

        # Assert
        assert documented_keys == real_keys, (
            f"cosalette ai help consumer's documented key set {documented_keys} "
            f"no longer matches ConsumerMeta's real keys {real_keys}. Update "
            f"_ai_content/_help_extra.py."
        )

    def test_consumer_overrides_help_ha_discovery_keys_match_typed_dict(self) -> None:
        """Documented ha_discovery() keys must be exactly HaDiscoveryMeta's keys."""
        # Arrange
        documented_keys = {
            "component",
            "value_template",
            "command_template",
            "expire_after",
            "extra",
        }

        # Act
        import typing

        real_keys = set(typing.get_type_hints(HaDiscoveryMeta))

        # Assert
        assert documented_keys == real_keys, (
            f"cosalette ai help consumer-overrides' documented ha_discovery() key "
            f"set {documented_keys} no longer matches HaDiscoveryMeta's real keys "
            f"{real_keys}. Update _ai_content/_help_extra.py."
        )

    def test_consumer_overrides_help_openhab_keys_match_typed_dict(self) -> None:
        """Documented openhab() keys must be exactly OpenHabMeta's keys."""
        # Arrange
        documented_keys = {
            "item_type",
            "label",
            "groups",
            "tags",
            "channel_type",
            "channel_params",
        }

        # Act
        import typing

        real_keys = set(typing.get_type_hints(OpenHabMeta))

        # Assert
        assert documented_keys == real_keys, (
            f"cosalette ai help consumer-overrides' documented openhab() key set "
            f"{documented_keys} no longer matches OpenHabMeta's real keys "
            f"{real_keys}. Update _ai_content/_help_extra.py."
        )


# =============================================================================
# The contracts topic's state_model guarantee, pinned to its backing code
# =============================================================================


class _Reading(BaseModel):
    """All-required contract — a dict missing ``value`` does not conform."""

    sensor: str
    value: float


class _OptionalReading(BaseModel):
    """Contract with an optional field — the exclude_none claim."""

    sensor: str
    brightness: int | None = None


async def _loosely_annotated() -> dict[str, object]:
    """A handler annotated the idiomatic, permissive way."""
    return {}


def _contracts_help() -> str:
    """Return the ``contracts`` topic text, failing loudly if it vanished."""
    content = get_extra_help("contracts")
    assert content is not None, "the 'contracts' help topic no longer exists"
    return content


class TestContractsHelpStateModelGuarantee:
    """Every clause of the state_model guarantee must match real behaviour.

    ADR-068 exists because this text promised validation that
    ``normalize_handler_return`` did not perform. Each test below asserts the
    claim *and* exercises the callable that has to make it true, so neither
    half can be changed alone.

    Technique: Back-to-Back Testing (prose vs. implementation) + Regression
    Testing (the exact historical defect).
    """

    def test_contracts_help_precedence_claim_matches_normalize_handler_return(
        self,
    ) -> None:
        """'state_model= outranks the return annotation' — ADR-068 clause A.

        The negative control matters: the same value passes when
        ``state_model`` is omitted, so the rejection is attributable to
        precedence and not to the annotation being strict.
        """
        # Arrange
        content = _contracts_help()
        non_conforming: dict[str, object] = {"sensor": "a"}  # no 'value'

        # Act
        with production_warning_filters():
            without_model = normalize_handler_return(
                _loosely_annotated, non_conforming, None
            )

        # Assert — the annotation alone accepts anything ...
        assert without_model == non_conforming
        # ... so state_model must be what rejects it.
        assert "state_model= outranks the return annotation" in content
        with production_warning_filters(), pytest.raises(ReturnValidationError):
            normalize_handler_return(_loosely_annotated, non_conforming, _Reading)

    def test_contracts_help_fail_closed_claim_matches_normalize_return(self) -> None:
        """'a plain dict that does not conform raises' — ADR-068 clause B.

        Asserted under production warning filters: the suite's
        ``filterwarnings = ["error"]`` would make the pre-0.9.0 fast path look
        fail-closed on its own, proving nothing.
        """
        # Arrange
        content = _contracts_help()

        # Assert
        assert "raises ReturnValidationError" in content
        assert "never published unchanged" in content
        with production_warning_filters(), pytest.raises(ReturnValidationError):
            normalize_return({"sensor": "a"}, _Reading, handler="rx")

    def test_contracts_help_registration_warning_claim_matches_registration(
        self,
    ) -> None:
        """'registration emits a UserWarning naming both' — ADR-068 clause F."""
        # Arrange
        content = _contracts_help()
        app = App(name="testapp", version="1.0.0", store=None)

        # Assert
        assert "emits a UserWarning naming both" in content
        with pytest.warns(UserWarning, match="state_model"):

            @app.telemetry("rx", interval=30, state_model=_Reading)
            async def rx() -> dict[str, object]:
                return {}

    def test_contracts_help_output_shape_claim_matches_both_dump_sites(self) -> None:
        """'an omitted key, not an explicit null' — ADR-068 clauses C and D.

        The claim is about *one* output shape, so both dump sites are checked.
        """
        # Arrange
        content = _contracts_help()
        payload: dict[str, object] = {"sensor": "a"}  # 'brightness' omitted

        # Act
        with production_warning_filters():
            from_return = normalize_return(payload, _OptionalReading)
        from_publish = validate_state_payload(payload, _OptionalReading)

        # Assert
        assert "exclude_none=True" in content
        assert "an omitted key, not an explicit null" in content
        assert from_return == from_publish == {"sensor": "a"}

    def test_contracts_help_opt_in_claim_matches_absent_state_model(self) -> None:
        """'Omit state_model ... and no validation happens at all'."""
        # Arrange
        content = _contracts_help()
        junk: dict[str, object] = {"anything": object()}

        # Act
        async def unannotated():  # noqa: ANN202 — the no-contract case
            return junk

        with production_warning_filters():
            result = normalize_handler_return(unannotated, junk, None)

        # Assert
        assert "Omit state_model (the default) and no validation" in content
        assert result is junk

    def test_contracts_help_does_not_resurrect_the_0_8_x_fallback_claim(self) -> None:
        """The superseded 0.8.x wording must not come back.

        Technique: Regression Testing — the shipped text once said
        ``state_model`` was 'only a fallback' behind the return annotation,
        which ADR-068 clause A reversed.
        """
        # Arrange
        content = _contracts_help()

        # Assert
        assert "only a fallback" not in content
        assert "behaves differently by archetype" not in content
