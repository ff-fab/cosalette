"""Canonical instance identities shared by settings and consumer generators."""

from __future__ import annotations

import re

_INSTANCE_ID_RE = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*")


def validate_instance_id(value: str) -> str:
    """Accept empty fallback or a canonical identity unchanged by slugification."""
    if value and _INSTANCE_ID_RE.fullmatch(value) is None:
        msg = (
            "instance_id must start with a lowercase letter and contain lowercase "
            "letters, digits, and single underscores between nonempty segments "
            f"(got {value!r})"
        )
        raise ValueError(msg)
    return value
