"""Save policies for controlling when DeviceStore state is persisted.

Implements the Strategy pattern for persist-decision logic.
Each policy encapsulates a single save-timing rule; composites
combine rules with boolean operators (``|`` for OR, ``&`` for AND).

Policies provided:
    - ``SaveOnPublish()`` — save after each MQTT publish
    - ``SaveOnChange()`` — save whenever the store is dirty
    - ``SaveOnShutdown()`` — save only on shutdown (lightest I/O)
    - ``AnySavePolicy`` / ``AllSavePolicy`` — boolean composites

The framework ALWAYS saves on shutdown regardless of policy (safety net).

See Also:
    ADR-013 — Telemetry publish strategies (analogous pattern).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from cosalette._stores import DeviceStore

# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class PersistPolicy(Protocol):
    """Save-timing contract for the persistence system.

    The framework calls ``should_save`` after each telemetry cycle
    to decide whether to persist the :class:`DeviceStore` immediately.
    This is simpler than :class:`PublishStrategy` — no clock binding
    or post-publish callback is needed.
    """

    def should_save(self, store: DeviceStore, published: bool) -> bool:
        """Decide whether to save right now.

        Args:
            store: The :class:`DeviceStore` being managed.
            published: ``True`` if an MQTT publish just occurred
                this cycle.

        Returns:
            ``True`` if the store should be saved now.
        """
        ...


# ---------------------------------------------------------------------------
# Abstract base with operator support
# ---------------------------------------------------------------------------


class _SavePolicyBase:
    """Concrete base providing ``|`` (OR) and ``&`` (AND) composition.

    All shipped policies inherit from this class so users can write
    expressive combinations such as::

        policy = SaveOnPublish() | SaveOnChange()
    """

    def __or__(self, other: _SavePolicyBase) -> AnySavePolicy:
        """Combine two policies with OR semantics."""
        return AnySavePolicy(self, other)

    def __and__(self, other: _SavePolicyBase) -> AllSavePolicy:
        """Combine two policies with AND semantics."""
        return AllSavePolicy(self, other)

    def should_save(self, store: DeviceStore, published: bool) -> bool:
        """Decide whether to save right now."""
        raise NotImplementedError  # pragma: no cover


# ---------------------------------------------------------------------------
# Concrete policies
# ---------------------------------------------------------------------------


class SaveOnPublish(_SavePolicyBase):
    """Save after each successful MQTT publish.

    This is the most common policy — the store is persisted
    whenever new data is published to MQTT, ensuring the persisted
    state matches what's been broadcast.
    """

    def should_save(
        self,
        store: DeviceStore,  # noqa: ARG002
        published: bool,
    ) -> bool:
        """Return ``True`` when an MQTT publish just occurred."""
        return published

    def __repr__(self) -> str:
        return "SaveOnPublish()"


class SaveOnChange(_SavePolicyBase):
    """Save whenever the store has been modified (dirty).

    Saves on every handler cycle where the store was mutated,
    regardless of whether MQTT publishing occurred.  Most aggressive
    policy — ensures minimal data loss on crash.
    """

    def should_save(
        self,
        store: DeviceStore,
        published: bool,  # noqa: ARG002
    ) -> bool:
        """Return ``True`` when the store has uncommitted changes."""
        return store.dirty

    def __repr__(self) -> str:
        return "SaveOnChange()"


class SaveOnShutdown(_SavePolicyBase):
    """Save only on graceful shutdown.

    The lightest I/O policy — no saves during normal operation.
    Data accumulated during a session is only persisted when the
    app shuts down cleanly.  Risk: data loss on hard crash/power loss.

    Note: The framework always saves on shutdown regardless of policy,
    so this policy effectively means "never save during the loop".
    """

    def should_save(
        self,
        store: DeviceStore,  # noqa: ARG002
        published: bool,  # noqa: ARG002
    ) -> bool:
        """Always return ``False`` — framework handles shutdown save."""
        return False

    def __repr__(self) -> str:
        return "SaveOnShutdown()"


# ---------------------------------------------------------------------------
# Composites
# ---------------------------------------------------------------------------


class AnySavePolicy(_SavePolicyBase):
    """OR-composite: save if **any** child says yes.

    Nested ``AnySavePolicy`` instances are automatically flattened::

        AnySavePolicy(AnySavePolicy(a, b), c)  →  AnySavePolicy(a, b, c)
    """

    def __init__(self, *children: _SavePolicyBase) -> None:
        self._children: list[_SavePolicyBase] = []
        for child in children:
            if isinstance(child, AnySavePolicy):
                self._children.extend(child._children)
            else:
                self._children.append(child)
        if not self._children:
            msg = "AnySavePolicy requires at least one child policy"
            raise ValueError(msg)

    def should_save(self, store: DeviceStore, published: bool) -> bool:
        """Return ``True`` if **any** child returns ``True``."""
        return any(c.should_save(store, published) for c in self._children)

    def __repr__(self) -> str:
        children = ", ".join(repr(c) for c in self._children)
        return f"AnySavePolicy({children})"


class AllSavePolicy(_SavePolicyBase):
    """AND-composite: save only if **all** children agree.

    Nested ``AllSavePolicy`` instances are automatically flattened::

        AllSavePolicy(AllSavePolicy(a, b), c)  →  AllSavePolicy(a, b, c)
    """

    def __init__(self, *children: _SavePolicyBase) -> None:
        self._children: list[_SavePolicyBase] = []
        for child in children:
            if isinstance(child, AllSavePolicy):
                self._children.extend(child._children)
            else:
                self._children.append(child)
        if not self._children:
            msg = "AllSavePolicy requires at least one child policy"
            raise ValueError(msg)

    def should_save(self, store: DeviceStore, published: bool) -> bool:
        """Return ``True`` only if **all** children return ``True``."""
        return all(c.should_save(store, published) for c in self._children)

    def __repr__(self) -> str:
        children = ", ".join(repr(c) for c in self._children)
        return f"AllSavePolicy({children})"
