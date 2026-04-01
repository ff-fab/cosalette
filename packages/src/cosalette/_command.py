"""Command data type for inbound device commands.

Represents an MQTT command received by the framework. Frozen and slotted
for immutability and memory efficiency.

See Also:
    ADR-025 — Command channel and sub-topic routing.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Command:
    """An inbound MQTT command.

    Attributes:
        topic: Full MQTT topic the command arrived on.
        payload: Raw payload string.
        sub_topic: Sub-topic segment, or None for root commands.
        timestamp: Monotonic timestamp at receipt (seconds).
    """

    topic: str
    payload: str
    sub_topic: str | None = None
    timestamp: float = 0.0
