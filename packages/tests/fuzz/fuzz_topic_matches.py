"""Fuzz cosalette._schema._topic_matches — address-template matching.

Oracle: matching arbitrary topic strings against fixed address templates
must return a bool without raising. ``re.escape`` + placeholder rewrite +
``fullmatch`` must stay total (and non-hanging) over all inputs.
"""

from _runner import instrument_imports, run

with instrument_imports():
    from cosalette._schema import _topic_matches  # noqa: E402

_TEMPLATES = ("status", "{deviceName}/set", "home/{room}/{sensor}", "{a}/{b}/{c}")


def fuzz_topic_matches(data: bytes) -> None:
    """Match the input against every fixed template."""
    topic = data.decode("utf-8", "surrogateescape")
    for template in _TEMPLATES:
        _topic_matches(template, topic)


run(fuzz_topic_matches)
