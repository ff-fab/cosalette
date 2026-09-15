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
from typing import Any, Literal, NamedTuple

from cosalette._clock import ClockPort, SystemClock
from cosalette._mqtt import ConnectCallback, MessageCallback, WillConfig
from cosalette._settings import MqttSettings

logger = logging.getLogger(__name__)

_MAX_RETAINED_LEDGER_ENTRIES = 1000
_MAX_RETAINED_LEDGER_BYTES = 16 * 1024 * 1024


class _RetainedEntry(NamedTuple):
    """A single retained-publish ledger entry (ADR-078)."""

    payload: str
    qos: int
    published_at: float


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
        ADR-078 — Retained message expiry and refresh ledger.
    """

    settings: MqttSettings
    will: WillConfig | None = None
    clock: ClockPort = field(default_factory=SystemClock)

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
    _ever_connected: bool = field(default=False, init=False, repr=False)
    _tls_hint_logged: bool = field(default=False, init=False, repr=False)
    _on_connect_callbacks: list[ConnectCallback] = field(
        default_factory=list,
        init=False,
        repr=False,
    )
    _retained: dict[str, _RetainedEntry] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )
    _refresh_task: asyncio.Task[None] | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _connect_callback_task: asyncio.Task[None] | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _retained_publish_lock: asyncio.Lock = field(
        default_factory=asyncio.Lock,
        init=False,
        repr=False,
    )
    _connect_ready: asyncio.Event = field(
        default_factory=asyncio.Event,
        init=False,
        repr=False,
    )
    _connection_generation: int = field(default=0, init=False, repr=False)
    _connection_protocol_version: Literal["3.1.1", "5"] | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _connection_message_expiry_interval: int | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _connected_at: float | None = field(default=None, init=False, repr=False)
    _v5_hint_logged: bool = field(default=False, init=False, repr=False)

    @property
    def _expiry_active(self) -> bool:
        """Whether MQTT 5 expiry is active for the current lifecycle."""
        protocol_version = self._connection_protocol_version
        if protocol_version is None:
            protocol_version = self.settings.protocol_version
        return protocol_version == "5"

    @property
    def _message_expiry_interval(self) -> int:
        """Return the lifecycle-stable expiry interval for MQTT 5 packets."""
        return (
            self._connection_message_expiry_interval
            if self._connection_message_expiry_interval is not None
            else self.settings.message_expiry_interval
        )

    @property
    def _publish_properties(self) -> Any:
        """Build fresh paho ``Properties`` for a retained MQTT 5 publish.

        Not cached: each packet receives a separate properties object while
        retaining the interval captured at the start of this lifecycle.
        """
        from paho.mqtt.packettypes import PacketTypes  # noqa: PLC0415
        from paho.mqtt.properties import Properties  # noqa: PLC0415

        props = Properties(PacketTypes.PUBLISH)
        props.MessageExpiryInterval = self._message_expiry_interval
        return props

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
        if retain and self._expiry_active:
            async with self._retained_publish_lock:
                self._record_retained_publish(topic, payload, qos)
                await self._publish_raw(topic, payload, retain=True, qos=qos)
        else:
            await self._publish_raw(topic, payload, retain=retain, qos=qos)
        logger.debug(
            "Published to %s (qos=%d, retain=%s)",
            topic,
            qos,
            retain,
        )

    def _record_retained_publish(self, topic: str, payload: str, qos: int) -> None:
        """Record a retained write, enforcing the bounded-ledger policy."""
        if payload == "":
            self._retained.pop(topic, None)
            return
        if (
            topic not in self._retained
            and len(self._retained) >= _MAX_RETAINED_LEDGER_ENTRIES
        ):
            msg = (
                "Retained message ledger limit reached "
                f"({_MAX_RETAINED_LEDGER_ENTRIES} topics); publish an empty retained "
                "payload to remove an existing topic before adding another"
            )
            raise RuntimeError(msg)
        topic_bytes = len(topic.encode("utf-8"))
        payload_bytes = len(payload.encode("utf-8"))
        existing_entry = self._retained.get(topic)
        existing_bytes = (
            topic_bytes + len(existing_entry.payload.encode("utf-8"))
            if existing_entry is not None
            else 0
        )
        ledger_bytes = sum(
            len(ledger_topic.encode("utf-8")) + len(entry.payload.encode("utf-8"))
            for ledger_topic, entry in self._retained.items()
        )
        if (
            ledger_bytes - existing_bytes + topic_bytes + payload_bytes
            > _MAX_RETAINED_LEDGER_BYTES
        ):
            msg = (
                "Retained message ledger byte limit reached "
                f"({_MAX_RETAINED_LEDGER_BYTES} bytes); reduce retained payload "
                "size or clear an existing topic before publishing"
            )
            raise RuntimeError(msg)
        self._retained[topic] = _RetainedEntry(payload, qos, self.clock.now())

    async def _publish_raw(
        self,
        topic: str,
        payload: str,
        *,
        retain: bool,
        qos: int,
    ) -> None:
        """Publish without touching the ledger or re-serialising the payload.

        Internal path shared by :meth:`publish` and the refresh task/loop.
        """
        if retain and self._expiry_active:
            await self._client.publish(
                topic,
                payload,
                retain=retain,
                qos=qos,
                properties=self._publish_properties,
            )
        else:
            await self._client.publish(
                topic,
                payload,
                retain=retain,
                qos=qos,
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

    async def _run_connect_callbacks(
        self,
        generation: int | None = None,
        connected_at: float | None = None,
    ) -> None:
        """Invoke registered connect callbacks (guarded, fire-and-forget).

        When MQTT 5 expiry is active, follows up with a guarded refresh of
        the retained ledger entries published before this connect instant,
        so the callbacks' own (fresher) reannounce publishes are never
        republished twice and always win the ordering (ADR-078).
        """
        connected_at = self.clock.now() if connected_at is None else connected_at
        connection_generation = (
            self._connection_generation if generation is None else generation
        )
        try:
            for callback in list(self._on_connect_callbacks):
                try:
                    await callback()
                except Exception:
                    logger.exception("MQTT connect callback failed")
            if self._expiry_active:
                try:
                    await self._refresh_retained(before=connected_at)
                except Exception:
                    logger.exception("MQTT post-connect ledger refresh failed")
        finally:
            if connection_generation == self._connection_generation:
                self._connect_ready.set()

    # -- Lifecycle ----------------------------------------------------------

    async def start(self) -> None:
        """Start the background connection loop."""
        if self._listen_task is not None and not self._listen_task.done():
            logger.debug("MqttClient.start() called while already running")
            return
        await self._cancel_connect_callback_task()
        await self._cancel_refresh_task()
        self._connection_protocol_version = self.settings.protocol_version
        self._connection_message_expiry_interval = self.settings.message_expiry_interval
        self._ever_connected = False
        self._tls_hint_logged = False
        self._v5_hint_logged = False
        self._log_transport_posture()
        # Build SSL context once — avoids re-reading CA file on every reconnect.
        if self._ssl_context is None:
            self._ssl_context = await asyncio.to_thread(self._build_ssl_context)
        self._stopping = False
        self._listen_task = asyncio.create_task(
            self._connection_loop(),
            name="cosalette-mqtt-connection-loop",
        )
        if self._expiry_active:
            period = self._message_expiry_interval / 3
            logger.info(
                "MQTT 5 enabled: message_expiry_interval=%ds, refresh period=%.0fs",
                self._message_expiry_interval,
                period,
            )
            self._refresh_task = asyncio.create_task(
                self._refresh_loop(),
                name="cosalette-mqtt-refresh-loop",
            )

    async def stop(self) -> None:
        """Stop the connection loop and clean up.

        Idempotent — safe to call multiple times.
        """
        self._stopping = True
        await self._cancel_connect_callback_task()
        await self._cancel_refresh_task()
        if self._listen_task is not None:
            self._listen_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._listen_task
            self._listen_task = None
        self._client = None
        self._connected.clear()
        self._connect_ready.clear()
        self._connection_generation += 1
        self._connected_at = None
        self._retained.clear()
        self._connection_protocol_version = None
        self._connection_message_expiry_interval = None

    async def _cancel_connect_callback_task(self) -> None:
        """Cancel and await the active connect callback task, if any."""
        callback_task = self._connect_callback_task
        if callback_task is None:
            return
        if not callback_task.done():
            callback_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await callback_task
        self._connect_callback_task = None

    async def _cancel_refresh_task(self) -> None:
        """Cancel and await the current refresh task, if it is still active."""
        refresh_task = self._refresh_task
        if refresh_task is None:
            return
        if not refresh_task.done():
            refresh_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await refresh_task
        self._refresh_task = None

    @property
    def is_connected(self) -> bool:
        """Whether the client is currently connected to the broker."""
        return self._connected.is_set()

    # -- Internal -----------------------------------------------------------

    _LOCAL_HOSTS = frozenset(
        {"localhost", "127.0.0.1", "::1"}  # noqa: S104 — allowlist entry, not a bind
    )

    def _log_transport_posture(self) -> None:
        """Warn about plaintext credentials or anonymous broker joins.

        Deployment hardening is ultimately broker-side, but the framework
        should make an insecure transport configuration loud (CWE-1188).
        Loopback addresses are exempt; other private-network brokers are not.
        """
        host = self.settings.host.strip().lower()
        if not self.settings.tls and host not in self._LOCAL_HOSTS:
            if self.settings.username is not None or self.settings.password is not None:
                logger.warning(
                    "MQTT credentials are configured but TLS is disabled — "
                    "the password will traverse '%s' in plaintext. Enable "
                    "MQTT__TLS=true for non-local brokers.",
                    host,
                )
            elif self.settings.username is None and self.settings.password is None:
                logger.warning(
                    "Connecting to non-local broker '%s' anonymously "
                    "(no username/password). Ensure the broker enforces "
                    "authentication and per-prefix ACLs.",
                    host,
                )

    # Transport handshake failures: the broker accepted the TCP connection but
    # the TLS handshake did not complete.  aiomqtt flattens these into
    # MqttError and drops the exception chain on some paths, so the message
    # text is matched too -- a plaintext mosquitto yields either
    # "[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred ..." or
    # "[Errno 104] Connection reset by peer", depending on timing.
    _HANDSHAKE_ERRORS = (
        ssl.SSLError,
        ConnectionResetError,
        BrokenPipeError,
        EOFError,
    )
    _HANDSHAKE_MARKERS = ("ssl:", "connection reset", "broken pipe", "eof occurred")
    _CERTIFICATE_MARKERS = ("certificate verify failed", "certificateverificationerror")
    _V5_PROTOCOL_REJECTION_MARKERS = (
        "unsupported protocol version",
        "unacceptable protocol version",
        "unsupported protocol level",
        "protocol version not supported",
    )

    @classmethod
    def _exception_chain(cls, exc: BaseException) -> tuple[BaseException, ...]:
        """Return the unique exceptions reachable through cause/context links."""
        seen: set[int] = set()
        chain: list[BaseException] = []
        current: BaseException | None = exc
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            chain.append(current)
            current = current.__cause__ or current.__context__
        return tuple(chain)

    @classmethod
    def _is_certificate_failure(cls, chain: tuple[BaseException, ...]) -> bool:
        """Return whether the failure proves a TLS listener responded."""
        if any(isinstance(error, ssl.SSLCertVerificationError) for error in chain):
            return True
        text = " ".join(str(error).lower() for error in chain)
        return any(marker in text for marker in cls._CERTIFICATE_MARKERS)

    @classmethod
    def _is_handshake_failure(cls, exc: BaseException) -> bool:
        """Report whether *exc* looks like a failed transport handshake."""
        chain = cls._exception_chain(exc)
        if cls._is_certificate_failure(chain):
            return False
        if any(isinstance(error, cls._HANDSHAKE_ERRORS) for error in chain):
            return True
        text = " ".join(str(error).lower() for error in chain)
        return any(marker in text for marker in cls._HANDSHAKE_MARKERS)

    @classmethod
    def _is_v5_protocol_rejection(cls, exc: BaseException) -> bool:
        """Return whether a broker credibly rejected the MQTT protocol level."""
        chain = cls._exception_chain(exc)
        for error in chain:
            reason_code = getattr(error, "rc", None)
            if isinstance(reason_code, int) and reason_code in {1, 132}:
                return True
        text = " ".join(str(error).lower() for error in chain)
        return any(marker in text for marker in cls._V5_PROTOCOL_REJECTION_MARKERS)

    def _log_tls_mismatch_hint(self, exc: BaseException) -> None:
        """Name a likely TLS-against-plaintext-broker mismatch, once per run.

        ADR-062 made ``tls`` default-on, so an app upgraded while pointed at a
        broker with no TLS listener loses connectivity with an error that is
        indistinguishable from a transient network fault -- and the reconnect
        backoff then makes a permanent misconfiguration look like a flaky
        broker that is slowly getting worse.

        The framework cannot prove the broker is plaintext, so this is
        advisory and never fatal: it does not raise, suppress the reconnect
        warning, or change backoff.  It fires only before the first successful
        connection, so an established deployment stays quiet on ordinary
        reconnects.  Extends the intent of :meth:`_log_transport_posture` from
        TLS *disabled* to TLS *misconfigured* (CWE-1188).
        """
        if (
            self._tls_hint_logged
            or self._ever_connected
            or not self.settings.tls
            or not self._is_handshake_failure(exc)
        ):
            return
        self._tls_hint_logged = True
        logger.error(
            "TLS handshake with %s:%d failed (%s) — is the broker listening "
            "in plaintext? MQTT TLS is enabled by default (ADR-062); set "
            "MQTT__TLS=false if this broker has no TLS listener.",
            self.settings.host,
            self.settings.port,
            exc,
        )

    def _log_v5_connection_hint(self, exc: BaseException) -> None:
        """Name a likely MQTT-5-against-3.1.1-only-broker mismatch, once per run.

        Mirrors :meth:`_log_tls_mismatch_hint`: advisory only, fires just
        before the first successful connection, and never raises, suppresses
        the reconnect warning, or changes backoff. There is no protocol
        fallback (ADR-078) — a v5 CONNECT a 3.1.1-only broker refuses is an
        ordinary connection failure that goes through the reconnect backoff.
        """
        if (
            self._v5_hint_logged
            or self._ever_connected
            or not self._expiry_active
            or not self._is_v5_protocol_rejection(exc)
        ):
            return
        self._v5_hint_logged = True
        logger.error(
            "MQTT 5 connection to %s:%d failed (%s) — does the broker support "
            "MQTT 5? Set MQTT__PROTOCOL_VERSION=3.1.1 to fall back to MQTT 3.1.1.",
            self.settings.host,
            self.settings.port,
            exc,
        )

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
    def _build_will(
        aiomqtt_mod: Any,
        will_cfg: WillConfig | None,
        *,
        properties: Any = None,
    ) -> Any:
        """Translate a :class:`WillConfig` into an ``aiomqtt.Will``, or *None*."""
        if will_cfg is not None:
            will_kwargs: dict[str, Any] = {
                "topic": will_cfg.topic,
                "payload": will_cfg.payload,
                "qos": will_cfg.qos,
                "retain": will_cfg.retain,
            }
            if properties is not None:
                # 3.1.1 path stays byte-identical: no properties= kwarg at all.
                will_kwargs["properties"] = properties
            return aiomqtt_mod.Will(**will_kwargs)
        return None

    def _build_connect_kwargs(self, aiomqtt_mod: Any) -> dict[str, Any]:
        """Build keyword arguments for ``aiomqtt.Client``."""
        password = self._extract_password()
        will_properties = None
        if self._expiry_active:
            from paho.mqtt.packettypes import PacketTypes  # noqa: PLC0415
            from paho.mqtt.properties import Properties  # noqa: PLC0415

            will_properties = Properties(PacketTypes.WILLMESSAGE)
            will_properties.MessageExpiryInterval = self._message_expiry_interval
        will = self._build_will(aiomqtt_mod, self.will, properties=will_properties)
        kwargs: dict[str, Any] = {
            "hostname": self.settings.host,
            "port": self.settings.port,
            "username": self.settings.username,
            "password": password,
            "identifier": self.settings.client_id or None,
            "will": will,
        }
        if self._expiry_active:
            kwargs["protocol"] = aiomqtt_mod.ProtocolVersion.V5
        if self._ssl_context is not None:
            kwargs["tls_context"] = self._ssl_context
        return kwargs

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
                client_kwargs = self._build_connect_kwargs(aiomqtt)

                async with aiomqtt.Client(**client_kwargs) as client:
                    self._client = client
                    # __aenter__ completes the MQTT connection before
                    # subscription restoration begins.
                    self._ever_connected = True
                    try:
                        # Restore tracked subscriptions
                        for topic in list(self._subscriptions):
                            await client.subscribe(
                                topic,
                                qos=1,
                            )

                        self._connected.set()
                        self._connection_generation += 1
                        generation = self._connection_generation
                        connected_at = self.clock.now()
                        self._connected_at = connected_at
                        self._connect_ready.clear()
                        # Reset backoff on successful connection
                        delay = self.settings.reconnect_interval
                        logger.info(
                            "MQTT connected to %s:%d",
                            self.settings.host,
                            self.settings.port,
                        )

                        self._connect_callback_task = asyncio.create_task(
                            self._run_connect_callbacks(generation, connected_at),
                            name="cosalette-mqtt-connect-callbacks",
                        )

                        async for message in client.messages:
                            await self._dispatch(message)
                    finally:
                        await self._cancel_connect_callback_task()
                        self._connected.clear()
                        self._connect_ready.clear()
                        self._connected_at = None
                        self._client = None

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._log_tls_mismatch_hint(exc)
                self._log_v5_connection_hint(exc)
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

    async def _refresh_loop(self) -> None:
        """Republish the retained ledger on a fixed tick-to-tick cadence.

        Period is ``message_expiry_interval / 3`` (ADR-078): after a
        successful pass the next two ticks both fall inside the window, so a
        single failed or missed pass never lets a topic expire. The cadence
        is measured tick to tick, but an overlong pass resynchronizes by
        waiting a full period so it cannot spin back-to-back.
        """
        next_tick = self.clock.now()
        while not self._stopping:
            tick_start = self.clock.now()
            period = self._message_expiry_interval / 3
            try:
                if self._connected.is_set():
                    before = (
                        None if self._connect_ready.is_set() else self._connected_at
                    )
                    await self._refresh_retained(before=before)
                    elapsed = self.clock.now() - tick_start
                    if elapsed > period:
                        logger.warning(
                            "Retained ledger refresh pass took %.1fs, "
                            "exceeding the %.0fs period",
                            elapsed,
                            period,
                        )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning(
                    "Retained ledger refresh pass failed, next tick retries",
                    exc_info=True,
                )
            # Skip missed ticks rather than entering a back-to-back refresh loop.
            next_tick += period
            now = self.clock.now()
            if next_tick <= now:
                next_tick = now + period
            await self.clock.sleep(next_tick - now)

    async def _refresh_retained(self, *, before: float | None = None) -> None:
        """Republish ledger entries, optionally only those older than *before*.

        Reads each entry from the ledger at publish time (never a snapshot),
        so a concurrent application publish is never overwritten by a stale
        refresh. Sequential — one publish in flight at a time.
        """
        async with self._retained_publish_lock:
            for topic in list(self._retained):
                entry = self._retained.get(topic)
                if entry is None:
                    continue
                if before is not None and entry.published_at >= before:
                    continue
                await self._publish_raw(
                    topic,
                    entry.payload,
                    retain=True,
                    qos=entry.qos,
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
