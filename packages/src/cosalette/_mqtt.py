"""MQTT client port and adapters.

Provides MqttPort (Protocol) and three implementations:

- MqttClient — real aiomqtt-based client with reconnection
- MockMqttClient — test double that records calls
- NullMqttClient — silent no-op adapter

Design decisions:

- aiomqtt imported lazily inside MqttClient._connection_loop() so Mock/Null
  work without aiomqtt installed (ADR-006 lazy import pattern)
- Subscriptions tracked internally and restored on reconnect
- MessageCallback dispatches (topic, payload) to registered handlers
- No topic filtering — consumers handle routing (removed velux /actual filter)
- WillConfig abstracts LWT without leaking aiomqtt types

See Also:
    ADR-001 §3 — MQTT adapter pluggability
    ADR-002 — Topic layout conventions
    ADR-006 — Protocol-based ports
    ADR-012 — LWT and availability
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from cosalette._settings import MqttSettings

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
        """Record a publish call."""
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
        """Clear all recorded data and callbacks."""
        self.published.clear()
        self.subscriptions.clear()
        self._callbacks.clear()

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
# Real adapter
# ---------------------------------------------------------------------------


@dataclass
class MqttClient:
    """Production MQTT adapter backed by *aiomqtt*.

    Uses a background task that maintains a persistent connection
    with automatic reconnection.  ``aiomqtt`` is imported lazily
    inside ``_connection_loop()`` so the mock and null adapters work
    without the dependency installed.

    See Also:
        ADR-006 — Hexagonal architecture (lazy imports).
        ADR-012 — LWT / availability via ``WillConfig``.
    """

    settings: MqttSettings
    will: WillConfig | None = None

    # internal state --------------------------------------------------------
    _callbacks: list[MessageCallback] = field(
        default_factory=list,
        init=False,
        repr=False,
    )
    _subscriptions: set[str] = field(
        default_factory=set,
        init=False,
        repr=False,
    )
    _client: Any = field(default=None, init=False, repr=False)
    _listen_task: asyncio.Task[None] | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _connected: asyncio.Event = field(
        default_factory=asyncio.Event,
        init=False,
        repr=False,
    )
    _stopping: bool = field(default=False, init=False, repr=False)

    # -- MqttPort methods --------------------------------------------------

    async def publish(
        self,
        topic: str,
        payload: str,
        *,
        retain: bool = False,
        qos: int = 1,
    ) -> None:
        """Publish a message to the broker.

        Raises:
            RuntimeError: If the client is not connected.
        """
        if self._client is None:
            msg = "MqttClient is not connected"
            raise RuntimeError(msg)
        await self._client.publish(
            topic,
            payload,
            retain=retain,
            qos=qos,
        )
        logger.debug(
            "Published to %s (qos=%d, retain=%s)",
            topic,
            qos,
            retain,
        )

    async def subscribe(self, topic: str) -> None:
        """Subscribe to *topic*.

        The subscription is tracked internally so it can be restored
        after a reconnection.
        """
        self._subscriptions.add(topic)
        if self._client is not None:
            await self._client.subscribe(
                topic,
                qos=self.settings.qos,
            )

    # -- Callback registration ---------------------------------------------

    def on_message(self, callback: MessageCallback) -> None:
        """Register a callback for inbound messages."""
        self._callbacks.append(callback)

    # -- Lifecycle ----------------------------------------------------------

    async def start(self) -> None:
        """Start the background connection loop."""
        if self._listen_task is not None and not self._listen_task.done():
            logger.debug("MqttClient.start() called while already running")
            return
        self._stopping = False
        self._listen_task = asyncio.create_task(
            self._connection_loop(),
        )

    async def stop(self) -> None:
        """Stop the connection loop and clean up.

        Idempotent — safe to call multiple times.
        """
        self._stopping = True
        if self._listen_task is not None:
            self._listen_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._listen_task
            self._listen_task = None
        self._client = None
        self._connected.clear()

    @property
    def is_connected(self) -> bool:
        """Whether the client is currently connected to the broker."""
        return self._connected.is_set()

    # -- Internal -----------------------------------------------------------

    async def _connection_loop(self) -> None:
        """Maintain a persistent connection with auto-reconnect.

        ``aiomqtt`` is imported lazily here so that ``MockMqttClient``
        and ``NullMqttClient`` work without the dependency.
        """
        try:
            import aiomqtt  # noqa: PLC0415
        except ModuleNotFoundError as exc:
            msg = "aiomqtt is required to use MqttClient"
            raise RuntimeError(msg) from exc

        while not self._stopping:
            try:
                password: str | None = None
                if self.settings.password is not None:
                    password = self.settings.password.get_secret_value()

                will: aiomqtt.Will | None = None
                if self.will is not None:
                    will = aiomqtt.Will(
                        topic=self.will.topic,
                        payload=self.will.payload,
                        qos=self.will.qos,
                        retain=self.will.retain,
                    )

                async with aiomqtt.Client(
                    hostname=self.settings.host,
                    port=self.settings.port,
                    username=self.settings.username,
                    password=password,
                    identifier=self.settings.client_id or None,
                    will=will,
                ) as client:
                    self._client = client
                    try:
                        # Restore tracked subscriptions
                        for topic in list(self._subscriptions):
                            await client.subscribe(
                                topic,
                                qos=self.settings.qos,
                            )

                        self._connected.set()
                        logger.info(
                            "MQTT connected to %s:%d",
                            self.settings.host,
                            self.settings.port,
                        )

                        async for message in client.messages:
                            await self._dispatch(message)
                    finally:
                        self._connected.clear()
                        self._client = None

            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning(
                    "MQTT connection lost, reconnecting in %.1fs",
                    self.settings.reconnect_interval,
                    exc_info=True,
                )
                await asyncio.sleep(
                    self.settings.reconnect_interval,
                )

    async def _dispatch(self, message: Any) -> None:
        """Decode and fan-out an inbound message to callbacks."""
        topic = str(message.topic)

        if message.payload is None:
            logger.debug(
                "Skipping message with None payload on %s",
                topic,
            )
            return

        payload = (
            message.payload.decode("utf-8")
            if isinstance(message.payload, (bytes, bytearray))
            else str(message.payload)
        )

        for cb in self._callbacks:
            try:
                await cb(topic, payload)
            except Exception:
                logger.exception(
                    "Error in message callback for %s",
                    topic,
                )
