"""MQTT client adapter — ``aiomqtt``-based implementation.

This module provides three implementations of
:class:`~velux2mqtt.ports.protocols.MqttPort`:

- :class:`MqttClientAdapter` — real ``aiomqtt`` MQTT client with
  automatic reconnection and message routing
- :class:`MockMqttAdapter` — records calls for unit/integration testing
- :class:`NullMqttAdapter` — silent no-op when MQTT is unavailable

Each backend provides async ``publish()`` and ``subscribe()`` methods,
satisfying the port contract via structural subtyping (PEP 544).

**Design decisions:**

- ``aiomqtt`` is imported lazily inside :meth:`MqttClientAdapter.start`
  so that :class:`MockMqttAdapter` and :class:`NullMqttAdapter` can be
  used without ``aiomqtt`` installed (mirrors the ``RPi.GPIO`` pattern
  in :mod:`~velux2mqtt.infrastructure.gpio_adapter`).

- The connection lifecycle is managed by the adapter's ``start()`` /
  ``stop()`` methods, invoked by the composition root.  Consumers only
  see the protocol methods (``publish`` / ``subscribe``).

- Topic subscriptions are tracked internally and restored automatically
  on reconnection without the application layer needing to re-subscribe.

- Messages on ``/actual`` topics are silently ignored to prevent
  feedback loops from the adapter's own state publications.

- A ``MessageCallback`` type allows the application layer to receive
  incoming messages without coupling to the MQTT library.

- A ``try/finally`` in the connection loop ensures ``_connected`` is
  always cleared on disconnect, even during cancellation.

See Also:
    ADR-001 §3 — MQTT adapter pluggability rationale.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from velux2mqtt.config import MqttSettings

logger = logging.getLogger(__name__)

#: Callback signature for incoming MQTT messages: ``(topic, payload) → awaitable``.
MessageCallback = Callable[[str, str], Awaitable[None]]


# ---------------------------------------------------------------------------
# MqttClientAdapter — real aiomqtt client
# ---------------------------------------------------------------------------


@dataclass
class MqttClientAdapter:
    """MQTT adapter wrapping ``aiomqtt`` with reconnection and routing.

    Manages the MQTT client lifecycle, including automatic reconnection
    with configurable interval.  Incoming messages are dispatched to
    registered callbacks.  Messages on topics ending with ``/actual``
    are silently ignored to prevent feedback loops.

    The adapter satisfies :class:`~velux2mqtt.ports.MqttPort` via
    structural subtyping — no base-class inheritance required.

    Args:
        settings: MQTT broker connection and topic configuration.
    """

    settings: MqttSettings
    _callbacks: list[MessageCallback] = field(
        default_factory=list, init=False, repr=False
    )
    _subscriptions: set[str] = field(default_factory=set, init=False, repr=False)
    _client: Any = field(default=None, init=False, repr=False)
    _listen_task: asyncio.Task[None] | None = field(
        default=None, init=False, repr=False
    )
    _connected: asyncio.Event = field(
        default_factory=asyncio.Event, init=False, repr=False
    )
    _stopping: bool = field(default=False, init=False, repr=False)

    # --- MqttPort protocol methods -----------------------------------------

    async def publish(
        self,
        topic: str,
        payload: str,
        *,
        retain: bool = False,
        qos: int = 1,
    ) -> None:
        """Publish a message to an MQTT topic.

        Raises:
            RuntimeError: If the adapter is not connected to a broker.
        """
        if self._client is None:
            msg = "MQTT client is not connected"
            raise RuntimeError(msg)
        await self._client.publish(topic, payload, qos=qos, retain=retain)
        logger.debug("Published to %s (qos=%d, retain=%s)", topic, qos, retain)

    async def subscribe(self, topic: str) -> None:
        """Subscribe to an MQTT topic pattern.

        The subscription is tracked internally and will be restored
        automatically on reconnection.
        """
        self._subscriptions.add(topic)
        if self._client is not None:
            await self._client.subscribe(topic, qos=self.settings.qos)
            logger.info("Subscribed to %s", topic)

    # --- Callback registration ---------------------------------------------

    def on_message(self, callback: MessageCallback) -> None:
        """Register a callback for incoming MQTT messages.

        Callbacks receive ``(topic: str, payload: str)`` for every
        message that passes the topic filter (not on ``/actual``).
        Multiple callbacks are called in registration order.
        """
        self._callbacks.append(callback)

    # --- Lifecycle management ----------------------------------------------

    async def start(self) -> None:
        """Start the MQTT connection loop as a background task.

        The loop connects to the broker, restores subscriptions, and
        dispatches incoming messages.  On disconnection it waits
        ``reconnect_interval`` seconds before reconnecting.
        """
        self._stopping = False
        self._listen_task = asyncio.create_task(self._connection_loop())
        logger.info("MQTT adapter started")

    async def stop(self) -> None:
        """Stop the MQTT connection loop and disconnect.

        Idempotent — safe to call multiple times.
        """
        self._stopping = True
        self._connected.clear()
        if self._listen_task is not None:
            self._listen_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._listen_task
            self._listen_task = None
        self._client = None
        logger.info("MQTT adapter stopped")

    @property
    def is_connected(self) -> bool:
        """Whether the adapter currently has an active broker connection."""
        return self._connected.is_set()

    # --- Internal machinery ------------------------------------------------

    async def _connection_loop(self) -> None:
        """Reconnecting connection loop — runs as a background task.

        Each iteration:
        1. Connects to the broker via ``aiomqtt.Client``
        2. Restores all tracked subscriptions
        3. Iterates ``client.messages`` dispatching to callbacks
        4. On ``MqttError``, waits ``reconnect_interval`` and retries

        ``aiomqtt`` is imported here (lazy) so the module can be loaded
        without the library installed — same pattern as ``RPi.GPIO``.
        """
        import aiomqtt

        while not self._stopping:
            try:
                password = (
                    self.settings.password.get_secret_value()
                    if self.settings.password
                    else None
                )
                async with aiomqtt.Client(
                    hostname=self.settings.host,
                    port=self.settings.port,
                    username=self.settings.username,
                    password=password,
                    identifier=self.settings.client_id,
                ) as client:
                    self._client = client

                    # Restore all tracked subscriptions
                    for topic in self._subscriptions:
                        await client.subscribe(topic, qos=self.settings.qos)

                    self._connected.set()
                    logger.info(
                        "Connected to MQTT broker %s:%d",
                        self.settings.host,
                        self.settings.port,
                    )

                    # Message dispatch loop
                    async for message in client.messages:
                        await self._dispatch(message)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._client = None
                self._connected.clear()
                if not self._stopping:
                    logger.warning(
                        "MQTT connection lost: %s — reconnecting in %.1fs",
                        exc,
                        self.settings.reconnect_interval,
                    )
                    await asyncio.sleep(self.settings.reconnect_interval)

    async def _dispatch(self, message: Any) -> None:
        """Route an incoming message to registered callbacks.

        Messages on topics ending with ``/actual`` are silently ignored
        to prevent feedback loops from own state publications.
        ``None`` payloads (MQTT delete-retained) are also skipped.
        """
        topic = str(message.topic)

        if self._should_ignore(topic):
            logger.debug("Ignoring own publication on %s", topic)
            return

        raw = message.payload
        if raw is None:
            logger.debug("Ignoring empty payload on %s", topic)
            return

        payload = (
            raw.decode("utf-8") if isinstance(raw, bytes | bytearray) else str(raw)
        )

        for callback in self._callbacks:
            try:
                await callback(topic, payload)
            except Exception:
                logger.exception("Error in message callback for topic %s", topic)

    @staticmethod
    def _should_ignore(topic: str) -> bool:
        """Return ``True`` if the topic should be ignored.

        Topics ending with ``/actual`` are the adapter's own state
        publications — processing them would create a feedback loop.
        """
        return topic.endswith("/actual")


# ---------------------------------------------------------------------------
# MockMqttAdapter — test double
# ---------------------------------------------------------------------------


@dataclass
class MockMqttAdapter:
    """Test double that records ``publish()`` and ``subscribe()`` calls.

    Useful for unit and integration tests that need to verify which
    messages were published and which topics were subscribed — without
    requiring a real MQTT broker.

    Attributes:
        published: Chronological list of ``(topic, payload, retain, qos)``.
        subscriptions: Ordered list of subscribed topic patterns.
    """

    published: list[tuple[str, str, bool, int]] = field(default_factory=list)
    subscriptions: list[str] = field(default_factory=list)

    async def publish(
        self,
        topic: str,
        payload: str,
        *,
        retain: bool = False,
        qos: int = 1,
    ) -> None:
        """Record the publish without broker I/O."""
        self.published.append((topic, payload, retain, qos))

    async def subscribe(self, topic: str) -> None:
        """Record the subscription without broker I/O."""
        self.subscriptions.append(topic)

    # --- Test helpers ------------------------------------------------------

    @property
    def publish_count(self) -> int:
        """Number of ``publish()`` calls recorded."""
        return len(self.published)

    @property
    def subscribe_count(self) -> int:
        """Number of ``subscribe()`` calls recorded."""
        return len(self.subscriptions)

    def reset(self) -> None:
        """Clear recorded calls."""
        self.published.clear()
        self.subscriptions.clear()

    def get_messages_for(self, topic: str) -> list[tuple[str, bool, int]]:
        """Get ``(payload, retain, qos)`` tuples published to *topic*."""
        return [
            (payload, retain, qos)
            for t, payload, retain, qos in self.published
            if t == topic
        ]


# ---------------------------------------------------------------------------
# NullMqttAdapter — silent fallback
# ---------------------------------------------------------------------------


@dataclass
class NullMqttAdapter:
    """Silent no-op adapter for environments without an MQTT broker.

    All methods succeed without side effects.  Useful for running the
    application in GPIO-only mode or for testing components that don't
    need MQTT verification.
    """

    async def publish(
        self,
        topic: str,
        payload: str,
        *,
        retain: bool = False,  # noqa: ARG002
        qos: int = 1,  # noqa: ARG002
    ) -> None:
        """Log and discard — no broker interaction."""
        logger.debug("NullMqtt publish: %s → %s (skipped)", topic, payload)

    async def subscribe(self, topic: str) -> None:
        """No-op — nothing to subscribe."""
        logger.debug("NullMqtt subscribe: %s (skipped)", topic)
