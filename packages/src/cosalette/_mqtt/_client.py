"""Production MQTT client adapter.

Contains the real aiomqtt-backed :class:`MqttClient` with exponential-backoff
reconnection.  Extracted from ``_mqtt.py`` to keep protocol definitions and
test doubles separate from the production adapter.

See Also:
    ADR-006 — Hexagonal architecture (lazy imports).
    ADR-012 — LWT / availability via ``WillConfig``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import ssl
from dataclasses import dataclass, field
from typing import Any

from cosalette._mqtt import ConnectCallback, MessageCallback, WillConfig
from cosalette._settings import MqttSettings

logger = logging.getLogger(__name__)


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
    _ssl_context: ssl.SSLContext | None = field(default=None, init=False, repr=False)
    _on_connect_callbacks: list[ConnectCallback] = field(
        default_factory=list,
        init=False,
        repr=False,
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
        """Publish a message to the broker.

        Raises:
            RuntimeError: If the client is not connected.
        """
        if self._client is None:
            msg = "MqttClient is not connected"
            raise RuntimeError(msg)
        if isinstance(payload, dict):
            from cosalette._json import dumps

            payload = dumps(payload)
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
                qos=1,
            )

    # -- Callback registration ---------------------------------------------

    def on_message(self, callback: MessageCallback) -> None:
        """Register a callback for inbound messages."""
        self._callbacks.append(callback)

    def add_connect_callback(self, callback: ConnectCallback) -> None:
        """Register a callback invoked after each successful (re)connect.

        Callbacks run concurrently with inbound message dispatch; keep them
        fast or they will delay reconnect restores.
        """
        self._on_connect_callbacks.append(callback)

    async def _run_connect_callbacks(self) -> None:
        """Invoke registered connect callbacks (guarded, fire-and-forget)."""
        for callback in list(self._on_connect_callbacks):
            try:
                await callback()
            except Exception:
                logger.exception("MQTT connect callback failed")

    # -- Lifecycle ----------------------------------------------------------

    async def start(self) -> None:
        """Start the background connection loop."""
        if self._listen_task is not None and not self._listen_task.done():
            logger.debug("MqttClient.start() called while already running")
            return
        # Build SSL context once — avoids re-reading CA file on every reconnect.
        if self._ssl_context is None:
            self._ssl_context = self._build_ssl_context()
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

    def _extract_password(self) -> str | None:
        """Return the MQTT password as a plain string, or *None*."""
        if self.settings.password is not None:
            return self.settings.password.get_secret_value()
        return None

    def _build_ssl_context(self) -> ssl.SSLContext | None:
        """Build an SSL context for broker TLS, or *None* when disabled."""
        if not self.settings.tls:
            return None

        context = ssl.create_default_context(cafile=self.settings.tls_ca_file)
        if self.settings.tls_cert_file is not None:
            context.load_cert_chain(
                certfile=self.settings.tls_cert_file,
                keyfile=self.settings.tls_key_file,
            )
        return context

    @staticmethod
    def _build_will(aiomqtt_mod: Any, will_cfg: WillConfig | None) -> Any:
        """Translate a :class:`WillConfig` into an ``aiomqtt.Will``, or *None*."""
        if will_cfg is not None:
            return aiomqtt_mod.Will(
                topic=will_cfg.topic,
                payload=will_cfg.payload,
                qos=will_cfg.qos,
                retain=will_cfg.retain,
            )
        return None

    async def _connection_loop(self) -> None:
        """Maintain a persistent connection with auto-reconnect.

        Uses **exponential backoff with jitter** on failures:
        the delay starts at ``reconnect_interval``, doubles after
        each consecutive failure (capped at ``reconnect_max_interval``),
        and resets to the base value on a successful connection.
        A ±20 % random jitter is applied to prevent thundering-herd
        reconnections when many clients share a broker.

        ``aiomqtt`` is imported lazily here so that ``MockMqttClient``
        and ``NullMqttClient`` work without the dependency.
        """
        try:
            import aiomqtt  # noqa: PLC0415
        except ModuleNotFoundError as exc:
            msg = "aiomqtt is required to use MqttClient"
            raise RuntimeError(msg) from exc

        delay = self.settings.reconnect_interval

        while not self._stopping:
            try:
                password = self._extract_password()
                will = self._build_will(aiomqtt, self.will)
                client_kwargs: dict[str, Any] = {
                    "hostname": self.settings.host,
                    "port": self.settings.port,
                    "username": self.settings.username,
                    "password": password,
                    "identifier": self.settings.client_id or None,
                    "will": will,
                }
                if self._ssl_context is not None:
                    client_kwargs["ssl_context"] = self._ssl_context

                async with aiomqtt.Client(**client_kwargs) as client:
                    self._client = client
                    try:
                        # Restore tracked subscriptions
                        for topic in list(self._subscriptions):
                            await client.subscribe(
                                topic,
                                qos=1,
                            )

                        self._connected.set()
                        # Reset backoff on successful connection
                        delay = self.settings.reconnect_interval
                        logger.info(
                            "MQTT connected to %s:%d",
                            self.settings.host,
                            self.settings.port,
                        )

                        asyncio.create_task(self._run_connect_callbacks())

                        async for message in client.messages:
                            await self._dispatch(message)
                    finally:
                        self._connected.clear()
                        self._client = None

            except asyncio.CancelledError:
                raise
            except Exception:
                jittered = delay * random.uniform(0.8, 1.2)  # ±20% jitter  # noqa: S311
                logger.warning(
                    "MQTT connection lost, reconnecting in %.1fs",
                    jittered,
                    exc_info=True,
                )
                await asyncio.sleep(jittered)
                delay = min(
                    delay * 2,
                    self.settings.reconnect_max_interval,
                )

    async def _dispatch(self, message: Any) -> None:
        """Decode and fan-out an inbound message to callbacks."""
        # Fast path: drop None payload before any string conversion.
        if message.payload is None:
            logger.debug(
                "Skipping message with None payload on %r",
                str(message.topic),
            )
            return

        topic = str(message.topic)
        raw = message.payload
        cap = self.settings.max_inbound_payload_bytes

        if isinstance(raw, (bytes, bytearray)):
            if len(raw) > cap:
                logger.warning(
                    "Dropping oversized payload on %r (%d bytes exceeds %d-byte cap)",
                    topic,
                    len(raw),
                    cap,
                )
                return
            try:
                payload = raw.decode("utf-8")
            except UnicodeDecodeError:
                logger.warning("Skipping non-UTF-8 payload on %r", topic)
                return
        elif isinstance(raw, str):
            # Some broker adapters or test doubles deliver already-decoded str.
            if len(raw) > cap:
                logger.warning(
                    "Dropping oversized payload on %r (%d chars exceeds %d-byte cap)",
                    topic,
                    len(raw),
                    cap,
                )
                return
            payload = raw
        else:
            logger.warning(
                "Dropping unexpected payload type %s on %r",
                type(raw).__name__,
                topic,
            )
            return

        for cb in self._callbacks:
            try:
                await cb(topic, payload)
            except Exception:
                logger.exception(
                    "Error in message callback for %s",
                    topic,
                )
