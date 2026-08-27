"""Fuzz TopicRouter's pure topic-matching helpers — inbound routing gate.

Oracle: arbitrary topic strings (any bytes, surrogate-escaped) must route
to a tuple result or ``None`` without raising. The exact-match /
longest-prefix scan (``rfind`` loop) must stay total over all inputs.
"""

import contextlib

from _runner import instrument_imports, run

with instrument_imports():
    from cosalette._mqtt._router import TopicRouter  # noqa: E402


async def _noop(topic: str, payload: str) -> None:
    """Registered callback — never invoked by the pure helpers."""


_ROUTER = TopicRouter(topic_prefix="home")
for _name in ("livingroom", "kitchen", "kitchen/oven", "a", "x" * 40):
    with contextlib.suppress(ValueError):
        _ROUTER.register(_name, _noop)  # duplicates in the fixed set — skip


def fuzz_router(data: bytes) -> None:
    """Route an arbitrary topic through the full extraction chain."""
    topic = data.decode("utf-8", "surrogateescape")
    _ROUTER._extract_device(topic)  # private: no public equivalent covers this path


run(fuzz_router)
