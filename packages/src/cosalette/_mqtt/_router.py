"""MQTT command topic routing.

Extracts device names from ``{prefix}/{device}/set`` topics and
dispatches inbound command messages to per-entity worker tasks.
Each registered entity has a dedicated FIFO worker; entities run
concurrently so a slow handler does not stall other devices.

Also supports root-level devices (unnamed) that listen on
``{prefix}/set`` directly.

Topic convention (ADR-002)::

    {prefix}/{device}/set    → command topic (subscribed, routed here)
    {prefix}/set             → root device command topic (when registered)
    {prefix}/{device}/state  → state topic (published, not routed)

The router is an internal component — not part of cosalette's public API.
Users register command handlers via ``DeviceContext.on_command()``;
the framework wires them into the router automatically.

See Also:
    ADR-002 — MQTT topic conventions.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from cosalette._mqtt import MessageCallback

logger = logging.getLogger(__name__)

_ROOT_WORKER_KEY = "<root>"


@dataclass
class _Entity:
    """Per-entity dispatch state: handler + dedicated FIFO queue."""

    name: str
    handler: MessageCallback
    queue: asyncio.Queue[tuple[str, str]]
    is_root: bool


class TopicRouter:
    """Routes MQTT command messages to per-device handlers.

    Each registered entity (named device or root) has a dedicated
    asyncio worker task draining its queue FIFO. Entities run
    concurrently; ordering is preserved within each entity.

    Parses ``{prefix}/{device}/set`` topics, extracts device names,
    and dispatches to registered handlers.  Also supports a single
    root handler for the ``{prefix}/set`` topic (unnamed devices).

    Topic convention (ADR-002)::

        {prefix}/{device}/set    → command topic (subscribed, routed here)
        {prefix}/set             → root device command topic
        {prefix}/{device}/state  → state topic (published, not routed)

    See Also:
        ADR-002 — MQTT topic conventions.
    """

    def __init__(self, *, topic_prefix: str) -> None:
        self._topic_prefix = topic_prefix
        self._prefix = topic_prefix + "/"
        self._root_topic = topic_prefix + "/set"
        self._handlers: dict[str, _Entity] = {}
        self._handler_prefixes: dict[str, str] = {}
        self._root_entity: _Entity | None = None
        self._worker_tasks: dict[str, asyncio.Task[None]] = {}

    def register(
        self,
        device_name: str,
        handler: MessageCallback,
        *,
        is_root: bool = False,
    ) -> None:
        """Register a command handler for a device.

        When *is_root* is True, registers the handler for the
        ``{prefix}/set`` topic instead of ``{prefix}/{device}/set``.

        Raises:
            ValueError: If a handler is already registered for *device_name*
                or if a root handler is already registered.
        """
        if is_root:
            if self._root_entity is not None:
                msg = "Root handler already registered"
                raise ValueError(msg)
            self._root_entity = _Entity(
                name="<root>",
                handler=handler,
                queue=asyncio.Queue(),
                is_root=True,
            )
        else:
            if device_name in self._handlers:
                msg = f"Handler already registered for device '{device_name}'"
                raise ValueError(msg)
            entity = _Entity(
                name=device_name,
                handler=handler,
                queue=asyncio.Queue(),
                is_root=False,
            )
            self._handlers[device_name] = entity
            self._handler_prefixes[f"{device_name}/"] = device_name

    @property
    def _root_handler(self) -> MessageCallback | None:
        """Return the root handler callable, or None if not registered."""
        return self._root_entity.handler if self._root_entity is not None else None

    async def route(self, topic: str, payload: str) -> None:
        """Enqueue an inbound MQTT message for the matched entity's worker.

        Returns immediately; the entity's dedicated worker task handles
        delivery. Workers are started lazily on first message.

        Checks for root device match (``{prefix}/set``) first, then
        falls back to extracting a device name from
        ``{prefix}/{device}/set``.

        Silently ignores:
        - Topics that don't match either pattern
        - Devices with no registered handler (logs WARNING)
        """
        # Check for root device match: {prefix}/set
        if topic == self._root_topic:
            if self._root_entity is not None:
                self._root_entity.queue.put_nowait((topic, payload))
                self._ensure_worker(self._root_entity)
            else:
                logger.warning("No root handler registered (topic: %r)", topic)
            return

        result = self._extract_device(topic)
        if result is None:
            return

        device, _sub_topic = result
        entity = self._handlers.get(device)
        if entity is None:
            logger.warning(
                "No handler registered for device %r (topic: %r)",
                device,
                topic,
            )
            return

        entity.queue.put_nowait((topic, payload))
        self._ensure_worker(entity)

    def _ensure_worker(self, entity: _Entity) -> None:
        """Start a worker task for *entity* if none is currently running."""
        key = _ROOT_WORKER_KEY if entity.is_root else entity.name
        task = self._worker_tasks.get(key)
        if task is None or task.done():
            self._worker_tasks[key] = asyncio.create_task(
                self._run_worker(entity),
                name=f"cmd-dispatch:{entity.name}",
            )

    async def _run_worker(self, entity: _Entity) -> None:
        """Drain entity.queue FIFO, calling the handler for each message."""
        q = entity.queue
        while True:
            topic, payload = await q.get()
            try:
                await entity.handler(topic, payload)
            except asyncio.CancelledError:
                q.task_done()
                raise
            except Exception:
                logger.exception("Command dispatch error for entity %r", entity.name)
                q.task_done()
            else:
                q.task_done()

    async def wait_idle(self) -> None:
        """Wait until all queued command messages have been processed."""
        queues: list[asyncio.Queue[tuple[str, str]]] = [
            e.queue for e in self._handlers.values()
        ]
        if self._root_entity is not None:
            queues.append(self._root_entity.queue)
        if queues:
            await asyncio.gather(*(q.join() for q in queues))

    async def aclose(self) -> None:
        """Cancel all worker tasks and wait for them to stop. Idempotent."""
        tasks = list(self._worker_tasks.values())
        self._worker_tasks.clear()
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _extract_device(self, topic: str) -> tuple[str, str | None] | None:
        """Extract device name and optional sub-topic from topic.

        Dispatches to focused helpers; see each helper for details.

        Returns:
            ``(device, sub_topic)`` on success; ``None`` if the topic
            shape cannot be matched.
        """
        middle = self._extract_topic_middle(topic)
        if middle is None:
            return None
        result = self._match_registered_device(middle)
        if result is not None:
            return result
        return self._parse_unregistered_topic(middle)

    def _extract_topic_middle(self, topic: str) -> str | None:
        """Strip ``{prefix}/`` and ``/set``; return the middle segment or None."""
        if not (topic.startswith(self._prefix) and topic.endswith("/set")):
            return None
        middle = topic[len(self._prefix) : -4]  # len("/set") == 4
        return middle if middle else None

    def _match_registered_device(self, middle: str) -> tuple[str, str | None] | None:
        """Match *middle* against registered device names.

        Resolution order:

        1. Exact registered name match → ``(device, None)``.
        2. Longest registered prefix (slash positions scanned right-to-left);
           returns ``(device, sub_topic)`` only when *sub_topic* is non-empty
           and contains no slash (one-level sub-topic rule).
        """
        if middle in self._handlers:
            return (middle, None)
        pos = len(middle) - 1
        while pos > 0:
            idx = middle.rfind("/", 0, pos + 1)
            if idx == -1:
                break
            device = self._handler_prefixes.get(middle[: idx + 1])
            if device is not None:
                sub_topic = middle[idx + 1 :]
                if sub_topic and "/" not in sub_topic:
                    return (device, sub_topic)
            pos = idx - 1
        return None

    def _parse_unregistered_topic(self, middle: str) -> tuple[str, str | None] | None:
        """Syntactic fallback so ``route()`` can warn about unknown devices.

        Accepts one-segment topics → ``(segment, None)`` and two-segment
        topics → ``(seg0, seg1)``; everything else returns ``None``.
        """
        parts = middle.split("/")
        if len(parts) == 1 and parts[0]:
            return (parts[0], None)
        if len(parts) == 2 and parts[0] and parts[1]:
            return (parts[0], parts[1])
        return None

    @property
    def subscriptions(self) -> list[str]:
        """Return topics that should be subscribed to for all registered devices.

        For each non-root device, subscribes to both:
        - ``{prefix}/{device}/set`` — root commands
        - ``{prefix}/{device}/+/set`` — sub-topic commands (wildcard)
        """
        subs: list[str] = []
        for device in self._handlers:
            subs.append(f"{self._topic_prefix}/{device}/set")
            subs.append(f"{self._topic_prefix}/{device}/+/set")
        if self._root_entity is not None:
            subs.append(f"{self._topic_prefix}/set")
        return subs
