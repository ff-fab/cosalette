"""Help content dispatcher for cosalette AI guidance.

Delegates to :mod:`_help_extra` and :mod:`_help_core` to keep individual
module sizes manageable.
"""

from __future__ import annotations

from cosalette._ai_content._help_core import get_core_help
from cosalette._ai_content._help_extra import get_extra_help
from cosalette._ai_content._meta import AVAILABLE_TOPICS

# Backward-compatible alias used by _ai_content/__init__.py
_get_extra_help = get_extra_help

__all__ = ["get_help_content", "_get_extra_help"]


def get_help_content(topic: str) -> str:
    """Get cosalette framework guidance for a specific topic.

    Args:
        topic: Help topic (telemetry, testing, configuration, architecture, …)

    Returns:
        Curated help content for the topic

    Raises:
        ValueError: If topic is not recognised
    """
    extra = get_extra_help(topic)
    if extra is not None:
        return extra
    core = get_core_help(topic)
    if core is not None:
        return core
    available = ", ".join(AVAILABLE_TOPICS)
    raise ValueError(f"Unknown topic: {topic}. Available: {available}")
