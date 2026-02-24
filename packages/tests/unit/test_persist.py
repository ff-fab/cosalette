"""Unit tests for cosalette._persist — save policies for persistence.

Test Techniques Used:
    - Specification-based: Protocol compliance, constructor contracts
    - Decision Table: should_save outcomes for each policy × input combinations
    - Equivalence Partitioning: Different policies with different triggers
    - State Transition: Dirty tracking interaction with policies
"""

from __future__ import annotations

import pytest

from cosalette._persist import (
    AllSavePolicy,
    AnySavePolicy,
    PersistPolicy,
    SaveOnChange,
    SaveOnPublish,
    SaveOnShutdown,
)
from cosalette._stores import DeviceStore, MemoryStore

# =============================================================================
# Helpers
# =============================================================================


def _make_store(*, dirty: bool = False) -> DeviceStore:
    """Create a DeviceStore with controlled dirty state."""
    backend = MemoryStore()
    store = DeviceStore(backend, "test")
    store.load()
    if dirty:
        store["_marker"] = True  # triggers dirty=True
    return store


# =============================================================================
# Tests
# =============================================================================


class TestSaveOnPublish:
    """SaveOnPublish — save when an MQTT publish just occurred.

    Technique: Decision Table — published × dirty combinations.
    """

    def test_returns_true_when_published(self) -> None:
        """should_save is True when published=True."""
        policy = SaveOnPublish()
        store = _make_store(dirty=False)
        assert policy.should_save(store, published=True) is True

    def test_returns_false_when_not_published(self) -> None:
        """should_save is False when published=False."""
        policy = SaveOnPublish()
        store = _make_store(dirty=True)
        assert policy.should_save(store, published=False) is False

    def test_ignores_dirty_state(self) -> None:
        """Published flag controls the decision, not dirty state."""
        policy = SaveOnPublish()

        clean = _make_store(dirty=False)
        dirty = _make_store(dirty=True)

        assert policy.should_save(clean, published=True) is True
        assert policy.should_save(dirty, published=True) is True
        assert policy.should_save(clean, published=False) is False
        assert policy.should_save(dirty, published=False) is False

    def test_satisfies_protocol(self) -> None:
        """SaveOnPublish satisfies the PersistPolicy protocol."""
        assert isinstance(SaveOnPublish(), PersistPolicy)


class TestSaveOnChange:
    """SaveOnChange — save when the store has been modified.

    Technique: Decision Table — dirty × published combinations.
    """

    def test_returns_true_when_dirty(self) -> None:
        """should_save is True when store.dirty is True."""
        policy = SaveOnChange()
        store = _make_store(dirty=True)
        assert policy.should_save(store, published=False) is True

    def test_returns_false_when_clean(self) -> None:
        """should_save is False when store.dirty is False."""
        policy = SaveOnChange()
        store = _make_store(dirty=False)
        assert policy.should_save(store, published=True) is False

    def test_ignores_published_flag(self) -> None:
        """Dirty state controls the decision, not published flag."""
        policy = SaveOnChange()

        clean = _make_store(dirty=False)
        dirty = _make_store(dirty=True)

        assert policy.should_save(clean, published=True) is False
        assert policy.should_save(clean, published=False) is False
        assert policy.should_save(dirty, published=True) is True
        assert policy.should_save(dirty, published=False) is True

    def test_satisfies_protocol(self) -> None:
        """SaveOnChange satisfies the PersistPolicy protocol."""
        assert isinstance(SaveOnChange(), PersistPolicy)


class TestSaveOnShutdown:
    """SaveOnShutdown — never save during the loop.

    Technique: Decision Table — all combinations return False.
    """

    def test_always_returns_false(self) -> None:
        """should_save is always False regardless of inputs."""
        policy = SaveOnShutdown()

        for dirty in (True, False):
            for published in (True, False):
                store = _make_store(dirty=dirty)
                assert policy.should_save(store, published=published) is False

    def test_satisfies_protocol(self) -> None:
        """SaveOnShutdown satisfies the PersistPolicy protocol."""
        assert isinstance(SaveOnShutdown(), PersistPolicy)


