"""Fuzz the schema-monitor inbound path — _decode_payload/_dispatch_message.

Oracle: the standalone monitor loop must survive arbitrary broker-facing
bytes: UTF-8 decoding failures degrade to ``None`` (never raise), and
schema/status payloads that are invalid, deeply nested (F-DP9,
CWE-674), or non-object JSON are skipped (never raise). Any exception
would kill the fleet-compliance monitor loop.
"""

from _runner import instrument_imports, run

with instrument_imports():
    from cosalette._schema._monitor import (  # noqa: E402
        NetworkComplianceMonitor,
        _decode_payload,
        _dispatch_message,
    )

_MONITOR = NetworkComplianceMonitor(frozenset({"app1"}))


def fuzz_monitor_dispatch(data: bytes) -> None:
    """Split input into topic/payload at the first NUL and dispatch."""
    _decode_payload(data[:64], topic="fuzz/topic")  # decode oracle
    topic_nul, _, payload_raw = data.partition(b"\x00")
    topic = topic_nul.decode("utf-8", "surrogateescape") or "app1/schema/status"
    payload = payload_raw.decode("utf-8", "surrogateescape")
    _dispatch_message(_MONITOR, topic, payload)


run(fuzz_monitor_dispatch)
