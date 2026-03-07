"""MQTT port protocols and lightweight adapters.

Provides MqttPort (Protocol) and lightweight adapters:

- MockMqttClient — test double that records calls
- NullMqttClient — silent no-op adapter

The production adapter (:class:`MqttClient`) lives in ``_mqtt_client``
and is re-exported here for backward compatibility.

See Also:
    ADR-001 §3 — MQTT adapter pluggability
    ADR-002 — Topic layout conventions
    ADR-006 — Protocol-based ports
    ADR-012 — LWT and availability
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

MessageCallback = Callable[[str, str], Awaitable[None]]
"""Async callback receiving (topic, payload) for each inbound message."""

# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WillConfig:
    """Last-Will-and-Testament configuration.

    Abstracts ``aiomqtt.Will`` so that callers never depend on the
    aiomqtt package directly.  The real client translates this into
    the library-specific type inside ``_connection_loop()``.

    See Also:
        ADR-012 — Health and availability reporting.
    """

    topic: str
    payload: str = "offline"
    qos: int = 1
    retain: bool = True


# ---------------------------------------------------------------------------
# Port (Protocol)
# ---------------------------------------------------------------------------


@runtime_checkable
class MqttPort(Protocol):
    """Port contract for MQTT publish/subscribe.

    Satisfies ADR-006 hexagonal architecture: all MQTT interaction
    goes through this protocol so adapters are swappable.
    """

    async def publish(
        self,
        topic: str,
        payload: str,
        *,
        retain: bool = False,
        qos: int = 1,
    ) -> None: ...

    async def subscribe(self, topic: str) -> None: ...


@runtime_checkable
class MqttLifecycle(Protocol):
    """Lifecycle management for MQTT adapters.

    Adapters that need explicit start/stop (e.g. connecting to a broker)
    implement this protocol.  Adapters like MockMqttClient and
    NullMqttClient that need no lifecycle management simply omit these
    methods — the framework detects their absence via ``isinstance``.

    See Also:
        ADR-006 — Interface Segregation: ports are narrow by design.
        PEP 544 — Structural subtyping (Protocols).
    """

    async def start(self) -> None: ...
    async def stop(self) -> None: ...


@runtime_checkable
class MqttMessageHandler(Protocol):
    """Message dispatch capability for MQTT adapters.

    Adapters that can receive inbound messages implement this protocol.
    The framework calls ``on_message`` to wire up the topic router.

    See Also:
        ADR-006 — Interface Segregation.
    """

    def on_message(self, callback: MessageCallback) -> None: ...


# ---------------------------------------------------------------------------
# Null adapter
# ---------------------------------------------------------------------------


@dataclass
class NullMqttClient:
    """Silent no-op MQTT adapter.

    Every method is a no-op that logs at DEBUG level.  Useful as a
    default when MQTT is not configured.
    """

    async def publish(
        self,
        topic: str,
        payload: str,  # noqa: ARG002
        *,
        retain: bool = False,  # noqa: ARG002
        qos: int = 1,  # noqa: ARG002
    ) -> None:
        """Silently discard a publish request."""
        logger.debug("NullMqttClient.publish(%s) — discarded", topic)

    async def subscribe(self, topic: str) -> None:
        """Silently discard a subscribe request."""
        logger.debug("NullMqttClient.subscribe(%s) — discarded", topic)


# ---------------------------------------------------------------------------
# Mock / test-double adapter
# ---------------------------------------------------------------------------


@dataclass
class MockMqttClient:
    """In-memory test double that records MQTT interactions.

    Records publishes and subscriptions for assertion.  Supports
    callback registration and simulated message delivery via
    ``deliver()``.
    """

    published: list[tuple[str, str, bool, int]] = field(
        default_factory=list,
    )
    subscriptions: list[str] = field(default_factory=list)
    raise_on_publish: Exception | None = field(default=None, repr=False)
    _callbacks: list[MessageCallback] = field(
        default_factory=list,
        init=False,
        repr=False,
    )

    # -- MqttPort methods --------------------------------------------------

    async def publish(
        self,
        topic: str,
        payload: str,
        *,
        retain: bool = False,
        qos: int = 1,
    ) -> None:
        """Record a publish call, or raise if ``raise_on_publish`` is set."""
        if self.raise_on_publish is not None:
            raise self.raise_on_publish
        self.published.append((topic, payload, retain, qos))

    async def subscribe(self, topic: str) -> None:
        """Record a subscribe call."""
        self.subscriptions.append(topic)

    # -- Test helpers -------------------------------------------------------

    def on_message(self, callback: MessageCallback) -> None:
        """Register an inbound-message callback."""
        self._callbacks.append(callback)

    async def deliver(self, topic: str, payload: str) -> None:
        """Simulate an inbound message by invoking all callbacks."""
        for cb in self._callbacks:
            await cb(topic, payload)

    @property
    def publish_count(self) -> int:
        """Number of recorded publishes."""
        return len(self.published)

    @property
    def subscribe_count(self) -> int:
        """Number of recorded subscriptions."""
        return len(self.subscriptions)

    def reset(self) -> None:
        """Clear all recorded data, callbacks, and failure injection."""
        self.published.clear()
        self.subscriptions.clear()
        self._callbacks.clear()
        self.raise_on_publish = None

    def get_messages_for(
        self,
        topic: str,
    ) -> list[tuple[str, bool, int]]:
        """Return ``(payload, retain, qos)`` tuples for *topic*."""
        return [
            (payload, retain, qos)
            for t, payload, retain, qos in self.published
            if t == topic
        ]


# ---------------------------------------------------------------------------
# Re-export: MqttClient lives in _mqtt_client but is importable from here
# for backward compatibility (e.g. ``from cosalette._mqtt import MqttClient``).
# ---------------------------------------------------------------------------
from cosalette._mqtt_client import MqttClient as MqttClient  # noqa: E402
