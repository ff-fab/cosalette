"""Fuzz cosalette._json.loads — the framework-wide JSON choke-point.

Oracle: arbitrary bytes must either parse or raise ``JSONDecodeError``
(orjson enforces a C-level nesting cap, so deep payloads stay structured
instead of raising RecursionError — the F-DP4 class, CWE-674). Any other
exception is a crash.
"""

import contextlib

from _runner import instrument_imports, run

with instrument_imports():
    from cosalette._json import JSONDecodeError, loads  # noqa: E402


def fuzz_json(data: bytes) -> None:
    """Parse arbitrary bytes; only JSONDecodeError is acceptable."""
    with contextlib.suppress(JSONDecodeError):
        loads(data)


run(fuzz_json)