class TestAnySavePolicy:
    """AnySavePolicy — OR composite: save if any child says yes.

    Technique: Specification-based — composite semantics and flattening.
    """

    def test_true_if_any_child_true(self) -> None:
        """Returns True when at least one child says True."""
        policy = SaveOnPublish() | SaveOnShutdown()  # publish=True → True
        store = _make_store()
        assert policy.should_save(store, published=True) is True

    def test_false_if_all_children_false(self) -> None:
        """Returns False when all children say False."""
        policy = SaveOnShutdown() | SaveOnShutdown()
        store = _make_store(dirty=True)
        assert policy.should_save(store, published=True) is False

    def test_flattens_nested_any(self) -> None:
        """Nested AnySavePolicy instances are flattened."""
        inner = AnySavePolicy(SaveOnPublish(), SaveOnChange())
        outer = AnySavePolicy(inner, SaveOnShutdown())
        assert len(outer._children) == 3

    def test_rejects_empty_children(self) -> None:
        """Empty children list raises ValueError."""
        with pytest.raises(ValueError, match="at least one"):
            AnySavePolicy()

    def test_satisfies_protocol(self) -> None:
        """AnySavePolicy satisfies the PersistPolicy protocol."""
        assert isinstance(AnySavePolicy(SaveOnPublish()), PersistPolicy)


class TestAllSavePolicy:
    """AllSavePolicy — AND composite: save only if all children agree.

    Technique: Specification-based — composite semantics and flattening.
    """

    def test_true_if_all_children_true(self) -> None:
        """Returns True when all children say True."""
        policy = SaveOnPublish() & SaveOnChange()
        store = _make_store(dirty=True)
        assert policy.should_save(store, published=True) is True

    def test_false_if_any_child_false(self) -> None:
        """Returns False when at least one child says False."""
        policy = SaveOnPublish() & SaveOnChange()
        store = _make_store(dirty=False)
        assert policy.should_save(store, published=True) is False

    def test_flattens_nested_all(self) -> None:
        """Nested AllSavePolicy instances are flattened."""
        inner = AllSavePolicy(SaveOnPublish(), SaveOnChange())
        outer = AllSavePolicy(inner, SaveOnShutdown())
        assert len(outer._children) == 3

    def test_rejects_empty_children(self) -> None:
        """Empty children list raises ValueError."""
        with pytest.raises(ValueError, match="at least one"):
            AllSavePolicy()

    def test_satisfies_protocol(self) -> None:
        """AllSavePolicy satisfies the PersistPolicy protocol."""
        assert isinstance(AllSavePolicy(SaveOnPublish()), PersistPolicy)


class TestComposition:
    """Composition via | and & operators.

    Technique: Specification-based — operator overloading produces
    correct composite types.
    """

    def test_or_creates_any_save_policy(self) -> None:
        """``a | b`` creates an AnySavePolicy."""
        result = SaveOnPublish() | SaveOnChange()
        assert isinstance(result, AnySavePolicy)

    def test_and_creates_all_save_policy(self) -> None:
        """``a & b`` creates an AllSavePolicy."""
        result = SaveOnPublish() & SaveOnChange()
        assert isinstance(result, AllSavePolicy)

    def test_nested_or_flattens(self) -> None:
        """``(a | b) | c`` flattens to 3 children."""
        result = (SaveOnPublish() | SaveOnChange()) | SaveOnShutdown()
        assert isinstance(result, AnySavePolicy)
        assert len(result._children) == 3

    def test_nested_and_flattens(self) -> None:
        """``(a & b) & c`` flattens to 3 children."""
        result = (SaveOnPublish() & SaveOnChange()) & SaveOnShutdown()
        assert isinstance(result, AllSavePolicy)
        assert len(result._children) == 3

    def test_mixed_composition(self) -> None:
        """``(a | b) & c`` creates AllSavePolicy with AnySavePolicy child."""
        result = (SaveOnPublish() | SaveOnChange()) & SaveOnShutdown()
        assert isinstance(result, AllSavePolicy)
        assert len(result._children) == 2
        assert isinstance(result._children[0], AnySavePolicy)
        assert isinstance(result._children[1], SaveOnShutdown)


class TestPersistPolicyProtocol:
    """Verify PersistPolicy is a runtime-checkable protocol.

    Technique: Specification-based — structural subtyping checks.
    """

    def test_protocol_is_runtime_checkable(self) -> None:
        """PersistPolicy can be used with isinstance."""

        class Dummy:
            def should_save(self, store: object, published: bool) -> bool:
                return True

        assert isinstance(Dummy(), PersistPolicy)

    def test_class_without_method_does_not_satisfy(self) -> None:
        """A class missing should_save fails isinstance."""

        class NotAPolicy:
            pass

        assert not isinstance(NotAPolicy(), PersistPolicy)

    def test_all_shipped_policies_satisfy(self) -> None:
        """Every concrete policy class satisfies the protocol."""
        policies = [
            SaveOnPublish(),
            SaveOnChange(),
            SaveOnShutdown(),
            AnySavePolicy(SaveOnPublish()),
            AllSavePolicy(SaveOnPublish()),
        ]
        for policy in policies:
            assert isinstance(policy, PersistPolicy), f"{type(policy).__name__}"
