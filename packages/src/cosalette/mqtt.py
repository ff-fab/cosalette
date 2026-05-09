"""MQTT binding markers and value types for cosalette handlers.

Provides PEP 593 :class:`~typing.Annotated` markers for binding MQTT
message context — payload, topic, and full message — into typed handler
parameters.

See Also:
    ADR-046 — Typed Handler Contract Validation.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Message:
    """Inbound MQTT message data.

    Injected when a handler parameter is annotated with :class:`Message`
    as its type.  Carries the raw topic string and the raw payload string.

    Example::

        @app.command("device/set")
        async def handle(msg: Message) -> None:
            print(msg.topic, msg.payload)

    """

    topic: str
    payload: str


class _PayloadMarker:
    """PEP 593 Annotated metadata for payload binding.

    Created by :func:`Payload`.
    """

    __slots__ = ("raw",)

    def __init__(self, *, raw: bool = False) -> None:
        self.raw = raw

    def __repr__(self) -> str:
        return f"Payload(raw={self.raw!r})"


class _TopicMarker:
    """PEP 593 Annotated metadata for full topic string binding.

    Created by :func:`Topic`.
    """

    __slots__ = ()

    def __repr__(self) -> str:
        return "Topic()"


def Payload(*, raw: bool = False) -> _PayloadMarker:
    """Bind a handler parameter to the inbound MQTT payload.

    When *raw* is ``False`` (default), the payload string is JSON-decoded
    and validated against the annotated type via Pydantic ``TypeAdapter``.
    When *raw* is ``True``, the raw payload string is passed through without
    JSON parsing or validation.

    Usage::

        @app.command("device/set")
        async def handle(
            cmd: Annotated[SetpointCmd, Payload()],
        ) -> None:
            ...

    Args:
        raw: Pass the raw payload string without parsing when ``True``.

    Returns:
        A marker suitable for ``Annotated[T, Payload()]``.

    """
    return _PayloadMarker(raw=raw)


def Topic() -> _TopicMarker:
    """Bind a handler parameter to the full inbound MQTT topic string.

    Usage::

        @app.command("devices/{id}/set")
        async def handle(
            full_topic: Annotated[str, Topic()],
        ) -> None:
            ...

    Returns:
        A marker suitable for ``Annotated[str, Topic()]``.

    """
    return _TopicMarker()
