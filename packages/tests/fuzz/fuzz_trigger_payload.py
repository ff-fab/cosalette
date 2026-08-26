"""Fuzz TriggerPayload.from_mqtt — triggerable-telemetry payload parsing.

Oracle: from_mqtt has a no-raise contract — invalid input degrades to
``data=None``. Deep nesting within the inbound size cap must surface as
"not valid JSON" instead of an unstructured RecursionError (F-DP9,
CWE-674). Any exception at all is a crash.
"""

from _runner import instrument_imports, run

with instrument_imports():
    from cosalette._runners._trigger import TriggerPayload  # noqa: E402


def fuzz_trigger_payload(data: bytes) -> None:
    """Parse an arbitrary payload; from_mqtt must never raise."""
    TriggerPayload.from_mqtt(data.decode("utf-8", "surrogateescape"))


run(fuzz_trigger_payload)
