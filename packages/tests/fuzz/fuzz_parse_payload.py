"""Fuzz cosalette._runners._contracts.parse_payload — typed-contract gate.

Oracle: for any payload string and any annotation, the only sanctioned
failure is :class:`PayloadValidationError` (default-deny redaction path).
Any other exception escaping the inbound validation gate is a crash
(e.g. a RecursionError regression of the F-DP4 hardening, CWE-674).
"""

import contextlib

from _runner import FuzzedDataProvider, instrument_imports, run

with instrument_imports():
    from pydantic import BaseModel  # noqa: E402

    from cosalette._runners._contracts import (  # noqa: E402
        PayloadValidationError,
        parse_payload,
    )


class _FuzzModel(BaseModel):
    """Representative handler payload contract."""

    name: str
    level: int = 0


_ANNOTATIONS: list[object] = [
    int,
    str,
    bool,
    float,
    "int | None",
    list[int],
    dict[str, float],
    _FuzzModel,
]


def fuzz_parse_payload(data: bytes) -> None:
    """Rotate annotations and payloads; only PayloadValidationError may escape."""
    provider = FuzzedDataProvider(data)
    annotation = provider.PickValueInList(_ANNOTATIONS)
    raw = provider.ConsumeBytes(provider.remaining_bytes()).decode("utf-8", "replace")
    with contextlib.suppress(PayloadValidationError):
        parse_payload(raw, annotation, param="value", handler="fuzz.harness")


run(fuzz_parse_payload)
