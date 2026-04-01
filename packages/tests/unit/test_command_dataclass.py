"""Unit tests for cosalette._command — Command dataclass.

Test Techniques Used:
    - Specification-based Testing: Field defaults, required fields,
      immutability, equality, hashability
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from cosalette._command import Command

pytestmark = pytest.mark.unit


class TestCommand:
    """Tests for the Command frozen dataclass."""

    def test_command_stores_required_fields(self) -> None:
        """Construct with topic+payload, verify fields stored.

        Technique: Specification-based Testing — required field storage.
        """
        cmd = Command(topic="myapp/blind/cmd", payload='{"action":"open"}')

        assert cmd.topic == "myapp/blind/cmd"
        assert cmd.payload == '{"action":"open"}'

    def test_command_default_sub_topic_is_none(self) -> None:
        """sub_topic defaults to None when not provided.

        Technique: Specification-based Testing — default value.
        """
        cmd = Command(topic="t", payload="p")

        assert cmd.sub_topic is None

    def test_command_default_timestamp_is_zero(self) -> None:
        """timestamp defaults to 0.0 when not provided.

        Technique: Specification-based Testing — default value.
        """
        cmd = Command(topic="t", payload="p")

        assert cmd.timestamp == 0.0

    def test_command_frozen_rejects_attribute_mutation(self) -> None:
        """Frozen dataclass rejects attribute assignment.

        Technique: Specification-based Testing — immutability guarantee.
        """
        cmd = Command(topic="t", payload="p")

        with pytest.raises(FrozenInstanceError):
            cmd.topic = "other"  # type: ignore[misc]

    def test_command_equality_matches_identical_fields(self) -> None:
        """Two Commands with identical fields compare equal.

        Technique: Specification-based Testing — value equality.
        """
        a = Command(topic="t", payload="p", sub_topic="s", timestamp=1.0)
        b = Command(topic="t", payload="p", sub_topic="s", timestamp=1.0)

        assert a == b

    def test_command_hashable_in_set(self) -> None:
        """Frozen dataclass is hashable and can be stored in a set.

        Technique: Specification-based Testing — hashability from frozen=True.
        """
        cmd = Command(topic="t", payload="p")

        s = {cmd}

        assert cmd in s

    def test_command_with_all_fields(self) -> None:
        """Construct with all four fields, verify all stored.

        Technique: Specification-based Testing — full construction.
        """
        cmd = Command(
            topic="myapp/blind/cmd/position",
            payload='{"value":50}',
            sub_topic="position",
            timestamp=123.456,
        )

        assert cmd.topic == "myapp/blind/cmd/position"
        assert cmd.payload == '{"value":50}'
        assert cmd.sub_topic == "position"
        assert cmd.timestamp == 123.456
