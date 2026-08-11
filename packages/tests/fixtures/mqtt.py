"""Shared MQTT test doubles for cosalette tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from cosalette._mqtt import ConnectCallback, MessageCallback


@dataclass
class FakeConnectAwareMqttClient:
    """Test double that records publishes AND supports connect callbacks.

    Unlike :class:`MockMqttClient`, this double implements
    ``add_connect_callback`` so ``isinstance(..., MqttConnectAware)`` is
    ``True``.  Tests control the connection lifecycle explicitly via
    ``simulate_connect()``.

    Intentionally NOT a ``MqttLifecycle`` (no start/stop) so tests remain
    fully synchronous over connect timing.

    See Also:
        ADR-006 — Interface Segregation (narrow protocols).
        ADR-012 — Health and availability reporting.
    """

    published: list[tuple[str, str, bool, int]] = field(default_factory=list)
    subscriptions: list[str] = field(default_factory=list)
    raise_on_publish: Exception | None = field(default=None, repr=False)
    _callbacks: list[MessageCallback] = field(
        default_factory=list, init=False, repr=False
    )
    _connect_callbacks: list[ConnectCallback] = field(
        default_factory=list, init=False, repr=False
    )

    # -- MqttPort methods --------------------------------------------------

    async def publish(
        self,
        topic: str,
        payload: str | dict[str, Any],
        *,
        retain: bool = False,
        qos: int = 1,
    ) -> None:
        """Record a publish call, or raise if ``raise_on_publish`` is set."""
        if self.raise_on_publish is not None:
            raise self.raise_on_publish
        if isinstance(payload, dict):
            from cosalette._json import dumps

            payload = dumps(payload)
        self.published.append((topic, payload, retain, qos))

    async def subscribe(self, topic: str) -> None:
        """Record a subscribe call."""
        self.subscriptions.append(topic)

    # -- MqttMessageHandler ------------------------------------------------

    def on_message(self, callback: MessageCallback) -> None:
        """Register an inbound-message callback."""
        self._callbacks.append(callback)

    # -- MqttConnectAware --------------------------------------------------

    def add_connect_callback(self, callback: ConnectCallback) -> None:
        """Register a callback invoked after each successful (re)connect."""
        self._connect_callbacks.append(callback)

    async def simulate_connect(self) -> None:
        """Invoke all registered connect callbacks (test helper).

        Unlike the production :meth:`MqttClient._run_connect_callbacks`, exceptions
        from callbacks are NOT caught and swallowed — they propagate to the caller.
        Do not register callbacks that raise when testing via this double; the
        production error-isolation behavior is covered separately in
        :mod:`tests.unit.mqtt.test_mqtt_connect_callbacks`.
        """
        for callback in list(self._connect_callbacks):
            await callback()

    # -- Test helpers -------------------------------------------------------

    def reset(self) -> None:
        """Clear all recorded data and failure injection."""
        self.published.clear()
        self.subscriptions.clear()
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

    @property
    def publish_count(self) -> int:
        """Number of recorded publishes."""
        return len(self.published)


@dataclass
class FakeLifecycleConnectAwareMqttClient:
    """Full-surface MQTT double mirroring the production :class:`MqttClient`.

    Unlike :class:`FakeConnectAwareMqttClient`, this double additionally
    implements :class:`MqttLifecycle` (``start``/``stop``) and exposes
    ``is_connected``, so it satisfies every optional MQTT capability the
    framework probes: ``MqttPort``, ``MqttMessageHandler``,
    ``MqttLifecycle`` and ``MqttConnectAware``.

    It exists to regression-test schema-enforcement wrapping:
    when the live client is wrapped by ``ValidatingMqttPort``, the wrapper
    must still transparently start/stop the inner client and register the
    F-1/F-2 connect reannounce hook. Tests drive (re)connects explicitly
    via :meth:`simulate_connect`.

    See Also:
        ADR-006 — Interface Segregation (narrow capability protocols).
        ADR-012 — Health and availability reporting.
    """

    published: list[tuple[str, str, bool, int]] = field(default_factory=list)
    subscriptions: list[str] = field(default_factory=list)
    raise_on_publish: Exception | None = field(default=None, repr=False)
    started: bool = field(default=False, init=False)
    stopped: bool = field(default=False, init=False)
    _connected: bool = field(default=False, init=False, repr=False)
    _callbacks: list[MessageCallback] = field(
        default_factory=list, init=False, repr=False
    )
    _connect_callbacks: list[ConnectCallback] = field(
        default_factory=list, init=False, repr=False
    )

    # -- MqttPort methods --------------------------------------------------

    async def publish(
        self,
        topic: str,
        payload: str | dict[str, Any],
        *,
        retain: bool = False,
        qos: int = 1,
    ) -> None:
        """Record a publish call, or raise if ``raise_on_publish`` is set."""
        if self.raise_on_publish is not None:
            raise self.raise_on_publish
        if isinstance(payload, dict):
            from cosalette._json import dumps

            payload = dumps(payload)
        self.published.append((topic, payload, retain, qos))

    async def subscribe(self, topic: str) -> None:
        """Record a subscribe call."""
        self.subscriptions.append(topic)

    # -- MqttMessageHandler ------------------------------------------------

    def on_message(self, callback: MessageCallback) -> None:
        """Register an inbound-message callback."""
        self._callbacks.append(callback)

    # -- MqttLifecycle -----------------------------------------------------

    async def start(self) -> None:
        """Record startup and mark the client connected."""
        self.started = True
        self._connected = True

    async def stop(self) -> None:
        """Record shutdown and mark the client disconnected."""
        self.stopped = True
        self._connected = False

    # -- MqttConnectAware --------------------------------------------------

    def add_connect_callback(self, callback: ConnectCallback) -> None:
        """Register a callback invoked after each successful (re)connect."""
        self._connect_callbacks.append(callback)

    @property
    def is_connected(self) -> bool:
        """Whether the client is currently 'connected' to the broker."""
        return self._connected

    async def simulate_connect(self) -> None:
        """Mark connected and invoke all registered connect callbacks.

        Exceptions from callbacks propagate (mirroring
        :meth:`FakeConnectAwareMqttClient.simulate_connect`).
        """
        self._connected = True
        for callback in list(self._connect_callbacks):
            await callback()

    # -- Test helpers -------------------------------------------------------

    def reset(self) -> None:
        """Clear recorded publishes/subscriptions and failure injection."""
        self.published.clear()
        self.subscriptions.clear()
        self.raise_on_publish = None

    def get_messages_for(self, topic: str) -> list[tuple[str, bool, int]]:
        """Return ``(payload, retain, qos)`` tuples for *topic*."""
        return [
            (payload, retain, qos)
            for t, payload, retain, qos in self.published
            if t == topic
        ]

    @property
    def publish_count(self) -> int:
        """Number of recorded publishes."""
        return len(self.published)
